"""M3 correctness checks against venue B running on the second VM.

Runs venue A and the aggregator in-process on this host; venue B is remote.
Fault actions on B go through scripts/remote_venue.sh (only the task's own process).
Writes artifacts/m3/<task>/check.json. Exit status 1 if any check fails.

  python3 scripts/m3_check.py TASK
"""
import asyncio
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, ".")
from quote_gate.aggregator import Aggregator  # noqa: E402
from quote_gate.book import Book, FeedStatus  # noqa: E402
from quote_gate.router import Side, route  # noqa: E402
from quote_gate.synthetic import GeneratorConfig, generate_quotes  # noqa: E402
from quote_gate.venue import Venue  # noqa: E402

REMOTE_B = (os.environ["QG_REMOTE_BIND"], 19102)   # set via: set -a; . docs/private/remote.env
STALE_NS = 100_000_000
results = []


def check(name, ok, **detail):
    results.append({"check": name, "pass": bool(ok), **detail})
    print(("PASS " if ok else "FAIL ") + name, json.dumps(detail), flush=True)


async def remote(task, *args):
    """Run off the event loop: a blocking ssh call would stall feeds and fake staleness."""
    return await asyncio.to_thread(_remote, task, *args)


def _remote(task, *args):
    return subprocess.run(["scripts/remote_venue.sh", args[0], task, *args[1:]],
                          capture_output=True, text=True, timeout=30)


async def wait_until(cond, timeout):
    t0 = time.monotonic()
    while not cond():
        if time.monotonic() - t0 > timeout:
            return None
        await asyncio.sleep(0.001)
    return time.monotonic() - t0


def matches_generator(st, session):
    cfg = GeneratorConfig(venue_id="B", start_bid_ticks=10002)
    exp = generate_quotes(2 * 1_000_003 + session, st.last_sequence, cfg)[-1]
    q = st.quote
    return (q.bid_ticks, q.ask_ticks, q.bid_quantity, q.ask_quantity) == \
        (exp.bid_ticks, exp.ask_ticks, exp.bid_quantity, exp.ask_quantity)


async def main(task):
    va = Venue("A", seed=1, rate_hz=1000)
    pa = await va.start()
    book = Book("SYN1", ["A", "B"], STALE_NS)
    agg = Aggregator(book, {"A": ("127.0.0.1", pa), "B": REMOTE_B})
    await agg.start()
    A, B = book.venues["A"], book.venues["B"]
    now = time.monotonic_ns

    # 1. Connect, session, wire == generator, no rejects/gaps.
    t = await wait_until(lambda: B.accepted >= 500 and agg.feed_rtt_ns["B"].count >= 5, 5)
    check("remote_connect_and_stream", t is not None, seconds=t, accepted=B.accepted,
          session=agg.venue_session["B"], epoch=B.connection_epoch)
    check("remote_wire_matches_generator", B.quote is not None and matches_generator(B, agg.venue_session["B"]),
          last_sequence=B.last_sequence)
    check("remote_no_rejects_no_gaps", B.rejected == {} and B.gaps == 0, rejected=B.rejected, gaps=B.gaps)
    check("remote_status_fresh", book.status("B", now()) is FeedStatus.FRESH)

    # 2. Freshness: freeze remote venue process -> STALE, excluded; resume -> FRESH.
    r = await remote(task, "signal", "STOP")
    t = await wait_until(lambda: book.status("B", now()) is FeedStatus.STALE, 3)
    a_fresh_while_b_stale = book.status("A", now()) is FeedStatus.FRESH
    age_ms = (now() - B.received_monotonic_ns) / 1e6
    d = route(book, Side.BUY, 1, now())
    check("freeze_goes_stale", t is not None and ("B", "stale") in d.excluded and B.connection_epoch == 1,
          stale_observed_after_signal_return_s=t, b_age_at_stale_ms=age_ms, signal=r.stdout.strip(),
          routed_to=d.venue_id, note="ssh signal latency precedes this interval; threshold 100 ms")
    check("local_A_fresh_while_B_stale", a_fresh_while_b_stale and d.venue_id == "A")
    await remote(task, "signal", "CONT")
    t = await wait_until(lambda: book.status("B", now()) is FeedStatus.FRESH, 3)
    check("resume_fresh_same_session", t is not None and B.connection_epoch == 1 and B.gaps == 0,
          seconds=t, rejected=B.rejected)
    rtt_before = agg.feed_rtt_ns["B"].summary()

    # 3. Disconnect: stop remote process -> DISCONNECTED; restart -> new epoch, sequence reset.
    r = await remote(task, "stop")
    t = await wait_until(lambda: book.status("B", now()) is FeedStatus.DISCONNECTED, 3)
    d = route(book, Side.BUY, 1, now())
    check("stop_goes_disconnected", t is not None and ("B", "disconnected") in d.excluded,
          seconds=t, stop_output=r.stdout.strip(), routed_to=d.venue_id)
    await asyncio.sleep(1.5)
    errs_down = dict(agg.feed_errors["B"])
    check("reconnect_attempts_while_down", sum(errs_down.values()) >= 2, feed_errors=errs_down)
    r = await remote(task, "start", "1800")
    t = await wait_until(lambda: B.connection_epoch == 2 and B.quote is not None, 5)
    check("restart_new_epoch_and_session", t is not None and agg.venue_session["B"] == 1,
          reconnect_after_start_return_s=t, epoch=B.connection_epoch, session=agg.venue_session["B"],
          note="new process restarts session numbering at 1; backoff cap 1 s")
    await asyncio.sleep(0.5)
    check("restart_sequence_reset_and_matches_generator",
          B.last_sequence < 2000 and matches_generator(B, agg.venue_session["B"]),
          last_sequence=B.last_sequence, rejected=B.rejected)
    check("local_A_continuous", A.connection_epoch == 1 and A.rejected == {} and A.gaps == 0,
          accepted=A.accepted)

    stats = agg.stats()
    await agg.stop()
    await va.stop()
    return {"feed_rtt_B_first_session": rtt_before, "final_stats": stats}


if __name__ == "__main__":
    task = sys.argv[1]
    extra = asyncio.run(main(task))
    out = {"task": task, "checks": results, **extra}
    with open(f"artifacts/m3/{task}/check.json", "w") as f:
        json.dump(out, f, indent=1)
    failed = [r["check"] for r in results if not r["pass"]]
    print(json.dumps({"passed": len(results) - len(failed), "failed": failed}))
    sys.exit(1 if failed else 0)
