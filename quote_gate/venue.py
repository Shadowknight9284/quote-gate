"""Synthetic venue feed server.

Accepts at most one subscriber connection. Each accepted connection is a new
session: it sends a hello frame, then quotes with sequence numbers restarting
at 1. Quotes are sent on an absolute schedule (offered rate); if the event
loop falls behind, sending lag is recorded rather than hidden.

Overload: if the socket write buffer exceeds ``max_write_buffer`` the scheduled
quote is not sent and ``coalesced`` is incremented. The subscriber then sees a
sequence gap. Coalescing is latest-wins at the source; coalesced quotes are
never counted as sent.
"""
from __future__ import annotations

import asyncio
import time

from . import protocol
from .metrics import Samples
from .synthetic import MAX_EVENTS, GeneratorConfig, iter_quotes


class Venue:
    def __init__(self, venue_id: str, symbol: str = "SYN1", seed: int = 1,
                 rate_hz: float = 1000.0, host: str = "127.0.0.1", port: int = 0,
                 start_bid_ticks: int = 10000, max_write_buffer: int = 256 * 1024):
        if rate_hz <= 0:
            raise ValueError("rate_hz must be positive")
        self.venue_id, self.symbol, self.seed = venue_id, symbol, seed
        self.interval_ns = int(1e9 / rate_hz)
        self.host, self.port = host, port
        self.start_bid_ticks = start_bid_ticks
        self.max_write_buffer = max_write_buffer
        self.session = 0
        self.paused = False
        self.sent = 0
        self.coalesced = 0
        self.refused_connections = 0
        self.pongs = 0
        self.pongs_dropped = 0
        self.send_lag_ns = Samples(50_000)
        self._server: asyncio.Server | None = None
        self._writer: asyncio.StreamWriter | None = None

    async def start(self) -> int:
        self._server = await asyncio.start_server(self._handle, self.host, self.port,
                                                  limit=protocol.MAX_FRAME)
        self.port = self._server.sockets[0].getsockname()[1]
        return self.port

    async def stop(self) -> None:
        self.drop_connection()
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    # --- fault injection (labeled; used by tests and fault scenarios) ---
    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.paused = False

    def drop_connection(self) -> None:
        if self._writer is not None:
            self._writer.transport.abort()

    def inject_raw(self, data: bytes) -> None:
        if self._writer is not None:
            self._writer.write(data)

    # ---
    async def _handle(self, reader, writer):
        if self._writer is not None:
            self.refused_connections += 1
            writer.transport.abort()
            return
        self._writer = writer
        self.session += 1
        cfg = GeneratorConfig(venue_id=self.venue_id, symbol=self.symbol,
                              start_bid_ticks=self.start_bid_ticks)
        events = iter_quotes(self.seed * 1_000_003 + self.session, MAX_EVENTS, cfg)
        control = asyncio.create_task(self._read_control(reader, writer))
        try:
            writer.write(protocol.encode({"type": "hello", "venue_id": self.venue_id,
                                          "symbol": self.symbol, "session": self.session}))
            next_t = time.monotonic_ns()
            for ev in events:
                now = time.monotonic_ns()
                if next_t > now:
                    await asyncio.sleep((next_t - now) / 1e9)
                elif self.sent % 64 == 0:
                    await asyncio.sleep(0)   # let other tasks run when behind schedule
                if writer.is_closing():
                    break
                self.send_lag_ns.add(time.monotonic_ns() - next_t)
                next_t += self.interval_ns
                if self.paused:
                    continue
                if writer.transport.get_write_buffer_size() > self.max_write_buffer:
                    self.coalesced += 1
                    continue
                writer.write(protocol.encode_quote(ev))
                self.sent += 1
        except (ConnectionError, OSError):
            pass
        finally:
            control.cancel()
            if self._writer is writer:
                self._writer = None
            writer.transport.abort()

    async def _read_control(self, reader, writer) -> None:
        """Answer pings in-stream (pong is queued behind pending quotes); EOF ends the session."""
        try:
            while True:
                obj = await protocol.read_frame(reader)
                if obj is None:
                    break
                if obj.get("type") == "ping":
                    if writer.transport.get_write_buffer_size() > self.max_write_buffer:
                        self.pongs_dropped += 1
                        continue
                    writer.write(protocol.encode({"type": "pong", "id": obj.get("id")}))
                    self.pongs += 1
        except (protocol.FrameError, ConnectionError, OSError):
            pass
        writer.transport.abort()

    def stats(self) -> dict:
        return {"venue_id": self.venue_id, "session": self.session, "sent": self.sent,
                "coalesced": self.coalesced, "refused_connections": self.refused_connections,
                "pongs": self.pongs, "pongs_dropped": self.pongs_dropped,
                "paused": self.paused, "send_lag_ns": self.send_lag_ns.summary()}
