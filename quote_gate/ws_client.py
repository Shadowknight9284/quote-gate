"""Minimal read-only RFC 6455 WebSocket client over TLS (standard library only).

Scope: one client connection, text messages, ping/pong, close, fragmentation; client frames are
masked as required. Bounded: a message larger than `max_message` raises WSError. Not a general
WebSocket library; used only to read a public market-data feed.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import ssl
import struct
from urllib.parse import urlparse

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class WSError(Exception):
    pass


class WSClient:
    def __init__(self, url: str, max_message: int = 32 * 1024 * 1024, timeout_s: float = 10.0):
        u = urlparse(url)
        if u.scheme != "wss":
            raise ValueError("wss:// only")
        self.host, self.port, self.path = u.hostname, u.port or 443, u.path or "/"
        self.max_message, self.timeout_s = max_message, timeout_s
        self.reader = self.writer = None

    async def connect(self) -> None:
        ctx = ssl.create_default_context()          # certificate and hostname verification on
        self.reader, self.writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port, ssl=ctx, server_hostname=self.host,
                                    limit=1 << 20), self.timeout_s)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET {self.path} HTTP/1.1\r\nHost: {self.host}\r\nUpgrade: websocket\r\n"
               f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
               f"User-Agent: quotegate-readonly/1\r\n\r\n")
        self.writer.write(req.encode())
        await self.writer.drain()
        head = await asyncio.wait_for(self.reader.readuntil(b"\r\n\r\n"), self.timeout_s)
        lines = head.decode("latin-1").split("\r\n")
        if not lines[0].startswith("HTTP/1.1 101"):
            raise WSError(f"handshake failed: {lines[0][:80]}")
        hdrs = {k.strip().lower(): v.strip() for k, _, v in (l.partition(":") for l in lines[1:] if l)}
        expect = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
        if hdrs.get("sec-websocket-accept") != expect:
            raise WSError("handshake: bad Sec-WebSocket-Accept")

    def _frame(self, opcode: int, payload: bytes) -> bytes:
        mask = os.urandom(4)
        n = len(payload)
        hdr = bytes([0x80 | opcode])
        if n < 126:
            hdr += bytes([0x80 | n])
        elif n < 65536:
            hdr += bytes([0x80 | 126]) + struct.pack("!H", n)
        else:
            hdr += bytes([0x80 | 127]) + struct.pack("!Q", n)
        return hdr + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload))

    async def send_text(self, text: str) -> None:
        self.writer.write(self._frame(0x1, text.encode()))
        await self.writer.drain()

    async def send_json(self, obj) -> None:
        await self.send_text(json.dumps(obj))

    async def recv(self) -> str | None:
        """Next complete text message; None on close."""
        buf, opcode0 = bytearray(), None
        while True:
            b0, b1 = await self.reader.readexactly(2)
            fin, op, masked, n = b0 & 0x80, b0 & 0x0F, b1 & 0x80, b1 & 0x7F
            if n == 126:
                n = struct.unpack("!H", await self.reader.readexactly(2))[0]
            elif n == 127:
                n = struct.unpack("!Q", await self.reader.readexactly(8))[0]
            if masked:
                raise WSError("server frames must not be masked")
            if n > self.max_message or len(buf) + n > self.max_message:
                raise WSError("message exceeds bound")
            payload = await self.reader.readexactly(n)
            if op == 0x9:                       # ping -> pong
                self.writer.write(self._frame(0xA, payload))
                continue
            if op == 0xA:
                continue
            if op == 0x8:
                return None
            if op in (0x1, 0x2):
                opcode0, buf = op, bytearray(payload)
            elif op == 0x0 and opcode0 is not None:
                buf += payload
            else:
                raise WSError(f"unexpected opcode {op}")
            if fin:
                if opcode0 != 0x1:
                    raise WSError("binary message not expected")
                return buf.decode("utf-8")

    async def close(self) -> None:
        if self.writer is not None:
            try:
                self.writer.write(self._frame(0x8, struct.pack("!H", 1000)))
                await asyncio.wait_for(self.writer.drain(), 2)
            except (OSError, asyncio.TimeoutError):
                pass
            self.writer.transport.abort()
