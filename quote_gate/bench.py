"""Scheduled-load benchmark client for the aggregator's route port.

Requests are scheduled at fixed offsets (offered rate). A bounded queue feeds
``connections`` workers, each with one request in flight, so outstanding
requests <= connections. If the queue is full at a scheduled instant the
request is counted ``not_sent_backlog`` rather than silently delayed forever.

What one request does: a ``route`` lookup (side, quantity) answered from the
aggregator's in-memory book. It does not contact a venue and executes nothing.

Latency fields (all client-host time.monotonic_ns; µs in summaries):
- schedule_to_completion: scheduled instant -> reply frame decoded. Includes
  generator delay, wait for a free connection, and the round trip.
- send_to_completion: just before the request is written -> reply frame decoded.
  Client-observed round trip over a persistent connection (not "service time").
- schedule_to_send: scheduled instant -> just before the request is written.
- generator_delay: scheduled instant -> producer enqueues the request.
Server-side figures come from the aggregator's ``stats`` (bounded rings,
cumulative since the aggregator started): ``route_ns`` = route() computation
only; ``process_ns`` = feed frame read -> book updated (feed path, not requests).
Observations are kept in memory (bounded by rate * duration) and written after
the run, never during it.

  python3 -m quote_gate.bench --port 9200 --rate 2000 --duration 10 --warmup 2 \
      --connections 8 --trials 3 --label local-2venue
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import subprocess
import time

from . import protocol
from .metrics import Anchor, percentile

ROUTE_FIELDS = ["scheduled_mono_ns", "sent_mono_ns", "completed_mono_ns", "outcome", "quote_age_ns"]

MAX_OBSERVATIONS = 2_000_000


async def _request(conn, obj):
    reader, writer = conn
    writer.write(protocol.encode(obj))
    await writer.drain()
    return await asyncio.wait_for(protocol.read_frame(reader), 5)


async def run_trial(host, port, rate, duration, connections, side="buy", quantity=1,
                    backlog=None, observer="bench@local"):
    total = int(rate * duration)
    if total > MAX_OBSERVATIONS:
        raise ValueError("rate * duration exceeds MAX_OBSERVATIONS")
    interval = 1e9 / rate
    queue: asyncio.Queue = asyncio.Queue(maxsize=backlog or connections * 4)
    obs = []                                  # (scheduled, sent, recv, outcome, quote_age_ns)
    anchor = Anchor(observer)
    gen_lag = []
    not_sent = 0
    conns = [await asyncio.open_connection(host, port, limit=protocol.MAX_FRAME)
             for _ in range(connections)]
    before = await _request(conns[0], {"type": "stats"})

    async def worker(conn):
        while True:
            t_sched = await queue.get()
            if t_sched is None:
                return
            t_send = time.monotonic_ns()
            try:
                r = await _request(conn, {"type": "route", "side": side, "quantity": quantity})
                outcome = (r.get("venue_id") or "NO_ROUTE") if r.get("type") == "route_result" \
                    else "ERROR"
                age = r.get("quote_age_ns")
            except (asyncio.TimeoutError, protocol.FrameError, ConnectionError, OSError) as e:
                outcome, age = "FAIL:" + type(e).__name__, None
            obs.append((t_sched, t_send, time.monotonic_ns(), outcome, age))

    workers = [asyncio.create_task(worker(c)) for c in conns]
    start = time.monotonic_ns() + 10_000_000
    for i in range(total):
        t_sched = start + int(i * interval)
        now = time.monotonic_ns()
        if t_sched > now:
            await asyncio.sleep((t_sched - now) / 1e9)
        gen_lag.append(time.monotonic_ns() - t_sched)
        try:
            queue.put_nowait(t_sched)
        except asyncio.QueueFull:
            not_sent += 1
    for _ in workers:
        await queue.put(None)
    await asyncio.gather(*workers)
    after = await _request(conns[0], {"type": "stats"})
    for _, w in conns:
        w.close()
    return {"start_ns": start, "offered": total, "header": anchor.header("qg.route_obs.v2", ROUTE_FIELDS),
            "stale_after_ns": before.get("stale_after_ns"), "not_sent_backlog": not_sent,
            "observations": obs, "generator_lag_ns": gen_lag,
            "server_before": before, "server_after": after}


def summarize(trial, warmup_s):
    cutoff = trial["start_ns"] + int(warmup_s * 1e9)
    steady = [o for o in trial["observations"] if o[0] >= cutoff]
    out = {"offered": trial["offered"], "not_sent_backlog": trial["not_sent_backlog"],
           "completed": len(trial["observations"]), "steady_samples": len(steady)}
    outcomes = {}
    for o in trial["observations"]:
        outcomes[o[3]] = outcomes.get(o[3], 0) + 1
    out["outcomes"] = outcomes
    stale = trial.get("stale_after_ns")
    if stale is not None:
        # Server-reported age of the chosen quote; a route on a quote at/over the stale
        # threshold would be a correctness failure. Self-reported, not independent.
        routed = [o for o in trial["observations"] if len(o) > 4 and o[4] is not None]
        out["freshness_violations"] = sum(1 for o in routed if o[4] >= stale)
        out["routed_with_age"] = len(routed)
    for name, vals in (("schedule_to_completion_us", [o[2] - o[0] for o in steady]),
                       ("send_to_completion_us", [o[2] - o[1] for o in steady]),
                       ("schedule_to_send_us", [o[1] - o[0] for o in steady]),
                       ("generator_delay_us", trial["generator_lag_ns"])):
        vals = sorted(vals)
        if vals:
            out[name] = {p: round(percentile(vals, q) / 1000, 1)
                         for p, q in (("p50", 50), ("p95", 95), ("p99", 99))}
            out[name]["max"] = round(vals[-1] / 1000, 1)
            out[name]["p99_meaningful"] = len(vals) >= 1000
    if steady:
        span = (steady[-1][2] - steady[0][0]) / 1e9
        out["steady_throughput_rps"] = round(len(steady) / span, 1) if span > 0 else None
    after = trial["server_after"]
    out["server_cumulative"] = {"route_ns": after.get("route_ns"),
                                "process_ns": after.get("process_ns"),
                                "feed_rtt_ns": {v: s.get("feed_rtt_ns")
                                                for v, s in after.get("venues", {}).items()}}
    return out


def _git_sha():
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True,
                              text=True, timeout=2).stdout.strip() or None
    except OSError:
        return None


def main(argv=None):
    p = argparse.ArgumentParser(prog="quote_gate.bench")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=9200)
    p.add_argument("--rate", type=float, required=True)
    p.add_argument("--duration", type=float, default=10)
    p.add_argument("--warmup", type=float, default=2)
    p.add_argument("--connections", type=int, default=8)
    p.add_argument("--trials", type=int, default=3)
    p.add_argument("--side", default="buy", choices=["buy", "sell"])
    p.add_argument("--quantity", type=int, default=1)
    p.add_argument("--label", default="run")
    p.add_argument("--out", default="artifacts/bench")
    p.add_argument("--observer", default="bench@local", help="host alias, not a hostname")
    a = p.parse_args(argv)

    run_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + a.label
    d = os.path.join(a.out, run_id)
    os.makedirs(d, exist_ok=True)
    meta = {"run_id": run_id, "label": a.label, "git": _git_sha(), "python": platform.python_version(),
            "rate": a.rate, "duration_s": a.duration, "warmup_s": a.warmup,
            "connections": a.connections, "trials": a.trials, "side": a.side, "quantity": a.quantity,
            "target": f"{a.host}:{a.port}", "observer": a.observer, "raw_schema": "qg.route_obs.v2",
            "run_started_utc_ns": time.time_ns(),
            "operation": "route lookup (no venue contact, no execution)",
            "instrumentation": "client monotonic_ns per request (sched/send/recv); "
                               "server bounded ring samples, cumulative since start"}
    summaries = []
    for t in range(a.trials):
        trial = asyncio.run(run_trial(a.host, a.port, a.rate, a.duration, a.connections,
                                      a.side, a.quantity, observer=a.observer))
        with open(os.path.join(d, f"trial{t}.raw.jsonl"), "w") as f:
            f.write(json.dumps(trial["header"]) + "\n")
            for o in trial["observations"]:
                f.write(json.dumps(o) + "\n")
        s = summarize(trial, a.warmup)
        s["trial"] = t
        summaries.append(s)
        print(json.dumps(s), flush=True)
    with open(os.path.join(d, "summary.json"), "w") as f:
        json.dump({"meta": meta, "trials": summaries}, f, indent=1)
    print(json.dumps({"saved": d}))


if __name__ == "__main__":
    main()
