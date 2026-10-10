"""Aggregator/router service.

Feed side: one persistent outbound TCP connection per venue. Each successful
hello starts a new connection epoch in the Book; any frame error, EOF, timeout
or socket error ends the epoch (venue becomes DISCONNECTED) and reconnects
with exponential backoff.

Client side: a separate listening port for route and stats requests, one
request in flight per client connection, at most ``max_clients`` connections.

Feed round trip: every PING_INTERVAL_S the aggregator sends a ping on each
feed connection (at most one outstanding; unanswered after PING_TIMEOUT_S is
counted ``ping_timeout``). ``feed_rtt_ns`` = aggregator monotonic time from just
before writing the ping to just after reading the matching pong. Both stamps
are on the aggregator host. The pong is queued behind quotes the venue already
buffered, so this is feed-path RTT, not bare network RTT.

Venue events (bounded ring, EVENT_CAPACITY; overwritten entries counted):
``connect`` / ``disconnect`` from the feed task, and ``status`` transitions seen
by a freshness monitor that polls every MONITOR_TICK_S. For a FRESH->STALE
transition, ``detect_delay_ns`` = monitor observation time - (quote receipt +
stale_after). The monitor is observability only: routing evaluates freshness
exactly at query time and does not depend on it.

Receipt time is taken when the frame has been read from the asyncio buffer;
time spent in the kernel or the stream buffer before that is not included.
"""
from __future__ import annotations

import asyncio
import json
import os
import time

from . import protocol
from .book import Book
from .book import FeedStatus
from .metrics import Anchor, Ring, Samples
from .router import Side, route

BACKOFF_INITIAL_S = 0.05
BACKOFF_MAX_S = 1.0
CONNECT_TIMEOUT_S = 1.0
HELLO_TIMEOUT_S = 1.0
CLIENT_IDLE_TIMEOUT_S = 30.0
PING_INTERVAL_S = 0.1
PING_TIMEOUT_S = 1.0
MONITOR_TICK_S = 0.001
EVENT_CAPACITY = 10_000
FEED_RTT_CAPACITY = 20_000


