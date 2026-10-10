"""Bounded read-only capture of Coinbase BTC-USD level2_batch + heartbeat (contract R1).

  python3 -m quote_gate.coinbase_capture --out artifacts/realdata/<run> [--seconds 600] [--max-mib 250]

Writes `raw.jsonl` (one line per received message: session, local index, local receipt wall/mono
ns, raw text verbatim) and `manifest.json` (limits, counts, stop reason, product metadata, sha256).
No account, key or order entry. Local clocks only.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import time
import urllib.request

from .ws_client import WSClient, WSError

URL = "wss://ws-feed.exchange.coinbase.com"
PRODUCT = "BTC-USD"
SUBSCRIBE = {"type": "subscribe", "product_ids": [PRODUCT], "channels": ["level2_batch", "heartbeat"]}
META_URL = f"https://api.exchange.coinbase.com/products/{PRODUCT}"
MAX_RECONNECTS = 3


def fetch_metadata() -> dict:
    req = urllib.request.Request(META_URL, headers={"User-Agent": "quotegate-readonly/1"})
    with urllib.request.urlopen(req, timeout=10) as r:
        m = json.loads(r.read())
    return {k: m.get(k) for k in ("id", "base_currency", "quote_currency", "quote_increment",
                                  "base_increment", "status", "trading_disabled")}


async def capture(out_dir: str, seconds: float, max_bytes: int) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    raw_path = os.path.join(out_dir, "raw.jsonl")
    meta = fetch_metadata()
    started_wall, t_end = time.time_ns(), time.monotonic() + seconds
    written, index, session, reconnects = 0, 0, 0, 0
    counts: dict[str, int] = {}
    sessions = []
    stop = "duration"
    with open(raw_path, "w") as f:
        while time.monotonic() < t_end:
            session += 1
            ws = WSClient(URL)
            sess = {"session": session, "start_wall_ns": time.time_ns(), "messages": 0, "end": None}
            sessions.append(sess)
            try:
                await ws.connect()
                await ws.send_json(SUBSCRIBE)
                while time.monotonic() < t_end:
                    remaining = t_end - time.monotonic()
                    msg = await asyncio.wait_for(ws.recv(), timeout=min(30.0, max(remaining, 0.01)))
                    wall, mono = time.time_ns(), time.monotonic_ns()
                    if msg is None:
                        raise WSError("server closed")
                    line = json.dumps({"session": session, "i": index, "wall_ns": wall,
                                       "mono_ns": mono, "raw": msg}) + "\n"
                    if written + len(line) > max_bytes:
                        stop = "size_limit"
                        break
                    f.write(line)
                    written += len(line)
                    index += 1
                    sess["messages"] += 1
                    try:
                        typ = json.loads(msg).get("type", "?")
                    except ValueError:
                        typ = "unparseable"
                    counts[typ] = counts.get(typ, 0) + 1
                if stop == "size_limit":
                    sess["end"] = "size_limit"
                    break
                sess["end"] = "duration"
            except asyncio.TimeoutError:
                sess["end"] = "timeout"
            except (WSError, OSError, ConnectionError, asyncio.IncompleteReadError) as e:
                sess["end"] = f"error:{type(e).__name__}"
            finally:
                await ws.close()
            if time.monotonic() >= t_end:
                break
            if reconnects >= MAX_RECONNECTS:
                stop = "reconnect_limit"
                break
            reconnects += 1
            await asyncio.sleep(min(2 ** reconnects, 10))
    sha = hashlib.sha256()
    with open(raw_path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            sha.update(chunk)
    manifest = {"schema": "qg.coinbase_capture.v1", "source": URL, "subscribe": SUBSCRIBE,
                "product_metadata": meta, "metadata_url": META_URL,
                "limits": {"seconds": seconds, "max_bytes": max_bytes, "max_reconnects": MAX_RECONNECTS},
                "started_wall_ns": started_wall, "ended_wall_ns": time.time_ns(), "stop_reason": stop,
                "messages": index, "bytes": written, "counts_by_type": counts, "sessions": sessions,
                "reconnects": reconnects, "raw_sha256": sha.hexdigest(),
                "notes": "local receipt timestamps only; no source-to-receiver latency claimed"}
    with open(os.path.join(out_dir, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)
    return manifest


def main(argv=None):
    p = argparse.ArgumentParser(prog="quote_gate.coinbase_capture")
    p.add_argument("--out", required=True)
    p.add_argument("--seconds", type=float, default=600.0)
    p.add_argument("--max-mib", type=float, default=250.0)
    a = p.parse_args(argv)
    m = asyncio.run(capture(a.out, a.seconds, int(a.max_mib * 1024 * 1024)))
    print(json.dumps({k: m[k] for k in ("stop_reason", "messages", "bytes", "counts_by_type",
                                        "reconnects", "raw_sha256")}, indent=1))


if __name__ == "__main__":
    main()
