"""Synthetic venue feed server.

Accepts at most one subscriber connection. Each accepted connection is a new
session: it sends a hello frame, then quotes with sequence numbers restarting
at 1. Quotes are sent on an absolute schedule (offered rate); if the event
loop falls behind, sending lag is recorded rather than hidden.

Overload: if the socket write buffer exceeds ``max_write_buffer`` the scheduled
quote is not sent and ``coalesced`` is incremented. The subscriber then sees a
sequence gap. Coalescing is latest-wins at the source; coalesced quotes are
never counted as sent.

Orders (contract O1, docs/protocol.md): orders arrive on the same connection and are matched
against the venue's own current quote with the shared rules in ``execution.match``. Outcomes are
recorded in a bounded in-memory ``OrderStore`` before the exec frame is written; exec/status frames
are never coalesced. ``instance`` is random per process: a restart loses the store.
"""
from __future__ import annotations

import asyncio
import secrets
import time

from . import protocol
from .execution import DEFAULT_STORE_CAPACITY, OrderStore, match
from .market_sim import MarketConfig, generate
from .metrics import Samples
from .router import Side
from .synthetic import MAX_EVENTS, GeneratorConfig, iter_quotes

ORDER_FIELDS = ("client_order_id", "symbol", "venue_id", "instance", "session", "side", "quantity",
                "limit_ticks")


