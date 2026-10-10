"""Command-line entry points.

  python3 -m quote_gate.cli venue --id A --port 9101 [--rate 1000 --seed 1 --host 127.0.0.1]
  python3 -m quote_gate.cli aggregator --venue A=127.0.0.1:9101 --venue B=127.0.0.1:9102 \
      [--port 9200 --stale-ms 100]
  python3 -m quote_gate.cli route --side buy --quantity 5 [--port 9200]
  python3 -m quote_gate.cli stats [--port 9200]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import signal

from . import protocol
from .aggregator import Aggregator
from .book import Book
from .venue import Venue


async def _serve(service, stats_every_s: float) -> None:
    await service.start()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    print(json.dumps({"listening": service.port}), flush=True)
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), stats_every_s)
        except asyncio.TimeoutError:
            print(json.dumps(service.stats()), flush=True)   # off the hot path, periodic
    await service.stop()
    print(json.dumps({"final": service.stats()}), flush=True)


async def _request(host: str, port: int, obj: dict) -> dict:
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port, limit=protocol.MAX_FRAME), 2)
    writer.write(protocol.encode(obj))
    await writer.drain()
    reply = await asyncio.wait_for(protocol.read_frame(reader), 2)
    writer.close()
    return reply


def _addr(s: str) -> tuple[str, tuple[str, int]]:
    vid, hp = s.split("=", 1)
    host, port = hp.rsplit(":", 1)
    return vid, (host, int(port))


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="quote_gate")
    sub = p.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("venue")
    v.add_argument("--id", required=True)
    v.add_argument("--host", default="127.0.0.1")
    v.add_argument("--port", type=int, required=True)
    v.add_argument("--rate", type=float, default=1000.0)
    v.add_argument("--seed", type=int, default=1)
    v.add_argument("--start-bid", type=int, default=10000)
    v.add_argument("--stats-every", type=float, default=5.0)
    v.add_argument("--market-seed", type=int, default=None,
                   help="serve the quant market_sim stream (seed) instead of the M1 generator")
    v.add_argument("--market-steps", type=int, default=0)
    v.add_argument("--beta-pct", type=int, default=50)
    v.add_argument("--fee", type=int, default=0, help="fee ticks per unit for fills")
    v.add_argument("--drop-ack-every", type=int, default=0,
                   help="FAULT INJECTION: record outcome but do not send the exec of every Nth order")
    v.add_argument("--fill-log", default=None, help="write evaluator fill log (JSONL) here on shutdown")
    a = sub.add_parser("aggregator")
    a.add_argument("--venue", action="append", type=_addr, required=True)
    a.add_argument("--host", default="127.0.0.1")
    a.add_argument("--port", type=int, default=9200)
    a.add_argument("--symbol", default="SYN1")
    a.add_argument("--stale-ms", type=float, default=100.0)
    a.add_argument("--stats-every", type=float, default=5.0)
    a.add_argument("--observer", default="aggregator@local",
                   help="observer identity recorded in artifacts (use a host alias, not a hostname)")
    a.add_argument("--artifact-dir", default=None, help="write events/feed RTT here on shutdown")
    for name in ("route", "stats"):
        c = sub.add_parser(name)
        c.add_argument("--host", default="127.0.0.1")
        c.add_argument("--port", type=int, default=9200)
        if name == "route":
            c.add_argument("--side", required=True, choices=["buy", "sell"])
            c.add_argument("--quantity", type=int, required=True)
    args = p.parse_args(argv)

    if args.cmd == "venue":
        market = None
        if args.market_seed is not None:
            from .market_sim import MarketConfig
            market = MarketConfig(seed=args.market_seed, steps=args.market_steps, beta_pct=args.beta_pct)
        svc = Venue(args.id, seed=args.seed, rate_hz=args.rate, host=args.host,
                    port=args.port, start_bid_ticks=args.start_bid, market=market,
                    fee_per_unit=args.fee)
        svc.drop_ack_every = args.drop_ack_every
        asyncio.run(_serve(svc, args.stats_every))
        if args.fill_log:
            with open(args.fill_log, "w") as f:
                f.write(json.dumps({"schema": "qg.venue_fills.v1", "venue_id": svc.venue_id,
                                    "instance": svc.instance, "orders_received": svc.orders_received,
                                    "acks_dropped_injected": svc.acks_dropped,
                                    "fill_log_dropped": svc.fill_log_dropped}) + "\n")
                for oid, outcome in svc.fill_log:
                    f.write(json.dumps([oid, outcome]) + "\n")
    elif args.cmd == "aggregator":
        venues = dict(args.venue)
        book = Book(args.symbol, venues, int(args.stale_ms * 1e6))
        asyncio.run(_serve(Aggregator(book, venues, host=args.host, port=args.port,
                                      observer=args.observer, artifact_dir=args.artifact_dir),
                           args.stats_every))
    else:
        req = {"type": "stats"} if args.cmd == "stats" else \
            {"type": "route", "side": args.side, "quantity": args.quantity}
        print(json.dumps(asyncio.run(_request(args.host, args.port, req)), indent=2))


if __name__ == "__main__":
    main()
