"""WebSocket client tests with in-memory byte streams (no network)."""
import asyncio
import base64
import hashlib
import ssl
import struct
import unittest
from unittest import mock

from quote_gate.ws_client import GUID, WSClient, WSError


def frame(op, payload=b"", fin=True, rsv=0, mask=False, length_override=None):
    b0 = (0x80 if fin else 0) | rsv | op
    n = len(payload) if length_override is None else length_override
    if n < 126:
        hdr = bytes([b0, (0x80 if mask else 0) | n])
    elif n < 65536:
        hdr = bytes([b0, (0x80 if mask else 0) | 126]) + struct.pack("!H", n)
    else:
        hdr = bytes([b0, (0x80 if mask else 0) | 127]) + struct.pack("!Q", n)
    return hdr + (b"\0\0\0\0" if mask else b"") + payload


class FakeWriter:
    def __init__(self):
        self.data = bytearray()

    def write(self, b):
        self.data += b

    async def drain(self):
        pass


def client_with(chunks, max_message=1024):
    c = WSClient("wss://example.invalid/", max_message=max_message)
    r = asyncio.StreamReader()
    for ch in chunks:
        r.feed_data(ch)
    r.feed_eof()
    c.reader, c.writer = r, FakeWriter()
    return c


def split(b, k=3):
    return [b[i:i + k] for i in range(0, len(b), k)]          # partial reads


class Frames(unittest.IsolatedAsyncioTestCase):
    async def test_text_message_with_partial_reads(self):
        c = client_with(split(frame(1, b'{"type":"heartbeat"}')))
        self.assertEqual(await c.recv(), '{"type":"heartbeat"}')

    async def test_extended_lengths(self):
        for n in (126, 70000):
            c = client_with([frame(1, b"a" * n)], max_message=1 << 20)
            self.assertEqual(len(await c.recv()), n)

    async def test_fragmented_with_interleaved_ping(self):
        c = client_with([frame(1, b"hel", fin=False), frame(9, b"p1"), frame(0, b"lo", fin=False),
                         frame(0, b"!")])
        self.assertEqual(await c.recv(), "hello!")
        # pong was written, masked, echoing the ping payload
        self.assertEqual(c.writer.data[0], 0x8A)
        mask = c.writer.data[2:6]
        self.assertEqual(bytes(b ^ mask[i % 4] for i, b in enumerate(c.writer.data[6:8])), b"p1")

    async def test_pong_ignored_and_close_returns_none(self):
        c = client_with([frame(0xA, b"x"), frame(8, struct.pack("!H", 1000))])
        self.assertIsNone(await c.recv())

    async def test_explicit_rejections(self):
        cases = {
            "rsv": [frame(1, b"x", rsv=0x40)],
            "masked": [frame(1, b"x", mask=True)],
            "binary": [frame(2, b"x")],
            "new data frame mid-fragment": [frame(1, b"a", fin=False), frame(1, b"b")],
            "continuation without start": [frame(0, b"a")],
            "fragmented control": [frame(9, b"p", fin=False)],
            "oversize control": [frame(9, b"p" * 126)],
            "unknown data opcode": [frame(3, b"x")],
            "unknown control opcode": [frame(0xB, b"")],
            "invalid utf-8": [frame(1, b"\xff\xfe")],
        }
        for name, chunks in cases.items():
            with self.subTest(name):
                with self.assertRaises(WSError):
                    await client_with(chunks).recv()

    async def test_size_bound_before_reading_payload(self):
        c = client_with([frame(1, b"", length_override=2048)], max_message=1024)
        with self.assertRaises(WSError):
            await c.recv()
        c = client_with([frame(1, b"a" * 600, fin=False), frame(0, b"b" * 600)], max_message=1024)
        with self.assertRaises(WSError):                    # bound applies to the whole message
            await c.recv()

    async def test_truncated_stream_raises(self):
        c = client_with([frame(1, b"abcdef")[:-2]])
        with self.assertRaises(asyncio.IncompleteReadError):
            await c.recv()


class Handshake(unittest.IsolatedAsyncioTestCase):
    def response(self, key, status="101 Switching Protocols", accept=None, extra=""):
        acc = accept if accept is not None else base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
        return (f"HTTP/1.1 {status}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {acc}\r\n{extra}\r\n").encode()

    async def run_hs(self, resp, key="dGhlIHNhbXBsZSBub25jZQ=="):
        c = client_with([resp])
        await c._handshake(key)
        return c

    async def test_valid_handshake(self):
        c = await self.run_hs(self.response("dGhlIHNhbXBsZSBub25jZQ=="))
        self.assertIn(b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==", c.writer.data)

    async def test_rejections(self):
        k = "dGhlIHNhbXBsZSBub25jZQ=="
        for resp in (self.response(k, status="200 OK"), self.response(k, accept="wrong"),
                     self.response(k, extra="Sec-WebSocket-Extensions: permessage-deflate\r\n")):
            with self.subTest(resp=resp[:40]), self.assertRaises(WSError):
                await self.run_hs(resp)

    def test_tls_context_verifies(self):
        ctx = WSClient.tls_context()
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(ctx.check_hostname)

    async def test_connect_passes_tls_context_and_hostname(self):
        seen = {}

        async def fake_open(host, port, ssl=None, server_hostname=None, limit=None):
            seen.update(host=host, port=port, ssl=ssl, sni=server_hostname)
            raise OSError("stop here")
        with mock.patch("asyncio.open_connection", fake_open):
            with self.assertRaises(OSError):
                await WSClient("wss://ws-feed.exchange.coinbase.com").connect()
        self.assertEqual((seen["host"], seen["port"], seen["sni"]),
                         ("ws-feed.exchange.coinbase.com", 443, "ws-feed.exchange.coinbase.com"))
        self.assertEqual(seen["ssl"].verify_mode, ssl.CERT_REQUIRED)

    def test_only_wss(self):
        with self.assertRaises(ValueError):
            WSClient("ws://example.invalid/")


if __name__ == "__main__":
    unittest.main()