class Venue:
    def __init__(self, venue_id: str, symbol: str = "SYN1", seed: int = 1,
                 rate_hz: float = 1000.0, host: str = "127.0.0.1", port: int = 0,
                 start_bid_ticks: int = 10000, max_write_buffer: int = 256 * 1024,
                 market: MarketConfig | None = None, fee_per_unit: int = 0,
                 store_capacity: int = DEFAULT_STORE_CAPACITY):
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
        self.market = market
        self.fee_per_unit = fee_per_unit
        self.instance = secrets.token_hex(8)
        self.store = OrderStore(store_capacity)
        self.quote = None
        self.remaining = {Side.BUY: 0, Side.SELL: 0}
        self.orders_executed = 0
        # Evaluator-only fill log (not part of the protocol; never sent to clients). Bounded.
        self.fill_log: list[tuple[str, dict]] = []
        self.fill_log_capacity = 1_000_000
        self.fill_log_dropped = 0
        self.bad_orders = 0
        self.drop_next_acks = 0             # fault: record outcome, do not send exec
        self.drop_ack_every = 0             # fault: drop the exec of every Nth stored order
        self.orders_received = 0
        self.acks_dropped = 0
        self.disconnect_after_exec = 0      # fault: record outcome, then drop the connection
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

    def restart_lose_store(self) -> None:
        """Fault: simulate a venue process restart (new instance, empty order store)."""
        self.instance = secrets.token_hex(8)
        self.store = OrderStore(self.store.capacity)
        self.drop_connection()

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
        if self.market is not None:
            idx = 0 if self.venue_id == "A" else 1
            events = (st.quotes[idx] for st in generate(self.market))
        else:
            cfg = GeneratorConfig(venue_id=self.venue_id, symbol=self.symbol,
                                  start_bid_ticks=self.start_bid_ticks)
            events = iter_quotes(self.seed * 1_000_003 + self.session, MAX_EVENTS, cfg)
        control = asyncio.create_task(self._read_control(reader, writer))
        try:
            writer.write(protocol.encode({"type": "hello", "venue_id": self.venue_id,
                                          "symbol": self.symbol, "session": self.session,
                                          "instance": self.instance}))
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
                # The venue's own book advances even if the feed is paused or coalesced.
                self.quote = ev
                self.remaining = {Side.BUY: ev.ask_quantity, Side.SELL: ev.bid_quantity}
                if self.paused:
                    continue
                if writer.transport.get_write_buffer_size() > self.max_write_buffer:
                    self.coalesced += 1
                    continue
                writer.write(protocol.encode_quote(ev))
                self.sent += 1
            if self.market is not None:
                # Finite experiment stream ended: keep the session open (no replay) until closed.
                while not writer.is_closing():
                    await asyncio.sleep(0.05)
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
                kind = obj.get("type")
                if kind == "order":
                    self._on_order(obj, writer)
                    continue
                if kind == "order_status":
                    self._on_status(obj, writer)
                    continue
                if kind == "release":
                    self.store.release(obj.get("client_order_id"))
                    continue
                if kind == "ping":
                    if writer.transport.get_write_buffer_size() > self.max_write_buffer:
                        self.pongs_dropped += 1
                        continue
                    writer.write(protocol.encode({"type": "pong", "id": obj.get("id")}))
                    self.pongs += 1
        except (protocol.FrameError, ConnectionError, OSError):
            pass
        writer.transport.abort()

    def _exec_frame(self, oid, outcome) -> dict:
        return {"type": "exec", "client_order_id": oid, "venue_id": self.venue_id,
                "instance": self.instance, **outcome}

    def _valid_order(self, obj) -> bool:
        if set(obj) != set(ORDER_FIELDS) | {"type"}:
            return False
        oid = obj["client_order_id"]
        return (isinstance(oid, str) and 1 <= len(oid) <= 64 and obj["symbol"] == self.symbol
                and obj["venue_id"] == self.venue_id and obj["instance"] == self.instance
                and type(obj["session"]) is int and obj["side"] in ("buy", "sell")
                and type(obj["quantity"]) is int and obj["quantity"] >= 1
                and type(obj["limit_ticks"]) is int and obj["limit_ticks"] >= 1)

    def _on_order(self, obj, writer) -> None:
        oid = obj.get("client_order_id")
        if not self._valid_order(obj):
            self.bad_orders += 1
            outcome = {"session": self.session, "status": "REJECTED", "reason": "bad_order",
                       "fill_price_ticks": None, "quantity": 0, "fee_ticks": 0, "quote_sequence": None}
            writer.write(protocol.encode(self._exec_frame(oid if isinstance(oid, str) else None, outcome)))
            return
        payload = {k: obj[k] for k in ORDER_FIELDS if k != "session"}
        prior = self.store.get(oid)
        if prior is not None:
            outcome = prior[1] if prior[0] == payload else {
                "session": self.session, "status": "REJECTED", "reason": "conflicting_order_id",
                "fill_price_ticks": None, "quantity": 0, "fee_ticks": 0, "quote_sequence": None}
            writer.write(protocol.encode(self._exec_frame(oid, outcome)))
            return
        if obj["session"] != self.session:
            status, price, reason = "REJECTED", None, "session"
        elif not self.store.has_room():
            outcome = {"session": self.session, "status": "REJECTED", "reason": "store_full",
                       "fill_price_ticks": None, "quantity": 0, "fee_ticks": 0, "quote_sequence": None}
            writer.write(protocol.encode(self._exec_frame(oid, outcome)))   # not stored, not executed
            return
        else:
            side = Side(obj["side"])
            status, price, reason = match(side, obj["quantity"], obj["limit_ticks"], self.quote, self.remaining)
            if status == "FILLED":
                self.remaining[side] -= obj["quantity"]
                self.orders_executed += 1
        filled = status == "FILLED"
        outcome = {"session": self.session, "status": status, "reason": reason,
                   "fill_price_ticks": price, "quantity": obj["quantity"] if filled else 0,
                   "fee_ticks": self.fee_per_unit * obj["quantity"] if filled else 0,
                   "quote_sequence": self.quote.sequence if self.quote is not None else None}
        if not self.store.has_room():           # session-rejections also need a slot
            writer.write(protocol.encode(self._exec_frame(oid, {**outcome, "reason": "store_full",
                                                                "status": "REJECTED"})))
            return
        self.store.put(oid, payload, outcome)    # record BEFORE acknowledging
        self.orders_received += 1
        if filled:
            if len(self.fill_log) < self.fill_log_capacity:
                self.fill_log.append((oid, outcome))
            else:
                self.fill_log_dropped += 1
        if self.drop_next_acks > 0 or (self.drop_ack_every and self.orders_received % self.drop_ack_every == 0):
            self.drop_next_acks = max(0, self.drop_next_acks - 1)
            self.acks_dropped += 1
            return
        if self.disconnect_after_exec > 0:
            self.disconnect_after_exec -= 1
            writer.transport.abort()
            return
        writer.write(protocol.encode(self._exec_frame(oid, outcome)))

    def _on_status(self, obj, writer) -> None:
        oid = obj.get("client_order_id")
        rec = self.store.get(oid)
        frame = {"type": "status", "client_order_id": oid, "venue_id": self.venue_id,
                 "instance": self.instance, "found": rec is not None}
        if rec is not None:
            frame.update(rec[1])
        writer.write(protocol.encode(frame))

    def stats(self) -> dict:
        return {"venue_id": self.venue_id, "session": self.session, "sent": self.sent,
                "coalesced": self.coalesced, "refused_connections": self.refused_connections,
                "pongs": self.pongs, "pongs_dropped": self.pongs_dropped,
                "paused": self.paused, "send_lag_ns": self.send_lag_ns.summary(),
                "instance": self.instance, "orders_executed": self.orders_executed,
                "bad_orders": self.bad_orders, "store_records": len(self.store),
                "store_unreleased": self.store.unreleased(), "store_evicted": self.store.evicted,
                "store_rejected_full": self.store.rejected_full}
