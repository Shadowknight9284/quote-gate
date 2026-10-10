"""Offline deterministic normalization and replay of a Coinbase capture (contract R1).

  python3 -m quote_gate.coinbase_replay --capture artifacts/realdata/<run> [--out <dir>]

Outputs `normalized.jsonl` (one top-of-book event per applied book message), `ingestion.json`, and
`report.json`: **real market observations with hypothetical execution**. No order is ever sent.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from decimal import Decimal

from .book import Book, FeedStatus, Quote
from .execution import match
from .l2book import L2Book, Units
from .ledger import Fill, Ledger
from .quant import PolicyConfig, Strategy, imbalance_bp, reference_signal
from .router import Side

LABEL = "real market observations with hypothetical execution"
VENUE, SYMBOL = "CB", "BTC-USD"
LOT_UNITS = 100_000                    # 0.001 BTC in 1e-8 BTC units
STALE_NS = 50_000_000
SCENARIOS = (("fee0_delay0", 0, 0), ("fee10bp_delay0", 10, 0), ("fee10bp_delay1", 10, 1))


def normalize(capture_dir: str) -> tuple[list, dict]:
    """Raw messages -> top-of-book events. Returns (events, ingestion stats). Pure w.r.t. the file."""
    with open(os.path.join(capture_dir, "manifest.json")) as f:
        manifest = json.load(f)
    meta = manifest["product_metadata"]
    units = Units(Decimal(meta["quote_increment"]), Decimal(meta["base_increment"]))
    book = L2Book(units)
    events, stats = [], {"messages": 0, "by_type": {}, "sessions": 0, "unparseable": 0,
                         "other_product": 0, "invalid_intervals": [], "heartbeats": 0}
    session, invalid_since = None, None
    last_mono = None
    with open(os.path.join(capture_dir, "raw.jsonl")) as f:
        for line in f:
            rec = json.loads(line)
            stats["messages"] += 1
            if rec["session"] != session:
                if session is not None:
                    book.disconnect()
                    events.append({"kind": "session_end", "session": session, "mono_ns": last_mono})
                session = rec["session"]
                stats["sessions"] += 1
                book.new_session()
                events.append({"kind": "session_start", "session": session, "mono_ns": rec["mono_ns"]})
            last_mono = rec["mono_ns"]
            try:
                msg = json.loads(rec["raw"])
            except ValueError:
                stats["unparseable"] += 1
                continue
            typ = msg.get("type", "?")
            stats["by_type"][typ] = stats["by_type"].get(typ, 0) + 1
            if typ == "heartbeat":
                stats["heartbeats"] += 1          # recorded; never treated as quote freshness
                continue
            if typ not in ("snapshot", "l2update"):
                continue
            if msg.get("product_id") != SYMBOL:
                stats["other_product"] += 1
                continue
            was_valid = book.state == "VALID"
            top = book.apply_snapshot(msg.get("bids"), msg.get("asks")) if typ == "snapshot" \
                else book.apply_batch(msg.get("changes"))
            if was_valid and book.state == "INVALID" or (typ == "snapshot" and book.state == "INVALID"):
                invalid_since = rec["mono_ns"]
                stats["invalid_intervals"].append({"session": session, "start_mono_ns": rec["mono_ns"],
                                                   "reason": book.invalid_reason})
            if top is not None:
                events.append({"kind": "top", "session": session, "i": rec["i"], "mono_ns": rec["mono_ns"],
                               "src": typ, "exchange_time": msg.get("time"),
                               "bid_ticks": top.bid_ticks, "bid_units": top.bid_units,
                               "ask_ticks": top.ask_ticks, "ask_units": top.ask_units})
    if session is not None:
        book.disconnect()
        events.append({"kind": "session_end", "session": session, "mono_ns": last_mono})
    stats["book"] = book.stats
    stats["top_events"] = sum(1 for e in events if e["kind"] == "top")
    stats["units"] = {"price_increment": str(units.price_increment), "size_increment": str(units.size_increment),
                      "lot_units": LOT_UNITS, "lot_btc": str(units.size_increment * LOT_UNITS)}
    stats["capture_sha256"] = manifest["raw_sha256"]
    return events, stats


def replay(events: list, fee_bp: int, delay_events: int, policy=PolicyConfig()) -> dict:
    """Frozen policy over the normalized events with hypothetical IOC execution (contract R1).

    Per top-of-book event (emitted after a WHOLE batch/snapshot is applied), in order:
      1. deliver the quote to the aggregator Book (freshness = local receipt age of the last applied
         book message; heartbeats never count);
      2. orders whose arrival event is this one are matched against this event's top;
      3. the strategy decides on this event (only if the reference is FRESH);
      4. a new order with delay 0 is matched against this same observation (zero-latency assumption);
         with delay d it arrives d events later (never earlier, never on a later session).
    Liquidity: displayed top size is consumed only by our fills within the same event and is restored
    from the next event's displayed size (optimistic: our fills never deplete the real book).
    Session end: orders still pending are cancelled ("session_ended"); the venue is DISCONNECTED until
    the next session's snapshot; nothing from a dead session can fill later.
    """
    book = Book(SYMBOL, [VENUE], STALE_NS)
    strat, led = Strategy(policy), Ledger(policy.q_max)
    epoch, seq, step, n = None, 0, 0, 0
    pending: list = []                 # (arrive_step, order dict)
    remaining = {Side.BUY: 0, Side.SELL: 0}
    last_valid = None                  # last valid top (used ONLY for the dated terminal valuation)
    out = {"decisions": {}, "orders": 0, "filled": 0, "rejected": {}, "max_abs_position": 0,
           "hit": 0, "hit_n": 0, "equity_marks": 0}
    fills = []                         # (step, signed_lots, price_ticks, fee) for the audit
    peak, dd, prev_mid2, prev_imb = 0, 0, None, None

    def fee_for(price_ticks, lots):
        return 0 if fee_bp == 0 else math.ceil(price_ticks * lots * fee_bp / 10000)   # 1e-5 USD, ceil

    def reject(o, why, q=None):
        out["rejected"][why] = out["rejected"].get(why, 0) + 1
        if why == "insufficient_quantity" and "first_size_rejection" not in out and q is not None:
            disp = q.ask_quantity if o["side"] is Side.BUY else q.bid_quantity
            out["first_size_rejection"] = {
                "step": step, "side": o["side"].value, "order_lots": o["lots"],
                "order_btc": str(Decimal(o["lots"]) / 1000), "order_units": o["lots"] * LOT_UNITS,
                "displayed_units": disp, "displayed_btc": str(Decimal(disp) / 10**8),
                "price_usd": str(Decimal(q.ask_ticks if o["side"] is Side.BUY else q.bid_ticks) / 100)}
        strat.on_resolved(o["id"], 0, step)

    def execute(o, q):
        status, price, why = match(o["side"], o["lots"] * LOT_UNITS, o["limit"], q, remaining)
        if status != "FILLED":
            return reject(o, why, q)
        remaining[o["side"]] -= o["lots"] * LOT_UNITS
        signed = o["lots"] if o["side"] is Side.BUY else -o["lots"]
        fee = fee_for(price, o["lots"])
        led.apply(Fill(f"f-{o['id']}", o["id"], VENUE, signed, price, fee, o["decision"], step))
        fills.append((step, signed, price, fee))
        out["filled"] += 1
        strat.on_resolved(o["id"], signed, step)

    for e in events:
        if e["kind"] == "session_start":
            epoch, seq = book.on_connect(VENUE), 0
            continue
        if e["kind"] == "session_end":
            book.on_disconnect(VENUE, epoch)
            for _, o in pending:
                reject(o, "session_ended")
            pending.clear()
            prev_mid2 = prev_imb = None                    # no evaluation pairs across sessions
            continue
        seq += 1
        step += 1
        q = Quote(VENUE, SYMBOL, seq, e["bid_ticks"], e["ask_ticks"], e["bid_units"], e["ask_units"])
        book.on_quote(VENUE, epoch, q, e["mono_ns"])
        last_valid = e
        remaining = {Side.BUY: e["ask_units"], Side.SELL: e["bid_units"]}
        # Evaluation-only: imbalance at the event immediately BEFORE a mid change vs that change's sign.
        mid2 = e["bid_ticks"] + e["ask_ticks"]
        imb = imbalance_bp(e["bid_units"], e["ask_units"])
        if prev_mid2 is not None and mid2 != prev_mid2 and abs(prev_imb) > policy.theta_bp:
            out["hit_n"] += 1
            out["hit"] += (prev_imb > 0) == (mid2 > prev_mid2)
        prev_mid2, prev_imb = mid2, imb
        due = [p for p in pending if p[0] <= step]
        for p in due:
            pending.remove(p)
            execute(p[1], q)
        reason, intent = strat.decide(reference_signal(book, VENUE, e["mono_ns"]), step)
        out["decisions"][reason] = out["decisions"].get(reason, 0) + 1
        if intent is not None:
            n += 1
            limit = e["ask_ticks"] if intent.side is Side.BUY else e["bid_ticks"]
            o = {"id": f"r{n}", "side": intent.side, "lots": intent.qty, "limit": limit, "decision": step}
            strat.on_sent(o["id"])
            out["orders"] += 1
            if delay_events == 0:
                execute(o, q)
            else:
                pending.append((step + delay_events, o))
        out["max_abs_position"] = max(out["max_abs_position"], abs(led.position))
        eq = led.equity_ticks(led.mark_price(e["bid_ticks"], e["ask_ticks"]))
        out["equity_marks"] += 1
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    unresolved = len(pending)
    mark = led.mark_price(last_valid["bid_ticks"], last_valid["ask_ticks"]) if last_valid else None
    eq = led.equity_ticks(mark)
    liq_fee = fee_for(mark, abs(led.position)) if (mark and led.position) else 0
    usd = lambda v: None if v is None else str(Decimal(v) / Decimal(100000))      # 1e-5 USD -> USD
    turnover_lots = sum(abs(f[1]) for f in fills)
    notional = sum(abs(f[1]) * f[2] for f in fills)                                # 1e-5 USD
    out.update({
        "label": LABEL, "fee_bp_hypothetical": fee_bp, "delay_events": delay_events,
        "policy": {"theta_bp": policy.theta_bp, "q_max_lots": policy.q_max, "max_order_lots": policy.max_order_qty,
                   "lot_btc": "0.001", "q_max_btc": "0.005", "max_order_btc": "0.005"},
        "orders_unresolved_at_end": unresolved, "final_position_lots": led.position,
        "final_position_btc": str(Decimal(led.position) / 1000),
        "turnover_lots": turnover_lots, "turnover_btc": str(Decimal(turnover_lots) / 1000),
        "notional_usd": usd(notional),
        "fees_paid_usd": usd(led.fees_ticks), "fees_usd": usd(led.fees_ticks),
        "fee_per_fill_usd": {"min": usd(min((f[3] for f in fills), default=0)),
                             "max": usd(max((f[3] for f in fills), default=0))},
        "realized_pre_fee_usd": usd(led.realized_gross_ticks),
        "unrealized_at_mark_usd": usd(None if mark is None else led.unrealized_ticks(mark)),
        "terminal_mark": None if mark is None else {
            "price_usd": str(Decimal(mark) / 100), "side": "bid (long)" if led.position > 0 else "ask (short)"
            if led.position < 0 else "n/a (flat)", "session": last_valid["session"],
            "local_event_index": last_valid["i"], "local_mono_ns": last_valid["mono_ns"],
            "note": "hypothetical liquidation at the last valid top before the capture ended; no trade"},
        "hypothetical_liquidation_fee_usd": usd(liq_fee),
        "post_fee_terminal_liquidation_value_pnl_usd": usd(None if eq is None else eq - liq_fee),
        "pre_fee_terminal_liquidation_value_pnl_usd": usd(None if eq is None else eq + led.fees_ticks),
        "max_drawdown_usd": usd(dd),
        "hit_rate": round(out["hit"] / out["hit_n"], 4) if out["hit_n"] else None,
    })
    return out


def run(capture_dir: str, out_dir: str) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    events, stats = normalize(capture_dir)
    h = hashlib.sha256()
    with open(os.path.join(out_dir, "normalized.jsonl"), "w") as f:
        for e in events:
            line = json.dumps(e, sort_keys=True) + "\n"
            f.write(line)
            h.update(line.encode())
    stats["normalized_sha256"] = h.hexdigest()
    with open(os.path.join(out_dir, "ingestion.json"), "w") as f:
        json.dump(stats, f, indent=1)
    report = {"label": LABEL, "capture_sha256": stats["capture_sha256"],
              "normalized_sha256": stats["normalized_sha256"],
              "replay_config": {"scenarios": [{"name": n_, "fee_bp": f_, "delay_events": d_} for n_, f_, d_ in SCENARIOS],
                                "lot_units": LOT_UNITS, "lot_btc": "0.001", "stale_after_ns": STALE_NS,
                                "policy": "PolicyConfig() frozen: theta 2000 bp, q_max 5, max order 5, cooldown 0",
                                "fee_rounding": "ceil per fill, in 1e-5 USD",
                                "price_unit": "0.01 USD/BTC tick; ledger price per lot in 1e-5 USD"},
              "scenarios": {name: replay(events, fee, delay) for name, fee, delay in SCENARIOS}}
    with open(os.path.join(out_dir, "report.json"), "w") as f:
        json.dump(report, f, indent=1, sort_keys=True)
    return {"ingestion": stats, "report": report}


def main(argv=None):
    p = argparse.ArgumentParser(prog="quote_gate.coinbase_replay")
    p.add_argument("--capture", required=True)
    p.add_argument("--out", default=None)
    a = p.parse_args(argv)
    res = run(a.capture, a.out or os.path.join(a.capture, "replay"))
    ing = res["ingestion"]
    print(json.dumps({"label": LABEL, "messages": ing["messages"], "by_type": ing["by_type"],
                      "sessions": ing["sessions"], "top_events": ing["top_events"], "book": ing["book"],
                      "invalid_intervals": len(ing["invalid_intervals"]),
                      "normalized_sha256": ing["normalized_sha256"]}, indent=1))
    for name, r in res["report"]["scenarios"].items():
        print(json.dumps({"scenario": name, **{k: r[k] for k in (
            "orders", "filled", "rejected", "final_position_lots", "fees_usd",
            "pre_fee_terminal_liquidation_value_pnl_usd", "post_fee_terminal_liquidation_value_pnl_usd",
            "max_drawdown_usd", "hit_rate", "hit_n", "decisions")}}))


if __name__ == "__main__":
    main()
