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
    """Frozen policy over the normalized events with hypothetical IOC execution (contract R1)."""
    book = Book(SYMBOL, [VENUE], STALE_NS)
    strat, led = Strategy(policy), Ledger(policy.q_max)
    epoch, seq, step = None, 0, 0
    pending: list = []                 # (arrive_step, order dict)
    remaining = {Side.BUY: 0, Side.SELL: 0}
    cur = None
    last_valid = None                  # last valid top seen (kept across session ends for terminal mark)
    out = {"decisions": {}, "orders": 0, "filled": 0, "rejected": {}, "max_abs_position": 0,
           "hit": 0, "hit_n": 0, "equity_marks": 0, "max_drawdown_1e5usd": 0}
    peak, prev_imb_mid = 0, None
    n = 0

    def fee_for(price_ticks, lots):
        return 0 if fee_bp == 0 else math.ceil(price_ticks * lots * fee_bp / 10000)   # 1e-5 USD, ceil

    for e in events:
        if e["kind"] == "session_start":
            epoch, seq = book.on_connect(VENUE), 0
            continue
        if e["kind"] == "session_end":
            book.on_disconnect(VENUE, epoch)
            cur = None
            continue
        seq += 1
        step += 1
        q = Quote(VENUE, SYMBOL, seq, e["bid_ticks"], e["ask_ticks"], e["bid_units"], e["ask_units"])
        book.on_quote(VENUE, epoch, q, e["mono_ns"])
        cur = last_valid = e
        remaining = {Side.BUY: e["ask_units"], Side.SELL: e["bid_units"]}
        mid2 = e["bid_ticks"] + e["ask_ticks"]
        imb = imbalance_bp(e["bid_units"], e["ask_units"])
        # evaluation-only statistic: sign(imbalance at a mid change) vs sign of the NEXT mid change
        if prev_imb_mid is not None and mid2 != prev_imb_mid[1]:
            if abs(prev_imb_mid[0]) > policy.theta_bp:
                out["hit_n"] += 1
                out["hit"] += (prev_imb_mid[0] > 0) == (mid2 > prev_imb_mid[1])
            prev_imb_mid = (imb, mid2)
        elif prev_imb_mid is None:
            prev_imb_mid = (imb, mid2)
        # orders arriving now
        for arrive, o in [p for p in pending if p[0] <= step]:
            pending.remove((arrive, o))
            status, price, reason = match(o["side"], o["lots"] * LOT_UNITS, o["limit"], q, remaining)
            if status == "FILLED" and book.status(VENUE, e["mono_ns"]) is FeedStatus.FRESH:
                remaining[o["side"]] -= o["lots"] * LOT_UNITS
                signed = o["lots"] if o["side"] is Side.BUY else -o["lots"]
                led.apply(Fill(f"f-{o['id']}", o["id"], VENUE, signed, price, fee_for(price, o["lots"]),
                               o["decision"], step))
                out["filled"] += 1
                strat.on_resolved(o["id"], signed, step)
            else:
                r = reason or "stale_at_arrival"
                out["rejected"][r] = out["rejected"].get(r, 0) + 1
                strat.on_resolved(o["id"], 0, step)
        # decision
        reason, intent = strat.decide(reference_signal(book, VENUE, e["mono_ns"]), step)
        out["decisions"][reason] = out["decisions"].get(reason, 0) + 1
        if intent is not None:
            n += 1
            limit = e["ask_ticks"] if intent.side is Side.BUY else e["bid_ticks"]
            o = {"id": f"r{n}", "side": intent.side, "lots": intent.qty, "limit": limit, "decision": step}
            strat.on_sent(o["id"])
            out["orders"] += 1
            pending.append((step + delay_events, o))
            if delay_events == 0:       # process immediately against the same observation
                pending.remove((step, o))
                status, price, rsn = match(o["side"], o["lots"] * LOT_UNITS, o["limit"], q, remaining)
                if status == "FILLED":
                    remaining[o["side"]] -= o["lots"] * LOT_UNITS
                    signed = o["lots"] if o["side"] is Side.BUY else -o["lots"]
                    led.apply(Fill(f"f-{o['id']}", o["id"], VENUE, signed, price, fee_for(price, o["lots"]),
                                   step, step))
                    out["filled"] += 1
                    strat.on_resolved(o["id"], signed, step)
                else:
                    out["rejected"][rsn] = out["rejected"].get(rsn, 0) + 1
                    strat.on_resolved(o["id"], 0, step)
        out["max_abs_position"] = max(out["max_abs_position"], abs(led.position))
        mark = led.mark_price(e["bid_ticks"], e["ask_ticks"])
        eq = led.equity_ticks(mark)
        out["equity_marks"] += 1
        peak = max(peak, eq)
        out["max_drawdown_1e5usd"] = max(out["max_drawdown_1e5usd"], peak - eq)
    unresolved = len(pending)
    last = last_valid
    mark = led.mark_price(last["bid_ticks"], last["ask_ticks"]) if last else None
    eq = led.equity_ticks(mark)
    liq_fee = fee_for(mark, abs(led.position)) if (mark and led.position) else 0
    usd = lambda v: None if v is None else str(Decimal(v) / Decimal(100000))      # 1e-5 USD -> USD
    out.update({
        "label": LABEL, "fee_bp_hypothetical": fee_bp, "delay_events": delay_events,
        "policy": {"theta_bp": policy.theta_bp, "q_max_lots": policy.q_max, "max_order_lots": policy.max_order_qty,
                   "lot_btc": "0.001"},
        "orders_unresolved_at_end": unresolved, "final_position_lots": led.position,
        "fees_usd": usd(led.fees_ticks), "realized_pre_fee_usd": usd(led.realized_gross_ticks),
        "terminal_mark": "last valid top (hypothetical liquidation; no trade)" if mark else "MISSING",
        "post_fee_terminal_liquidation_value_pnl_usd": usd(None if eq is None else eq - liq_fee),
        "pre_fee_terminal_liquidation_value_pnl_usd": usd(None if eq is None else eq + led.fees_ticks),
        "max_drawdown_usd": usd(out.pop("max_drawdown_1e5usd")),
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
