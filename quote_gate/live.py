"""Networked strategy runner inside the aggregator process (loopback slice; contract O1).

Same Strategy, router and Ledger as the deterministic backtest; orders travel on the existing
persistent venue connections and are demultiplexed by the aggregator's single feed reader.

Cadence: one decision per ACCEPTED reference-venue quote (event index = that quote's sequence).
Order lifecycle: SENT -> (exec) RESOLVED | (ack timeout / disconnect) UNKNOWN -> (status) RESOLVED
or still UNKNOWN. UNKNOWN orders halt trading, are never resent, and are only queried.

Measurements (aggregator host monotonic clock only):
  exec_rtt_ns      order frame written -> matching exec frame read (orders resolved by exec in time)
  reconcile_ns     order frame written -> resolution via status or late exec (UNKNOWN orders)
These are distinct from route-query RTT and feed ping/pong RTT.

  python3 -m quote_gate.live --seed 101 --steps 3000 --rate 500
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import dataclass, field

from . import protocol
from .aggregator import Aggregator
from .book import Book
from .ledger import Fill, Ledger, LedgerError
from .market_sim import MarketConfig
from .metrics import Samples
from .quant import PolicyConfig, Strategy, reference_signal
from .router import Side, route
from .venue import Venue

ACK_TIMEOUT_S = 0.2
STATUS_INTERVAL_S = 0.1
MAX_STATUS_QUERIES = 50          # per UNKNOWN order; then left UNKNOWN and reported


@dataclass
class Pending:
    oid: str
    venue_id: str
    instance: str
    session: int
    side: Side
    qty: int
    limit: int
    decision_ref: int
    sent_ns: int
    state: str = "SENT"          # SENT | UNKNOWN | RESOLVED | LOST_RECORD
    queries: int = 0
    timer: asyncio.Task | None = None


@dataclass
class Counters:
    decisions: dict = field(default_factory=dict)
    sent: int = 0
    filled: int = 0
    rejected: dict = field(default_factory=dict)
    timeouts: int = 0
    unknown_disconnect: int = 0
    resolved_by_exec: int = 0
    resolved_by_status: int = 0
    resolved_not_received: int = 0
    lost_record: int = 0
    duplicate_exec: int = 0
    invalid_response: dict = field(default_factory=dict)
    no_route: int = 0
    not_connected: int = 0
    status_queries: int = 0


class LiveRunner:
    def __init__(self, agg: Aggregator, policy: PolicyConfig, reference: str = "A",
                 exec_venues=("A",), run_tag: str = "run", ack_timeout_s: float = ACK_TIMEOUT_S,
                 status_interval_s: float = STATUS_INTERVAL_S):
        self.agg, self.book = agg, agg.book
        self.policy = policy
        self.reference = reference
        self.allowed = frozenset(exec_venues)
        self.run_tag = run_tag
        self.ack_timeout_s, self.status_interval_s = ack_timeout_s, status_interval_s
        self.strategy = Strategy(policy)
        self.ledger = Ledger(policy.q_max)
        self.pending: dict[str, Pending] = {}
        self.resolved: dict[str, dict] = {}       # oid -> outcome applied
        self.c = Counters()
        self.exec_rtt_ns = Samples(20_000)
        self.reconcile_ns = Samples(5_000)
        self.decision_log: list[tuple[int, str]] = []   # (event index, reason) bounded below
        self.max_decision_log = 200_000
        self._n = 0
        self.last_event = 0             # reference quote sequence (resets per venue session)
        self.decision_step = 0          # monotonic across sessions; used for strategy timing
        agg.order_handler = self.on_frame
        agg.quote_listener = self.on_quote
        agg.disconnect_listener = self.on_disconnect

    # ---- decisions ----
    def on_quote(self, vid, q, now_ns) -> None:
        if vid != self.reference:
            return
        self.last_event = q.sequence
        self.decision_step += 1
        reason, intent = self.strategy.decide(reference_signal(self.book, self.reference, now_ns),
                                              self.decision_step)
        self.c.decisions[reason] = self.c.decisions.get(reason, 0) + 1
        if len(self.decision_log) < self.max_decision_log:
            self.decision_log.append((q.sequence, reason))
        if intent is None:
            return
        d = route(self.book, intent.side, intent.qty, now_ns, allowed=self.allowed)
        if d.venue_id is None:
            self.c.no_route += 1            # nothing sent; position unchanged
            return
        conn = self.agg.conn.get(d.venue_id)
        if conn is None:
            self.c.not_connected += 1
            return
        self._n += 1
        oid = f"{self.run_tag}-{self._n}"
        p = Pending(oid, d.venue_id, conn["instance"], conn["session"], intent.side, intent.qty,
                    d.price_ticks, q.sequence, time.monotonic_ns())
        frame = {"type": "order", "client_order_id": oid, "symbol": self.book.symbol,
                 "venue_id": d.venue_id, "instance": p.instance, "session": p.session,
                 "side": intent.side.value, "quantity": intent.qty, "limit_ticks": d.price_ticks}
        self.pending[oid] = p
        self.strategy.on_sent(oid)
        p.sent_ns = time.monotonic_ns()
        conn["writer"].write(protocol.encode(frame))      # from here on the order may have executed
        self.c.sent += 1
        p.timer = asyncio.get_running_loop().create_task(self._ack_timer(p))

    # ---- responses ----
    def on_frame(self, vid, obj) -> None:
        oid = obj.get("client_order_id")
        kind = obj.get("type")
        if oid in self.resolved:
            if kind == "exec":
                self.c.duplicate_exec += 1
            return
        p = self.pending.get(oid)
        if p is None:
            return self._invalid("unknown_order_id")
        if obj.get("venue_id") != p.venue_id or obj.get("instance") != p.instance:
            if kind == "status" and obj.get("venue_id") == p.venue_id and obj.get("found") is False:
                return self._lost_record(p)       # different instance: venue restarted, record lost
            return self._invalid("venue_or_instance_mismatch")
        if kind == "exec":
            if obj.get("session") != p.session:
                return self._invalid("session_mismatch")
            self._apply(p, obj, "exec")
        elif kind == "status":
            if not obj.get("found"):
                # Same instance, not found: records precede acks and unreleased records are never
                # evicted, so the order never arrived. Resolved as not executed.
                self.c.resolved_not_received += 1
                self._resolve(p, 0, "not_received")
                return
            if obj.get("session") != p.session:
                return self._invalid("session_mismatch")
            self._apply(p, obj, "status")

    def _apply(self, p: Pending, obj, via: str) -> None:
        status = obj.get("status")
        if status == "FILLED":
            qty, price, fee = obj.get("quantity"), obj.get("fill_price_ticks"), obj.get("fee_ticks")
            if not (type(qty) is int and qty == p.qty and type(price) is int and type(fee) is int):
                return self._invalid("malformed_fill")
            signed = qty if p.side is Side.BUY else -qty
            f = Fill(f"{p.venue_id}:{p.instance}:{p.oid}", p.oid, p.venue_id, signed, price, fee,
                     p.decision_ref, self.last_event)
            try:
                if self.ledger.apply(f) == "duplicate":
                    self.c.duplicate_exec += 1
                    return
            except LedgerError:
                return self._invalid("ledger_rejected")
            self.c.filled += 1
            self._resolve(p, signed, via)
        elif status == "REJECTED":
            r = obj.get("reason") or "unspecified"
            self.c.rejected[r] = self.c.rejected.get(r, 0) + 1
            self._resolve(p, 0, via)
        else:
            self._invalid("bad_status")

    def _resolve(self, p: Pending, signed: int, via: str) -> None:
        elapsed = time.monotonic_ns() - p.sent_ns
        if p.state == "SENT" and via == "exec":
            self.c.resolved_by_exec += 1
            self.exec_rtt_ns.add(elapsed)
        else:
            if via == "status":
                self.c.resolved_by_status += 1
            self.reconcile_ns.add(elapsed)
        p.state = "RESOLVED"
        if p.timer is not None and p.timer is not asyncio.current_task():
            p.timer.cancel()
        del self.pending[p.oid]
        self.resolved[p.oid] = {"via": via, "signed_qty": signed}
        self.strategy.on_resolved(p.oid, signed, self.decision_step)
        conn = self.agg.conn.get(p.venue_id)
        if conn is not None and conn["instance"] == p.instance:
            conn["writer"].write(protocol.encode({"type": "release", "client_order_id": p.oid}))

    def _lost_record(self, p: Pending) -> None:
        if p.state != "LOST_RECORD":
            p.state = "LOST_RECORD"
            self.c.lost_record += 1
        if p.timer is not None and p.timer is not asyncio.current_task():
            p.timer.cancel()
        # stays in pending and in strategy.halted_unknown: trading remains halted

    def _invalid(self, why: str) -> None:
        self.c.invalid_response[why] = self.c.invalid_response.get(why, 0) + 1

    # ---- timeouts, disconnects, reconciliation ----
    def on_disconnect(self, vid, epoch) -> None:
        for p in list(self.pending.values()):
            if p.venue_id == vid and p.state == "SENT":
                self.c.unknown_disconnect += 1
                self._to_unknown(p)

    def _to_unknown(self, p: Pending) -> None:
        p.state = "UNKNOWN"
        self.strategy.on_unknown(p.oid)
        if p.timer is not None and p.timer is not asyncio.current_task():
            p.timer.cancel()
        p.timer = asyncio.get_running_loop().create_task(self._reconcile(p))

    async def _ack_timer(self, p: Pending) -> None:
        await asyncio.sleep(self.ack_timeout_s)
        if p.state == "SENT":
            self.c.timeouts += 1
            self._to_unknown(p)

    async def _reconcile(self, p: Pending) -> None:
        while p.state == "UNKNOWN" and p.queries < MAX_STATUS_QUERIES:
            await asyncio.sleep(self.status_interval_s)
            conn = self.agg.conn.get(p.venue_id)
            if conn is None or p.state != "UNKNOWN":
                continue
            p.queries += 1
            self.c.status_queries += 1
            conn["writer"].write(protocol.encode({"type": "order_status", "client_order_id": p.oid}))

    def unresolved(self) -> list[Pending]:
        return list(self.pending.values())

    def report(self) -> dict:
        c = self.c
        return {"decisions": c.decisions, "sent": c.sent, "filled": c.filled, "rejected": c.rejected,
                "timeouts": c.timeouts, "unknown_disconnect": c.unknown_disconnect,
                "resolved_by_exec": c.resolved_by_exec, "resolved_by_status": c.resolved_by_status,
                "resolved_not_received": c.resolved_not_received, "lost_record": c.lost_record,
                "duplicate_exec": c.duplicate_exec, "invalid_response": c.invalid_response,
                "no_route": c.no_route, "not_connected": c.not_connected,
                "status_queries": c.status_queries,
                "unresolved": [(p.oid, p.state) for p in self.pending.values()],
                "position": self.ledger.position, "cash_ticks": self.ledger.cash_ticks,
                "fees_ticks": self.ledger.fees_ticks,
                "exec_rtt_ns": self.exec_rtt_ns.summary(), "reconcile_ns": self.reconcile_ns.summary()}


def venue_reconciliation(runner: LiveRunner, venue: Venue) -> dict:
    """Evaluator-only: compare the strategy ledger with the venue's authoritative order store."""
    venue_fills = dict(venue.fill_log)        # evaluator-only full fill log (store may evict)
    led = {f.order_id: f for f in runner.ledger.fills.values() if f.venue_id == venue.venue_id}
    unresolved = {p.oid for p in runner.unresolved()}
    unbooked = [oid for oid in venue_fills if oid not in led]
    return {"venue_fills": len(venue_fills), "ledger_fills": len(led),
            "unbooked_venue_fills": len(unbooked),
            "unbooked_all_unresolved": all(oid in unresolved for oid in unbooked),
            "ledger_not_at_venue": sum(1 for oid in led if oid not in venue_fills),
            "venue_fill_log_dropped": venue.fill_log_dropped,
            "mismatched": sum(1 for oid, f in led.items() if oid in venue_fills and (
                f.price_ticks != venue_fills[oid]["fill_price_ticks"]
                or abs(f.signed_qty) != venue_fills[oid]["quantity"]
                or f.fee_ticks != venue_fills[oid]["fee_ticks"]))}


