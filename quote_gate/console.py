"""Local operations console: a stdlib HTTP server for the browser UI in ``web/``.

  python3 -m quote_gate.console [--port 8050]

Runs the same loopback stack as ``quote_gate.live`` on a background asyncio loop: venue A is the
strategy's reference and its only execution venue; venue B streams the second synthetic venue's
quotes (market data and route previews only). The browser polls JSON snapshots, triggers the
venues' labelled fault hooks, and runs the evidence checks. Binds 127.0.0.1 only; everything is
simulated.

Snapshots are taken on the event loop being observed, so the console adds load to the latencies it
shows. Use ``python3 -m quote_gate.live`` for benchmark numbers.
"""
from __future__ import annotations

import argparse
import asyncio
import glob
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from decimal import Decimal, InvalidOperation
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import fillcheck
from .aggregator import Aggregator
from .book import Book
from .live import LiveRunner, venue_reconciliation
from .market_sim import MarketConfig
from .quant import PolicyConfig, reference_signal, target_position
from .router import Side, route
from .venue import Venue

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIR = os.path.join(ROOT, "web")
STATIC = {"/": ("index.html", "text/html; charset=utf-8"),
          "/app.css": ("app.css", "text/css; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/logo.png": ("logo.png", "image/png"),
          "/favicon.png": ("favicon.png", "image/png"),
          "/favicon.ico": ("favicon.png", "image/png"),
          "/apple-touch-icon.png": ("apple-touch-icon.png", "image/png")}

RATE_HZ = 500                     # same offered rate as the published loopback runs
STALE_NS = 50_000_000             # same threshold as the order-path runner
SETTLE_S = 1.0
RECENT_ORDERS = 40
NOTES = 200
TIMELINE_S = 30
RTT_BINS_PER_DECADE = 8
RTT_MIN_US, RTT_MAX_US = 10, 10_000
MAX_BODY = 4096
MAX_FILLCHECK_BODY = 4 * fillcheck.MAX_BYTES + 4096     # two CSVs, allowing for JSON escaping
FAULTS = {"pause_feed", "pause_feed_1s", "resume_feed", "drop_connection",
          "drop_next_ack", "disconnect_after_exec", "restart_mid_order"}
FAULT_CHECK_S = 0.02              # how often pending fault outcomes are evaluated
FAULT_TIMEOUT_S = 5.0             # a fault whose expected outcome is not seen by then is "not observed"
MAX_FAULTS = 100
EXPECT = {
    "pause_feed": "venue goes stale within 50 ms and is excluded from routing",
    "pause_feed_1s": "venue goes stale within 50 ms, then fresh again after the 1 s pause",
    "resume_feed": "venue is fresh again",
    "drop_connection": "venue disconnects and reconnects with a new epoch",
    "drop_next_ack": "order goes UNKNOWN, trading halts, a status query resolves it; nothing is resent",
    "disconnect_after_exec": "order goes UNKNOWN on disconnect, then is resolved via status after reconnecting",
    "restart_mid_order": "venue loses its records; the order stays unresolved and trading stays halted",
}
ORDER_FAULTS = {"drop_next_ack", "disconnect_after_exec", "restart_mid_order"}   # venue A only


class ConsoleRunner(LiveRunner):
    """LiveRunner that also keeps bounded records of recent orders and lifecycle events.

    Records are appended after the parent has taken its timing, so measurements are unchanged.
    """

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.recent: deque = deque(maxlen=RECENT_ORDERS)
        self.notes: deque = deque(maxlen=NOTES)          # (mono_ns, venue, kind, text)
        self._reason = None

    def note(self, venue, kind, text):
        self.notes.append((time.monotonic_ns(), venue, kind, text))

    def _apply(self, p, obj, via):
        self._reason = obj.get("reason")
        super()._apply(p, obj, via)

    def _resolve(self, p, signed, via):
        super()._resolve(p, signed, via)
        f = self.ledger.fills.get(f"{p.venue_id}:{p.instance}:{p.oid}") if signed else None
        self.recent.append({"oid": p.oid, "side": p.side.value, "qty": p.qty, "limit": p.limit,
                            "venue": p.venue_id, "status": "FILLED" if signed else "REJECTED",
                            "reason": None if signed else (self._reason if via != "not_received"
                                                           else "not_received"),
                            "via": via, "price": f.price_ticks if f else None,
                            "fee": f.fee_ticks if f else None})
        self._reason = None
        if via != "exec":
            self.note(p.venue_id, "order", f"{p.oid} resolved via {via.replace('_', ' ')}")

    def _to_unknown(self, p):
        super()._to_unknown(p)
        self.note(p.venue_id, "unknown", f"{p.oid} UNKNOWN: trading halted, querying status")

    def _lost_record(self, p):
        first = p.state != "LOST_RECORD"
        super()._lost_record(p)
        if first:
            self.note(p.venue_id, "lost", f"{p.oid} LOST RECORD: venue restarted; stays halted")


class Session:
    """One console run: venues A and B, aggregator and runner on loopback."""

    def __init__(self, seed: int, seconds: int):
        self.seed, self.seconds = seed, seconds
        self.state = "starting"
        self.stop_requested = False
        self.final: dict | None = None
        self.error: str | None = None
        self.policy = PolicyConfig()
        self.faults: list[dict] = []                # injected faults and their observed outcomes
        self._fault_check: asyncio.Task | None = None

    async def start(self) -> None:
        # Extra steps so a reconnect (which restarts the venue's stream) cannot exhaust it early.
        steps = (self.seconds + 5) * RATE_HZ
        m = MarketConfig(seed=self.seed, steps=steps, beta_pct=50)
        self.venues = {"A": Venue("A", rate_hz=RATE_HZ, market=m, fee_per_unit=1),
                       "B": Venue("B", rate_hz=RATE_HZ, market=m, fee_per_unit=1)}
        addrs = {v: ("127.0.0.1", await ven.start()) for v, ven in self.venues.items()}
        self.book = Book("SYN1", ["A", "B"], STALE_NS)
        self.agg = Aggregator(self.book, addrs, observer="console")
        self.runner = ConsoleRunner(self.agg, self.policy, run_tag=f"s{self.seed}")
        await self.agg.start()
        self.t0 = time.monotonic_ns()
        self.state = "running"
        self._watch_task = asyncio.get_running_loop().create_task(self._watch())

    async def _watch(self) -> None:
        try:
            deadline = time.monotonic() + self.seconds
            while time.monotonic() < deadline and not self.stop_requested:
                await asyncio.sleep(0.05)
            self.agg.quote_listener = None          # stop deciding; let in-flight outcomes resolve
            self.state = "settling"
            await asyncio.sleep(SETTLE_S)
            self.final = self.snapshot(detail=True)
            self.final["state"] = "complete"
        except Exception as e:                      # surfaced to the UI instead of a silent hang
            self.error = f"{type(e).__name__}: {e}"
        finally:
            await self.agg.stop()
            for v in self.venues.values():
                await v.stop()
            self.state = "complete"

    # ---- faults (each maps to a labelled Venue hook) ----
    def fault(self, kind: str, venue_id: str) -> dict:
        v = self.venues[venue_id]
        r = self.runner
        rec = self._record_fault(kind, venue_id)
        if kind == "pause_feed":
            v.pause()
            text = "feed paused (venue keeps its own book; quotes are not sent)"
        elif kind == "pause_feed_1s":
            v.pause()
            asyncio.get_running_loop().call_later(1.0, v.resume)
            text = "feed paused for 1 s, then resumed"
        elif kind == "resume_feed":
            v.resume()
            text = "feed resumed"
        elif kind == "drop_connection":
            v.drop_connection()
            text = "connection dropped; aggregator reconnects with a new epoch"
        elif kind == "drop_next_ack":
            v.drop_next_acks += 1
            text = "next execution report will be recorded but not sent"
        elif kind == "disconnect_after_exec":
            v.disconnect_after_exec += 1
            text = "connection will drop right after the next order is recorded"
        else:                                       # restart_mid_order
            asyncio.get_running_loop().create_task(self._restart_mid_order(v))
            text = "dropping the next ack, then restarting the venue (order store lost)"
        r.note(venue_id, "fault", text)
        if self._fault_check is None or self._fault_check.done():
            self._fault_check = asyncio.get_running_loop().create_task(self._check_faults())
        return {"id": rec["id"], "note": text, "expect": rec["expect"]}

    def _record_fault(self, kind: str, venue_id: str) -> dict:
        c = self.runner.c
        rec = {"id": len(self.faults) + 1, "kind": kind, "venue": venue_id, "t_ns": time.monotonic_ns(),
               "expect": EXPECT[kind], "state": "pending", "observed_ms": None, "detail": None,
               "base": {"timeouts": c.timeouts, "unknown_disconnect": c.unknown_disconnect,
                        "resolved": c.resolved_by_status + c.resolved_not_received,
                        "lost": c.lost_record, "connects": self.agg.connects[venue_id]}}
        if len(self.faults) < MAX_FAULTS:
            self.faults.append(rec)
        return rec

    async def _check_faults(self) -> None:
        """Mark each pending fault handled once its expected outcome appears in the running system."""
        while any(f["state"] == "pending" for f in self.faults):
            await asyncio.sleep(FAULT_CHECK_S)
            now = time.monotonic_ns()
            events = self.agg.events.items()
            for f in self.faults:
                if f["state"] != "pending":
                    continue
                result = self._fault_outcome(f, events)
                if result is not None:
                    f["state"], f["detail"] = "handled", result[1]
                    f["observed_ms"] = round((result[0] - f["t_ns"]) / 1e6, 1)
                elif now - f["t_ns"] > FAULT_TIMEOUT_S * 1e9:
                    f["state"] = "not_observed"
                    f["detail"] = ("no order was sent to fault (trading halted or no signal)"
                                   if f["kind"] in ORDER_FAULTS else "expected change not seen")

    def _fault_outcome(self, f: dict, events: list):
        """(observed monotonic ns, detail) when the expected outcome has happened, else None."""
        c, b, vid, t0 = self.runner.c, f["base"], f["venue"], f["t_ns"]
        after = [(t, k, d) for t, v, k, d in events if v == vid and t >= t0]
        status = [(t, d) for t, k, d in after if k == "status"]
        now = time.monotonic_ns()
        kind = f["kind"]
        if kind in ("pause_feed", "pause_feed_1s"):
            stale = next(((t, d) for t, d in status if d["to"] in ("stale", "disconnected")), None)
            if stale is None:
                return None
            age = stale[1].get("quote_age_ns")
            detail = f"{stale[1]['to']} (last quote {age / 1e6:.1f} ms old); excluded from routing" \
                if age is not None else stale[1]["to"]
            if kind == "pause_feed":
                return stale[0], detail
            fresh = next((t for t, d in status if t > stale[0] and d["to"] == "fresh"), None)
            return None if fresh is None else \
                (stale[0], detail + f"; fresh again {(fresh - t0) / 1e6:,.0f} ms after the pause began")
        if kind == "resume_feed":
            fresh = next((t for t, d in status if d["to"] == "fresh"), None)
            if fresh is None and self.book.status(vid, now).value == "fresh":
                fresh = now
            return None if fresh is None else (fresh, "fresh again")
        if kind == "drop_connection":
            conn = next(((t, d) for t, k, d in after if k == "connect"), None)
            return None if conn is None else (conn[0], f"reconnected: epoch {conn[1]['epoch']}")
        resolved = c.resolved_by_status + c.resolved_not_received
        if kind == "drop_next_ack" and c.timeouts > b["timeouts"] and resolved > b["resolved"]:
            return now, "ack timeout → UNKNOWN → status query → resolved; not resent"
        if kind == "disconnect_after_exec" and c.unknown_disconnect > b["unknown_disconnect"] \
                and resolved > b["resolved"]:
            return now, "UNKNOWN on disconnect → reconnected → resolved via status; not resent"
        if kind == "restart_mid_order" and c.lost_record > b["lost"]:
            lost = [p.oid for p in self.runner.pending.values() if p.state == "LOST_RECORD"]
            return now, f"{', '.join(lost) or 'order'} unresolvable; trading halted instead of guessing"
        return None

    async def _restart_mid_order(self, v: Venue) -> None:
        """Same sequence as test_venue_restart_with_lost_record_stays_unresolved."""
        r = self.runner
        before = r.c.timeouts
        v.drop_next_acks += 1
        t_end = time.monotonic() + 3.0
        while r.c.timeouts == before:
            if time.monotonic() > t_end or self.state != "running":
                r.note(v.venue_id, "fault", "restart skipped: no order timed out (is trading halted?)")
                for f in self.faults:
                    if f["kind"] == "restart_mid_order" and f["state"] == "pending":
                        f["state"], f["detail"] = "skipped", "no order timed out (trading halted or no signal)"
                return
            await asyncio.sleep(0.005)
        v.restart_lose_store()
        r.note(v.venue_id, "fault", "venue restarted: new instance, empty order store")

    # ---- snapshot (runs on the event loop) ----
    def snapshot(self, detail: bool = False) -> dict:
        now = time.monotonic_ns()
        book, r, c = self.book, self.runner, self.runner.c
        venues = {}
        for vid, st in book.venues.items():
            q = st.quote
            venues[vid] = {
                "status": st.status(now, book.stale_after_ns).value,
                "role": "reference + execution" if vid == "A" else "market data only",
                "age_ms": round((now - st.received_monotonic_ns) / 1e6, 2) if q else None,
                "bid": q.bid_ticks if q else None, "ask": q.ask_ticks if q else None,
                "bid_qty": q.bid_quantity if q else None, "ask_qty": q.ask_quantity if q else None,
                "sequence": st.last_sequence, "epoch": st.connection_epoch,
                "accepted": st.accepted, "rejected": sum(st.rejected.values()), "gaps": st.gaps,
                "connects": self.agg.connects[vid], "paused": self.venues[vid].paused}
        sig = reference_signal(book, "A", now)
        lost = any(p.state == "LOST_RECORD" for p in r.pending.values())
        trading = ("halted_lost_record" if lost else "halted_unknown" if r.strategy.halted_unknown
                   else "no_signal" if sig is None else "trading")
        previews = {}
        for side in (Side.BUY, Side.SELL):
            d = route(book, side, 1, now)
            previews[side.value] = {"venue": d.venue_id, "price": d.price_ticks,
                                    "excluded": [list(x) for x in d.excluded]}
        out = {
            "state": self.state, "seed": self.seed, "seconds": self.seconds, "rate_hz": RATE_HZ,
            "stale_ms": STALE_NS // 1_000_000, "error": self.error,
            "elapsed_s": round((now - self.t0) / 1e9, 2),
            "trading": trading, "venues": venues, "route_preview": previews,
            "strategy": {"signal_bp": sig, "target": target_position(sig, self.policy),
                         "theta_bp": self.policy.theta_bp, "q_max": self.policy.q_max,
                         "outstanding": r.strategy.outstanding,
                         "halted_orders": sorted(r.strategy.halted_unknown),
                         "decisions": dict(c.decisions)},
            "counters": {"sent": c.sent, "filled": c.filled, "rejected": dict(c.rejected),
                         "timeouts": c.timeouts, "unknown_disconnect": c.unknown_disconnect,
                         "resolved_by_exec": c.resolved_by_exec,
                         "resolved_by_status": c.resolved_by_status,
                         "resolved_not_received": c.resolved_not_received,
                         "lost_record": c.lost_record, "duplicate_exec": c.duplicate_exec,
                         "status_queries": c.status_queries, "no_route": c.no_route,
                         "invalid_response": dict(c.invalid_response)},
            "ledger": {"position": r.ledger.position, "max_abs_position": r.max_abs_position,
                       "cash_ticks": r.ledger.cash_ticks, "fees_ticks": r.ledger.fees_ticks,
                       "realized_gross_ticks": r.ledger.realized_gross_ticks},
            "unresolved": [[p.oid, p.state] for p in r.pending.values()],
            "pnl": self._pnl(),
            "faults": [{k: f[k] for k in ("id", "kind", "venue", "expect", "state", "observed_ms", "detail")}
                       for f in self.faults],
            "timeline": self._timeline(now),
            "events": self._events(now),
        }
        if detail:
            out["orders"] = list(reversed(r.recent))
            out["reconciliation"] = venue_reconciliation(r, self.venues["A"])
            out["exec_rtt"] = {**r.exec_rtt_ns.summary(), "hist": _histogram(r.exec_rtt_ns.values())}
            out["reconcile_ns"] = r.reconcile_ns.summary()
            out["feed_rtt"] = {v: self.agg.feed_rtt_ns[v].summary() for v in book.venues}
        return out

    def _pnl(self) -> dict:
        """Cash plus open position at venue A's last executable price (hypothetical; no closing trade)."""
        q, led = self.book.venues["A"].quote, self.runner.ledger
        mark = led.mark_price(q.bid_ticks if q else None, q.ask_ticks if q else None)
        return {"equity_ticks": led.equity_ticks(mark), "mark_ticks": mark if led.position else None}

    def _timeline(self, now: int) -> dict:
        """Status transitions in the last TIMELINE_S seconds, plus each venue's state at the start."""
        cutoff = now - TIMELINE_S * 1_000_000_000
        start = {v: None for v in self.book.venues}
        segs = {v: [] for v in self.book.venues}
        for t, vid, kind, d in self.agg.events.items():
            if kind != "status":
                continue
            if t < cutoff:
                start[vid] = d["to"]
            else:
                segs[vid].append([round((t - now) / 1e9, 3), d["to"]])
        return {"window_s": TIMELINE_S, "start": start, "transitions": segs}

    def _events(self, now: int, n: int = 40) -> list:
        agg = []
        for t, vid, kind, d in self.agg.events.items()[-n:]:
            if kind == "status":
                text = f"status {d['from'] or '-'} → {d['to']}"
                if "quote_age_ns" in d:
                    text += f" (last quote {d['quote_age_ns'] / 1e6:.1f} ms ago)"
            elif kind == "connect":
                text = f"connected: epoch {d['epoch']}, session {d['session']}"
            else:
                text = f"disconnected: epoch {d['epoch']} ({d.get('reason')})"
            agg.append((t, vid, kind, text))
        merged = sorted(agg + list(self.runner.notes)[-n:], key=lambda e: e[0])[-n:]
        return [[round((t - now) / 1e9, 3), vid, kind, text] for t, vid, kind, text in reversed(merged)]


def _histogram(values_ns: list[int]) -> dict:
    """Log-spaced bins in microseconds; values outside the range are counted at the edges."""
    lo, hi = math.log10(RTT_MIN_US), math.log10(RTT_MAX_US)
    nb = int((hi - lo) * RTT_BINS_PER_DECADE)
    counts = [0] * nb
    for v in values_ns:
        x = (math.log10(max(v, 1) / 1000) - lo) * RTT_BINS_PER_DECADE
        counts[min(nb - 1, max(0, int(x)))] += 1
    edges = [round(10 ** (lo + i / RTT_BINS_PER_DECADE), 2) for i in range(nb + 1)]
    return {"edges_us": edges, "counts": counts}


# ---- published evidence (read-only files in the repository) ----

def _read_json(path: str):
    with open(path) as f:
        return json.load(f)


def _source_matrix_path() -> str | None:
    found = sorted(glob.glob(os.path.join(ROOT, "docs/evidence/synthetic-matrix/source-*.json")))
    return found[0] if found else None


def quant_summary() -> dict:
    path = _source_matrix_path()
    if path is None:
        return {"available": False}
    m = _read_json(path)
    cells = []
    for c in m["cells"]:
        g, x = c["aggregate"], c["exec"]
        cells.append({"cell": c["cell"], "beta_pct": c["beta_pct"], "trade": x["trade"],
                      "exec_delay_steps": x["exec_delay_steps"], "drop_ack_pct": x["drop_ack_pct"],
                      "outage_every": x["outage_every"], "outage_len": x["outage_len"],
                      "fills": g["fills"]["mean"], "turnover": g["turnover"]["mean"],
                      "fees": g["fees"]["mean"], "gross": g["gross"], "net": g["net"],
                      "hit_rate": g["hit_rate"], "open_position_seeds": g["open_position_seeds"],
                      "max_abs_position": g["max_abs_position"]["max"],
                      "unknown_mean": g["unknown"]["mean"], "reconciliation": g["reconciliation"]})
    by = {c["cell"]: c for c in m["cells"]}
    paired = [{"seed": a["seed"], "diff": b["gross"] - a["gross"]}
              for a, b in zip(by["2_null"]["per_seed"], by["3_controlled"]["per_seed"])]
    return {"available": True, "source": os.path.relpath(path, ROOT), "run_id": m["run_id"],
            "seeds": m["seeds"], "steps": m["steps"], "policy": m["policy"],
            "fee_per_unit": m["fee_per_unit"], "cells": cells,
            "paired": paired, "paired_stats": m["paired_vs_null"]["3_controlled-2_null:gross"]}


def replay_summary() -> dict:
    d = os.path.join(ROOT, "docs/realdata-results")
    try:
        rep, man = _read_json(os.path.join(d, "report.json")), _read_json(os.path.join(d, "manifest.json"))
    except FileNotFoundError:
        return {"available": False}
    keys = ("orders", "filled", "rejected", "hit_rate", "fees_usd", "fee_bp_hypothetical",
            "delay_events", "pre_fee_terminal_liquidation_value_pnl_usd",
            "post_fee_terminal_liquidation_value_pnl_usd", "final_position_lots", "max_abs_position")
    return {"available": True, "label": rep["label"],
            "scenarios": {n: {k: s.get(k) for k in keys} for n, s in rep["scenarios"].items()},
            "capture": {"product": man["product_metadata"]["id"], "seconds": man["limits"]["seconds"],
                        "messages": man["messages"], "counts_by_type": man["counts_by_type"],
                        "reconnects": man["reconnects"], "stop_reason": man["stop_reason"]}}


def evidence_summary() -> dict:
    runs = []
    for p in sorted(glob.glob(os.path.join(ROOT, "docs/evidence/perf-exec-rtt-*/run*.json"))):
        r = _read_json(p)
        runs.append({"file": os.path.relpath(p, ROOT), "sent": r["sent"], "filled": r["filled"],
                     "timeouts": r["timeouts"], "exec_rtt_ns": r["exec_rtt_ns"],
                     "mismatched": r["venue_reconciliation"]["mismatched"]})
    return {"perf_runs": runs, "hashes": [{"file": f, "sha256": h} for h, f in _published_hashes()]}


def _published_hashes() -> list[tuple[str, str]]:
    try:
        with open(os.path.join(ROOT, "docs/evidence/README.md")) as f:
            text = f.read()
    except FileNotFoundError:
        return []
    return re.findall(r"^([0-9a-f]{64})\s+(\S+)$", text, flags=re.M)


def verify_hashes() -> dict:
    out = []
    for want, rel in _published_hashes():
        path = os.path.join(ROOT, rel)
        try:
            with open(path, "rb") as f:
                got = hashlib.sha256(f.read()).hexdigest()
        except FileNotFoundError:
            got = None
        out.append({"file": rel, "expected": want, "actual": got, "match": got == want})
    return {"files": out, "all_match": bool(out) and all(x["match"] for x in out)}


def run_tests() -> dict:
    t0 = time.monotonic()
    p = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."],
                       cwd=ROOT, capture_output=True, text=True, timeout=600)
    tail = (p.stderr or "").strip().splitlines()
    ran = next((int(m.group(1)) for line in tail if (m := re.match(r"Ran (\d+) test", line))), None)
    return {"ok": p.returncode == 0, "ran": ran, "summary": tail[-1] if tail else "",
            "seconds": round(time.monotonic() - t0, 1)}