class Aggregator:
    def __init__(self, book: Book, venue_addrs: dict[str, tuple[str, int]],
                 host: str = "127.0.0.1", port: int = 0, max_clients: int = 16,
                 sample_capacity: int = 200_000, observer: str = "aggregator",
                 artifact_dir: str | None = None):
        self.book = book
        self.venue_addrs = venue_addrs
        self.host, self.port = host, port
        self.max_clients = max_clients
        self.process_ns = Samples(sample_capacity)    # receipt -> state updated
        self.route_ns = Samples(sample_capacity)      # route computation only
        self.connects = {v: 0 for v in venue_addrs}
        self.venue_session = {v: None for v in venue_addrs}
        # Live connection handles: vid -> {"writer", "epoch", "session", "instance"} (None if down).
        self.conn = {v: None for v in venue_addrs}
        # Optional order-path hooks (quote_gate.live). All run on the single feed reader task.
        self.order_handler = None          # (vid, frame) for exec/status frames
        self.quote_listener = None         # (vid, quote, receipt_ns) after an ACCEPTED quote
        self.disconnect_listener = None    # (vid, epoch) when a connection ends
        self.feed_rtt_ns = {v: Samples(FEED_RTT_CAPACITY) for v in venue_addrs}
        self.anchor = Anchor(observer)
        self.artifact_dir = artifact_dir
        self.events = Ring(EVENT_CAPACITY)            # (mono_ns, venue, kind, detail dict)
        self.feed_rtt_log = Ring(FEED_RTT_CAPACITY * len(venue_addrs))  # (mono_ns, venue, rtt_ns)
        self.stale_detect_ns = Samples(EVENT_CAPACITY)
        self.feed_errors = {v: {} for v in venue_addrs}
        self.clients = 0
        self._client_writers: set = set()
        self.refused_clients = 0
        self.requests = 0
        self.bad_requests = 0
        self._tasks: list[asyncio.Task] = []
        self._server: asyncio.Server | None = None

    async def start(self) -> int:
        for vid in self.venue_addrs:
            self._tasks.append(asyncio.create_task(self._feed(vid), name=f"feed-{vid}"))
        self._tasks.append(asyncio.create_task(self._monitor(), name="freshness-monitor"))
        self._server = await asyncio.start_server(self._client, self.host, self.port,
                                                  limit=protocol.MAX_FRAME)
        self.port = self._server.sockets[0].getsockname()[1]
        return self.port

    async def stop(self) -> None:
        """Stop accepting clients, close client connections, cancel feeds.

        In-flight route replies are abandoned. Client connections are aborted explicitly because
        Server.wait_closed() waits for every active connection (Python 3.12); the wait is bounded.
        """
        if self._server:
            self._server.close()
        for w in list(self._client_writers):
            w.transport.abort()
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._server:
            try:
                await asyncio.wait_for(self._server.wait_closed(), 2.0)
            except asyncio.TimeoutError:
                self._count_stop_timeout = True
        if self.artifact_dir:
            self.dump_artifacts(self.artifact_dir)

    def dump_artifacts(self, d: str) -> None:
        """Write events and feed RTT samples (control path; called at shutdown)."""
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "venue_events.jsonl"), "w") as f:
            f.write(json.dumps(self.anchor.header(
                "qg.venue_events.v1", ["mono_ns", "venue_id", "kind", "detail"]) |
                {"retained": len(self.events.items()), "dropped": self.events.dropped}) + "\n")
            for e in self.events.items():
                f.write(json.dumps(list(e)) + "\n")
        with open(os.path.join(d, "feed_rtt.jsonl"), "w") as f:
            f.write(json.dumps(self.anchor.header(
                "qg.feed_rtt.v1", ["completed_mono_ns", "venue_id", "rtt_ns"]) |
                {"retained": len(self.feed_rtt_log.items()), "dropped": self.feed_rtt_log.dropped,
                 "semantics": "aggregator-host monotonic: before ping write -> pong read; "
                              "application-level feed connection RTT, not quote age"}) + "\n")
            for e in self.feed_rtt_log.items():
                f.write(json.dumps(list(e)) + "\n")

    async def _monitor(self) -> None:
        book = self.book
        last = {v: None for v in book.venues}
        while True:
            await asyncio.sleep(MONITOR_TICK_S)
            now = time.monotonic_ns()
            for vid, st in book.venues.items():
                status = st.status(now, book.stale_after_ns)
                if status is last[vid]:
                    continue
                detail = {"from": last[vid].value if last[vid] else None, "to": status.value,
                          "epoch": st.connection_epoch}
                if st.quote is not None:
                    detail["quote_age_ns"] = now - st.received_monotonic_ns
                if status is FeedStatus.STALE:
                    d = now - (st.received_monotonic_ns + book.stale_after_ns)
                    detail["detect_delay_ns"] = d
                    self.stale_detect_ns.add(d)
                self.events.add((now, vid, "status", detail))
                last[vid] = status

    def _count_error(self, vid: str, kind: str) -> None:
        d = self.feed_errors[vid]
        d[kind] = d.get(kind, 0) + 1

    async def _feed(self, vid: str) -> None:
        host, port = self.venue_addrs[vid]
        backoff = BACKOFF_INITIAL_S
        book = self.book
        while True:
            writer = None
            epoch = None
            pinger = None
            ping = {"id": 0, "sent": None}
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(host, port, limit=protocol.MAX_FRAME), CONNECT_TIMEOUT_S)
                hello = await asyncio.wait_for(protocol.read_frame(reader), HELLO_TIMEOUT_S)
                if not hello or hello.get("type") != "hello" or hello.get("venue_id") != vid:
                    raise protocol.FrameError("bad hello")
                epoch = book.on_connect(vid)
                self.connects[vid] += 1
                self.venue_session[vid] = hello.get("session")
                self.conn[vid] = {"writer": writer, "epoch": epoch, "session": hello.get("session"),
                                  "instance": hello.get("instance")}
                self.events.add((time.monotonic_ns(), vid, "connect",
                                 {"epoch": epoch, "session": hello.get("session")}))
                backoff = BACKOFF_INITIAL_S
                pinger = asyncio.create_task(self._pinger(vid, writer, ping))
                while True:
                    obj = await protocol.read_frame(reader)
                    if obj is None:
                        raise ConnectionError("eof")
                    t0 = time.monotonic_ns()
                    if obj.get("type") == "pong":
                        if ping["sent"] is not None and obj.get("id") == ping["id"]:
                            self.feed_rtt_ns[vid].add(t0 - ping["sent"])
                            self.feed_rtt_log.add((t0, vid, t0 - ping["sent"]))
                            ping["sent"] = None
                        else:
                            self._count_error(vid, "unmatched_pong")
                        continue
                    if obj.get("type") in ("exec", "status"):
                        if self.order_handler is not None:
                            self.order_handler(vid, obj)
                        else:
                            self._count_error(vid, "unexpected_order_frame")
                        continue
                    q = protocol.quote_from_obj(obj)
                    if q is None:
                        self._count_error(vid, "bad_shape")
                        continue
                    rejected = book.on_quote(vid, epoch, q, t0)
                    self.process_ns.add(time.monotonic_ns() - t0)
                    if rejected is None and self.quote_listener is not None:
                        self.quote_listener(vid, q, t0)
            except asyncio.CancelledError:
                raise
            except (protocol.FrameError, ConnectionError, OSError, asyncio.TimeoutError) as e:
                self._count_error(vid, type(e).__name__)
                if epoch is not None:
                    self.events.add((time.monotonic_ns(), vid, "disconnect",
                                     {"epoch": epoch, "reason": type(e).__name__}))
            finally:
                if pinger is not None:
                    pinger.cancel()
                self.conn[vid] = None
                if epoch is not None:
                    book.on_disconnect(vid, epoch)
                    if self.disconnect_listener is not None:
                        self.disconnect_listener(vid, epoch)
                if writer is not None:
                    writer.transport.abort()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX_S)

    async def _pinger(self, vid: str, writer, ping: dict) -> None:
        while True:
            await asyncio.sleep(PING_INTERVAL_S)
            if ping["sent"] is not None:
                if time.monotonic_ns() - ping["sent"] < PING_TIMEOUT_S * 1e9:
                    continue
                self._count_error(vid, "ping_timeout")
            ping["id"] += 1
            ping["sent"] = time.monotonic_ns()
            writer.write(protocol.encode({"type": "ping", "id": ping["id"]}))

    async def _client(self, reader, writer) -> None:
        if self.clients >= self.max_clients:
            self.refused_clients += 1
            writer.transport.abort()
            return
        self.clients += 1
        self._client_writers.add(writer)
        try:
            while True:
                obj = await asyncio.wait_for(protocol.read_frame(reader), CLIENT_IDLE_TIMEOUT_S)
                if obj is None:
                    break
                writer.write(protocol.encode(self._handle_request(obj)))
                await writer.drain()
                # Fairness: a client with many buffered requests must not starve the feed readers
                # (read_frame and drain return without yielding while data/buffer space is available).
                await asyncio.sleep(0)
        except (protocol.FrameError, ConnectionError, OSError, asyncio.TimeoutError):
            pass
        finally:
            self.clients -= 1
            self._client_writers.discard(writer)
            writer.transport.abort()

    def _handle_request(self, obj: dict) -> dict:
        self.requests += 1
        rid = obj.get("id")
        kind = obj.get("type")
        if kind == "route":
            try:
                side = Side(obj.get("side"))
                t0 = time.monotonic_ns()
                d = route(self.book, side, obj.get("quantity"), t0)
                self.route_ns.add(time.monotonic_ns() - t0)
                age = None
                if d.venue_id is not None:
                    age = t0 - self.book.venues[d.venue_id].received_monotonic_ns
            except ValueError as e:
                self.bad_requests += 1
                return {"type": "error", "id": rid, "error": str(e)[:200]}
            return {"type": "route_result", "id": rid, "venue_id": d.venue_id,
                    "price_ticks": d.price_ticks, "quote_sequence": d.quote_sequence,
                    "connection_epoch": d.connection_epoch, "quote_age_ns": age,
                    "excluded": [list(x) for x in d.excluded]}
        if kind == "stats":
            return {"type": "stats", "id": rid, **self.stats()}
        self.bad_requests += 1
        return {"type": "error", "id": rid, "error": "unknown request type"}

    def stats(self) -> dict:
        now = time.monotonic_ns()
        venues = {}
        for vid, st in self.book.venues.items():
            venues[vid] = {"status": st.status(now, self.book.stale_after_ns).value,
                           "epoch": st.connection_epoch, "last_sequence": st.last_sequence,
                           "accepted": st.accepted, "rejected": dict(st.rejected),
                           "gaps": st.gaps, "connects": self.connects.get(vid, 0),
                           "venue_session": self.venue_session.get(vid),
                           "feed_rtt_ns": self.feed_rtt_ns[vid].summary(),
                           "feed_errors": dict(self.feed_errors.get(vid, {}))}
        return {"venues": venues, "process_ns": self.process_ns.summary(),
                "route_ns": self.route_ns.summary(), "requests": self.requests,
                "stale_after_ns": self.book.stale_after_ns,
                "stale_detect_ns": self.stale_detect_ns.summary(),
                "events": {"count": self.events.count, "dropped": self.events.dropped},
                "bad_requests": self.bad_requests, "clients": self.clients,
                "refused_clients": self.refused_clients}