async def run_loopback(seed: int, steps: int, rate: float, fee: int = 1, policy=PolicyConfig(),
                       settle_s: float = 1.0) -> dict:
    """Venue A (market_sim stream) + aggregator + runner on loopback TCP in one event loop."""
    m = MarketConfig(seed=seed, steps=steps, beta_pct=50)
    va = Venue("A", rate_hz=rate, market=m, fee_per_unit=fee)
    port = await va.start()
    book = Book("SYN1", ["A"], 50_000_000)
    agg = Aggregator(book, {"A": ("127.0.0.1", port)})
    runner = LiveRunner(agg, policy, run_tag=f"s{seed}")
    await agg.start()
    t0 = time.monotonic()
    while va.sent < steps and time.monotonic() - t0 < steps / rate * 3 + 5:
        await asyncio.sleep(0.05)
    await asyncio.sleep(settle_s)            # let in-flight acks/status resolve; then report
    rep = runner.report()
    rep["venue_reconciliation"] = venue_reconciliation(runner, va)
    rep["venue"] = {k: v for k, v in va.stats().items() if k != "instance"}
    rep["feed_rtt_ns"] = agg.feed_rtt_ns["A"].summary()
    rep["decision_log"] = runner.decision_log
    await agg.stop()
    await va.stop()
    return rep