def reproduce_matrix() -> dict:
    src = _source_matrix_path()
    if src is None:
        return {"ok": False, "error": "published source matrix not found"}
    tmp = tempfile.mkdtemp(prefix="qg-matrix-")
    t0 = time.monotonic()
    try:
        p = subprocess.run([sys.executable, "-m", "quote_gate.experiments", "--out", tmp],
                           cwd=ROOT, capture_output=True, text=True, timeout=600)
        if p.returncode != 0:
            return {"ok": False, "error": (p.stderr or "").strip()[-500:]}
        new = _read_json(json.loads(p.stdout.strip().splitlines()[-1])["saved"])
        old = _read_json(src)
        ids = (new.pop("run_id"), old.pop("run_id"))
        diff = sorted(k for k in set(new) | set(old) if new.get(k) != old.get(k))
        return {"ok": not diff, "differing_keys": diff, "new_run_id": ids[0], "source_run_id": ids[1],
                "compared_keys": sorted(set(new) | set(old)), "seconds": round(time.monotonic() - t0, 1)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


JOBS = {"tests": run_tests, "reproduce": reproduce_matrix, "verify": verify_hashes}


# ---- fill check (user-supplied CSVs; processed in memory, never stored) ----

def _csv_field(body: dict, name: str) -> str:
    v = body.get(name)
    if not isinstance(v, str) or not v.strip():
        raise BadRequest(f"{name} must be the CSV file's text")
    return v


def _mapping_field(body: dict, name: str) -> dict | None:
    v = body.get(name)
    if v is None:
        return None
    if not isinstance(v, dict) or any(k not in fillcheck.FIELDS or (x is not None and not isinstance(x, str))
                                      for k, x in v.items()):
        raise BadRequest(f"{name} must map {', '.join(fillcheck.FIELDS)} to column names")
    return v


def _positive_decimal(body: dict, name: str) -> Decimal | None:
    v = body.get(name)
    if v in (None, ""):
        return None
    try:
        d = Decimal(str(v))
    except InvalidOperation:
        raise BadRequest(f"{name} must be a number") from None
    if not d.is_finite() or d <= 0:
        raise BadRequest(f"{name} must be a positive number")
    return d


def fillcheck_preview(body: dict) -> dict:
    text = _csv_field(body, "csv")
    try:
        headers = fillcheck.read_headers(text)
    except fillcheck.FillCheckError as e:
        raise BadRequest(str(e)) from None
    if not headers:
        raise BadRequest("the file is empty")
    rows = []
    for rec in fillcheck._reader(text):
        if len(rows) > 5:
            break
        rows.append(rec)
    return {"headers": headers, "mapping": fillcheck.detect_mapping(headers), "rows": rows[1:6],
            "fields": list(fillcheck.FIELDS), "required": list(fillcheck.REQUIRED)}


def fillcheck_run(body: dict) -> dict:
    logs = {}
    for side, label in (("broker", "broker file"), ("bot", "bot log")):
        try:
            logs[side] = fillcheck.parse(_csv_field(body, f"{side}_csv"), _mapping_field(body, f"{side}_mapping"))
        except fillcheck.FillCheckError as e:
            raise BadRequest(f"{label}: {e}") from None
    return fillcheck.check(logs["broker"], logs["bot"], _positive_decimal(body, "max_position"),
                           _positive_decimal(body, "mark"))


SAMPLES = {"small": ("broker_fills.csv", "bot_log.csv", 5),
           "realistic": ("realistic_broker.csv", "realistic_bot.csv", 20)}


def fillcheck_sample(name: str = "small") -> dict:
    if name not in SAMPLES:
        raise BadRequest(f"unknown sample; choose one of {', '.join(SAMPLES)}")
    broker, bot, limit = SAMPLES[name]
    d = os.path.join(ROOT, "examples", "fills")
    with open(os.path.join(d, broker)) as f, open(os.path.join(d, bot)) as g:
        return {"broker_csv": f.read(), "bot_csv": g.read(), "broker_name": broker, "bot_name": bot,
                "suggested_limit": limit}


# ---- server ----

class Console:
    """Owns the background event loop, the current session and background jobs."""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, name="console-loop", daemon=True)
        self.thread.start()
        self.session: Session | None = None
        self.jobs: dict[str, dict] = {}
        self.lock = threading.Lock()

    def call(self, fn, timeout=5.0):
        """Run fn() on the event loop thread and return its result."""
        async def wrap():
            return fn()
        return asyncio.run_coroutine_threadsafe(wrap(), self.loop).result(timeout)

    def start_run(self, seed: int, seconds: int) -> dict:
        with self.lock:
            if self.session is not None and self.session.state != "complete":
                raise Conflict("a run is already in progress")
            s = Session(seed, seconds)
            asyncio.run_coroutine_threadsafe(s.start(), self.loop).result(10)
            self.session = s
        return {"started": True, "seed": seed, "seconds": seconds}

    def live(self, detail: bool) -> dict:
        s = self.session
        if s is None:
            return {"state": "idle"}
        if s.state == "complete" and s.final is not None:
            return s.final if detail else {k: v for k, v in s.final.items()
                                           if k not in ("orders", "exec_rtt", "reconcile_ns", "feed_rtt")}
        if s.state == "complete":
            return {"state": "complete", "error": s.error}
        return self.call(lambda: s.snapshot(detail))

    def stop_run(self) -> dict:
        s = self.session
        if s is None or s.state != "running":
            raise Conflict("no run in progress")
        s.stop_requested = True
        return {"stopping": True}

    def fault(self, kind: str, venue_id: str) -> dict:
        s = self.session
        if s is None or s.state != "running":
            raise Conflict("faults can only be injected during a run")
        return {"ok": True, **self.call(lambda: s.fault(kind, venue_id))}

    def start_job(self, name: str) -> dict:
        with self.lock:
            job = self.jobs.get(name)
            if job is not None and job["state"] == "running":
                raise Conflict(f"{name} is already running")
            job = self.jobs[name] = {"state": "running", "result": None}

        def work():
            try:
                job["result"] = JOBS[name]()
            except Exception as e:
                job["result"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            job["state"] = "done"
        threading.Thread(target=work, name=f"job-{name}", daemon=True).start()
        return {"state": "running"}

    def close(self):
        s = self.session
        if s is not None and s.state == "running":
            s.stop_requested = True
            deadline = time.monotonic() + 5
            while s.state != "complete" and time.monotonic() < deadline:
                time.sleep(0.05)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(2)


class Conflict(Exception):
    pass


class BadRequest(Exception):
    pass


def _int_field(body: dict, name: str, lo: int, hi: int, default: int) -> int:
    v = body.get(name, default)
    if type(v) is not int or not lo <= v <= hi:
        raise BadRequest(f"{name} must be an integer in [{lo}, {hi}]")
    return v


def make_handler(console: Console):
    class Handler(BaseHTTPRequestHandler):
        server_version = "QuoteGateConsole"

        def log_message(self, *args):      # no per-request logging
            pass

        def _send(self, code, body: bytes, ctype: str):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code=HTTPStatus.OK):
            self._send(code, json.dumps(obj, separators=(",", ":")).encode(), "application/json")

        def _body(self, limit: int = MAX_BODY) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n > limit:
                raise BadRequest("request body too large")
            raw = self.rfile.read(n) if n else b"{}"
            try:
                obj = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                raise BadRequest("body must be JSON") from None
            if not isinstance(obj, dict):
                raise BadRequest("body must be a JSON object")
            return obj

        def _dispatch(self, fn):
            try:
                self._json(fn())
            except BadRequest as e:
                self._json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
            except Conflict as e:
                self._json({"error": str(e)}, HTTPStatus.CONFLICT)
            except Exception as e:
                self._json({"error": f"{type(e).__name__}: {e}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

        def do_GET(self):
            u = urlparse(self.path)
            if u.path in STATIC:
                name, ctype = STATIC[u.path]
                with open(os.path.join(WEB_DIR, name), "rb") as f:
                    return self._send(HTTPStatus.OK, f.read(), ctype)
            q = parse_qs(u.query)
            routes = {"/api/live": lambda: console.live(q.get("detail") == ["1"]),
                      "/api/quant": quant_summary, "/api/replay": replay_summary,
                      "/api/evidence": evidence_summary,
                      "/api/fillcheck/sample": lambda: fillcheck_sample((q.get("name") or ["small"])[0])}
            if u.path in routes:
                return self._dispatch(routes[u.path])
            m = re.fullmatch(r"/api/jobs/(\w+)", u.path)
            if m and m.group(1) in JOBS:
                return self._dispatch(lambda: console.jobs.get(m.group(1), {"state": "idle"}))
            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

        def do_POST(self):
            path = urlparse(self.path).path

            def run():
                b = self._body()
                return console.start_run(_int_field(b, "seed", 1, 1_000_000, 101),
                                         _int_field(b, "seconds", 5, 300, 30))

            def fault():
                b = self._body()
                kind, venue = b.get("kind"), b.get("venue", "A")
                if kind not in FAULTS or venue not in ("A", "B"):
                    raise BadRequest("unknown fault or venue")
                if kind in ORDER_FAULTS and venue != "A":
                    raise BadRequest("order faults apply to venue A (the execution venue)")
                return console.fault(kind, venue)

            routes = {"/api/run": run, "/api/stop": console.stop_run, "/api/fault": fault,
                      "/api/fillcheck/preview": lambda: fillcheck_preview(self._body(MAX_FILLCHECK_BODY)),
                      "/api/fillcheck": lambda: fillcheck_run(self._body(MAX_FILLCHECK_BODY))}
            if path in routes:
                return self._dispatch(routes[path])
            m = re.fullmatch(r"/api/jobs/(\w+)", path)
            if m and m.group(1) in JOBS:
                return self._dispatch(lambda: console.start_job(m.group(1)))
            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    return Handler


def serve(port: int = 8050) -> tuple[ThreadingHTTPServer, Console]:
    console = Console()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(console))
    httpd.daemon_threads = True
    return httpd, console


def main(argv=None):
    p = argparse.ArgumentParser(prog="quote_gate.console")
    p.add_argument("--port", type=int, default=8050)
    a = p.parse_args(argv)
    httpd, console = serve(a.port)
    print(f"QuoteGate console on http://127.0.0.1:{httpd.server_address[1]}  (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        console.close()


if __name__ == "__main__":
    main()
