"""Deterministic backtest: market -> book -> strategy -> router -> simulated venue -> ledger.

Per step t, in order: (1) deliver quotes (subject to the feed fault schedule) to the Book and to
the venues' own state; (2) strategy decides once; an order is sent; (3) venue processes orders
arriving at t; (4) acks due at t are delivered; (5) ack timeouts -> UNKNOWN; (6) reconciliations
due at t; (7) mark equity. Contract: docs/contracts/Q1-quant.md.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from .book import Book
from .ledger import Fill, Ledger
from .market_sim import MarketConfig, generate
from .quant import PolicyConfig, Strategy, imbalance_bp, reference_signal
from .router import Side, route

REFERENCE = "A"


@dataclass(frozen=True, slots=True)
class ExecConfig:
    exec_delay_steps: int = 0
    ack_delay_steps: int = 0
    ack_timeout_steps: int = 5
    reconcile_after_steps: int = 10
    fee_per_unit: int = 0
    drop_ack_pct: int = 0            # fault: percent of acks lost (fault RNG)
    outage_every: int = 0            # fault: reference feed silent for outage_len of every N steps
    outage_len: int = 0
    fault_seed: int = 0
    stale_after_ns: int = 50_000_000
    trade: bool = True               # False = no-trade baseline


@dataclass(frozen=True, slots=True)
class Order:
    order_id: str
    venue_id: str
    side: Side
    qty: int
    limit_ticks: int
    epoch: int
    decision_step: int


class SimVenue:
    """Venue-side state: current quote, remaining displayed size, idempotent order store."""

    def __init__(self, venue_id: str, fee_per_unit: int):
        self.venue_id = venue_id
        self.fee_per_unit = fee_per_unit
        self.quote = None
        self.remaining = {Side.BUY: 0, Side.SELL: 0}
        self.epoch = 1
        self.orders: dict[str, tuple[Order, dict]] = {}

    def on_quote(self, q) -> None:
        self.quote = q
        self.remaining = {Side.BUY: q.ask_quantity, Side.SELL: q.bid_quantity}   # replenish

    def submit(self, o: Order, step: int) -> dict:
        prior = self.orders.get(o.order_id)
        if prior is not None:
            if prior[0] == o:
                return prior[1]
            return {"status": "REJECTED", "reason": "conflicting_order_id"}
        q = self.quote
        price = q.ask_ticks if o.side is Side.BUY else q.bid_ticks
        if o.epoch != self.epoch:
            res = {"status": "REJECTED", "reason": "session"}
        elif (o.side is Side.BUY and price > o.limit_ticks) or (o.side is Side.SELL and price < o.limit_ticks):
            res = {"status": "REJECTED", "reason": "price_moved"}
        elif self.remaining[o.side] < o.qty:
            res = {"status": "REJECTED", "reason": "insufficient_quantity"}
        else:
            self.remaining[o.side] -= o.qty
            signed = o.qty if o.side is Side.BUY else -o.qty
            res = {"status": "FILLED", "fill": Fill(f"f-{o.order_id}", o.order_id, self.venue_id, signed,
                                                    price, self.fee_per_unit * o.qty, o.decision_step, step)}
        self.orders[o.order_id] = (o, res)
        return res

    def status(self, order_id: str):
        prior = self.orders.get(order_id)
        return None if prior is None else prior[1]


@dataclass
class Result:
    decisions: dict = field(default_factory=dict)
    fills: int = 0
    turnover: int = 0
    rejections: dict = field(default_factory=dict)
    unknown: int = 0
    reconciled: int = 0
    duplicate_fills: int = 0
    max_abs_position: int = 0
    marks_missing: int = 0
    max_drawdown_ticks: int = 0
    spread_cost_halfticks: int = 0
    hit: int = 0
    hit_n: int = 0
    orders_sent: int = 0
    final: dict = field(default_factory=dict)


def run(mcfg: MarketConfig, pcfg: PolicyConfig, xcfg: ExecConfig) -> Result:
    book = Book(mcfg.symbol, ["A", "B"], xcfg.stale_after_ns)
    epochs = {v: book.on_connect(v) for v in ("A", "B")}
    venues = {v: SimVenue(v, xcfg.fee_per_unit) for v in ("A", "B")}
    strat = Strategy(pcfg)
    led = Ledger(pcfg.q_max)
    frng = random.Random(xcfg.fault_seed)
    res = Result()
    in_flight: dict[int, list[Order]] = {}     # arrive step -> orders
    acks: dict[int, list[tuple[Order, dict]]] = {}
    sent_at: dict[str, int] = {}
    reconcile: dict[int, list[Order]] = {}
    orders: dict[str, Order] = {}
    peak = 0
    prev = None
    last = None

    def book_fill(o: Order, outcome: dict, step: int) -> None:
        if outcome["status"] == "FILLED":
            f = outcome["fill"]
            if led.apply(f) == "duplicate":
                res.duplicate_fills += 1
                return
            res.fills += 1
            res.turnover += abs(f.signed_qty)
            res.spread_cost_halfticks += abs(2 * f.price_ticks - (2 * lat_by_step[step] + mcfg.spread_ticks)) * abs(f.signed_qty)
            strat.on_resolved(o.order_id, f.signed_qty, step)
        else:
            res.rejections[outcome["reason"]] = res.rejections.get(outcome["reason"], 0) + 1
            strat.on_resolved(o.order_id, 0, step)

    lat_by_step = {}
    for st in generate(mcfg):
        t, now = st.t, st.now_ns
        lat_by_step[t] = st.latent_price
        qa, qb = st.quotes
        # (1) venues always see their own quotes; the feed fault only affects the aggregator book.
        venues["A"].on_quote(qa)
        venues["B"].on_quote(qb)
        outage = xcfg.outage_every > 0 and t % xcfg.outage_every < xcfg.outage_len
        if not outage:
            book.on_quote("A", epochs["A"], qa, now)
        book.on_quote("B", epochs["B"], qb, now)
        # evaluator-only prediction statistic (uses the next latent move, never shown to strategy)
        if prev is not None:
            imb_prev, p_prev = prev
            move = st.latent_price - p_prev
            if move and abs(imb_prev) > pcfg.theta_bp:
                res.hit_n += 1
                res.hit += (imb_prev > 0) == (move > 0)
        prev = (imbalance_bp(qa.bid_quantity, qa.ask_quantity), st.latent_price)
        # (2) decide
        if xcfg.trade:
            reason, intent = strat.decide(reference_signal(book, REFERENCE, now), t)
            res.decisions[reason] = res.decisions.get(reason, 0) + 1
            if intent is not None:
                d = route(book, intent.side, intent.qty, now)
                if d.venue_id is None:
                    res.rejections["no_route"] = res.rejections.get("no_route", 0) + 1
                else:
                    o = Order(f"o{t}", d.venue_id, intent.side, intent.qty, d.price_ticks,
                              d.connection_epoch, t)
                    orders[o.order_id] = o
                    res.orders_sent += 1
                    strat.on_sent(o.order_id)
                    sent_at[o.order_id] = t
                    in_flight.setdefault(t + xcfg.exec_delay_steps, []).append(o)
        # (3) venue processing
        for o in in_flight.pop(t, []):
            outcome = venues[o.venue_id].submit(o, t)
            if frng.randrange(100) >= xcfg.drop_ack_pct:
                acks.setdefault(t + xcfg.ack_delay_steps, []).append((o, outcome))
        # (4) acks
        for o, outcome in acks.pop(t, []):
            if o.order_id in sent_at or o.order_id in strat.halted_unknown:
                sent_at.pop(o.order_id, None)
                book_fill(o, outcome, t)
        # (5) timeouts -> UNKNOWN (no resend, no invented fill)
        for oid, s in list(sent_at.items()):
            if t - s >= xcfg.ack_timeout_steps:
                del sent_at[oid]
                strat.on_unknown(oid)
                res.unknown += 1
                reconcile.setdefault(t + xcfg.reconcile_after_steps, []).append(orders[oid])
        # (6) reconciliation by client order id
        for o in reconcile.pop(t, []):
            if o.order_id in strat.halted_unknown:
                outcome = venues[o.venue_id].status(o.order_id)
                if outcome is not None:
                    res.reconciled += 1
                    book_fill(o, outcome, t)
        # (7) mark at reference venue's executable price, only if FRESH
        res.max_abs_position = max(res.max_abs_position, abs(led.position))
        if book.status("A", now).value == "fresh":
            mark = led.mark_price(qa.bid_ticks, qa.ask_ticks)
            eq = led.equity_ticks(mark)
            peak = max(peak, eq)
            res.max_drawdown_ticks = max(res.max_drawdown_ticks, peak - eq)
            last = (qa, eq)
        else:
            res.marks_missing += 1
    # End of run: report open inventory and unrealized P&L; hypothetical liquidation is labelled.
    qa = last[0] if last else None
    mark = led.mark_price(qa.bid_ticks, qa.ask_ticks) if qa else None
    eq = led.equity_ticks(mark)
    liq_fee = xcfg.fee_per_unit * abs(led.position)
    # Venue record vs ledger after reconciliation: every venue fill must be booked exactly once,
    # except fills whose outcome is still UNKNOWN at the end (reported, never invented).
    venue_fills = {r["fill"].fill_id: r["fill"] for v in venues.values()
                   for _, r in v.orders.values() if r["status"] == "FILLED"}
    unbooked = [f for fid, f in venue_fills.items() if fid not in led.fills]
    phantom = [fid for fid in led.fills if fid not in venue_fills]
    mismatched = [fid for fid, f in led.fills.items() if fid in venue_fills and venue_fills[fid] != f]
    unresolved_ids = set(strat.halted_unknown) | ({strat.outstanding} if strat.outstanding else set())
    res.reconciliation = {
        "venue_fills": len(venue_fills), "ledger_fills": len(led.fills),
        "unbooked_venue_fills": len(unbooked),
        "unbooked_all_unresolved": all(f.order_id in unresolved_ids for f in unbooked),
        "unbooked_signed_qty": sum(f.signed_qty for f in unbooked),
        "ledger_fills_not_at_venue": len(phantom), "mismatched": len(mismatched)}
    res.final = {
        "position": led.position, "cash_ticks": led.cash_ticks, "fees_ticks": led.fees_ticks,
        "realized_gross_ticks": led.realized_gross_ticks,
        "unrealized_ticks": None if mark is None else led.unrealized_ticks(mark),
        "mark_ticks": mark, "mark_source": "last fresh A executable price" if mark is not None else "MISSING",
        "equity_marked_ticks": eq,
        "net_if_liquidated_ticks": None if eq is None else eq - liq_fee,
        "gross_if_liquidated_ticks": None if eq is None else eq + led.fees_ticks,
        "halted_unknown_at_end": len(strat.halted_unknown),
        "outstanding_at_end": strat.outstanding is not None,
        "liquidation_fee_ticks": liq_fee,
    }
    res.ledger = led
    return res