def compare_with_backtest(seed: int, steps: int, live_log) -> dict:
    """Match decisions by exogenous event index; differences come from arrival/observation timing."""
    from .backtest import ExecConfig, run as bt_run
    r = bt_run(MarketConfig(seed=seed, steps=steps, beta_pct=50), PolicyConfig(),
               ExecConfig(fee_per_unit=1, stale_after_ns=50_000_000))
    bt_orders = {f.decision_ref for f in r.authoritative_ledger.fills.values()}
    live_by_event = dict(live_log)
    common = [e for e in live_by_event]
    return {"live_decisions": len(live_by_event), "backtest_fills": len(bt_orders),
            "live_order_events_also_backtest_fill_events": sum(
                1 for e in common if live_by_event[e] == "order" and (e - 1) in bt_orders),
            "live_order_events": sum(1 for e in common if live_by_event[e] == "order"),
            "note": "backtest decides on every step with exact synchronous fills; live decides per "
                    "accepted quote and blocks while an order is outstanding, so decision sets differ"}


def main(argv=None):
    p = argparse.ArgumentParser(prog="quote_gate.live")
    p.add_argument("--seed", type=int, default=101)
    p.add_argument("--steps", type=int, default=3000)
    p.add_argument("--rate", type=float, default=500.0)
    a = p.parse_args(argv)
    rep = asyncio.run(run_loopback(a.seed, a.steps, a.rate))
    log = rep.pop("decision_log")
    rep["backtest_comparison"] = compare_with_backtest(a.seed, a.steps, log)
    print(json.dumps(rep, indent=1))


if __name__ == "__main__":
    main()
