"""Focused checks for concerns left unverified by audit A1: rapid reconnects and ping bookkeeping."""
import asyncio
import time
import unittest

from quote_gate import aggregator as agg_mod
from quote_gate import protocol
from quote_gate.aggregator import Aggregator
from quote_gate.book import Book
from quote_gate.venue import Venue


async def wait_until(cond, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met before timeout")
        await asyncio.sleep(0.005)


def task_names():
    return sorted(t.get_name() for t in asyncio.all_tasks() if not t.done())


class RapidReconnect(unittest.IsolatedAsyncioTestCase):
    async def test_epochs_monotonic_no_leaked_tasks(self):
        v = Venue("A", rate_hz=500)
        port = await v.start()
        book = Book("SYN1", ["A"], 50_000_000)
        agg = Aggregator(book, {"A": ("127.0.0.1", port)})
        await agg.start()
        await wait_until(lambda: book.venues["A"].quote is not None)
        baseline = len(asyncio.all_tasks())
        epochs = [book.venues["A"].connection_epoch]
        for _ in range(8):
            v.drop_connection()
            await wait_until(lambda: book.venues["A"].connection_epoch == epochs[-1] + 1
                             and book.venues["A"].quote is not None)
            epochs.append(book.venues["A"].connection_epoch)
        self.assertEqual(epochs, list(range(1, 10)))
        self.assertEqual(agg.connects["A"], 9)
        self.assertEqual(v.refused_connections, 0)          # never two concurrent subscribers
        await asyncio.sleep(0.05)
        self.assertLessEqual(len(asyncio.all_tasks()), baseline, task_names())
        await agg.stop()
        await v.stop()

    async def test_second_subscriber_refused(self):
        v = Venue("A", rate_hz=100)
        port = await v.start()
        r1, w1 = await asyncio.open_connection("127.0.0.1", port, limit=protocol.MAX_FRAME)
        self.assertEqual((await protocol.read_frame(r1))["session"], 1)
        r2, w2 = await asyncio.open_connection("127.0.0.1", port)
        self.assertEqual(await asyncio.wait_for(r2.read(), 2), b"")
        self.assertEqual(v.refused_connections, 1)
        w1.close()
        w2.close()
        await v.stop()


class StopAndFairness(unittest.IsolatedAsyncioTestCase):
    async def test_stop_returns_with_open_idle_client(self):
        agg = Aggregator(Book("SYN1", ["A"], 50_000_000), {"A": ("127.0.0.1", 9)})
        port = await agg.start()
        r, w = await asyncio.open_connection("127.0.0.1", port)
        await wait_until(lambda: agg.clients == 1)
        await asyncio.wait_for(agg.stop(), 3)            # previously hung (Python 3.12 wait_closed)
        self.assertEqual(await asyncio.wait_for(r.read(), 2), b"")
        w.close()

    async def test_flooding_client_does_not_starve_event_loop(self):
        v = Venue("A", rate_hz=500)
        vport = await v.start()
        agg = Aggregator(Book("SYN1", ["A"], 50_000_000), {"A": ("127.0.0.1", vport)})
        port = await agg.start()
        await wait_until(lambda: agg.book.venues["A"].accepted >= 5)
        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write(b"".join(protocol.encode({"type": "route", "side": "buy", "quantity": 1}) for _ in range(50_000)))
        longest, last, t0 = 0.0, time.monotonic(), time.monotonic()
        while time.monotonic() - t0 < 0.4:
            await asyncio.sleep(0.001)
            now = time.monotonic()
            longest, last = max(longest, now - last), now
        self.assertLess(longest, 0.02)     # loop keeps cycling; before the fix stalls grew with the flood
        w.transport.abort()
        await agg.stop()
        await v.stop()


class PingBookkeeping(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._saved = (agg_mod.PING_INTERVAL_S, agg_mod.PING_TIMEOUT_S)
        agg_mod.PING_INTERVAL_S, agg_mod.PING_TIMEOUT_S = 0.02, 0.15
        self.pings = []
        self.sessions = 0
        self.reply_with_previous_id = False

        async def silent_venue(reader, writer):
            # Sends hello + quotes, records pings, never answers them.
            self.sessions += 1
            writer.write(protocol.encode({"type": "hello", "venue_id": "A", "symbol": "SYN1",
                                          "session": self.sessions}))
            seq = 0

            async def feed():
                nonlocal seq
                while True:
                    seq += 1
                    writer.write(protocol.encode({"type": "quote", "venue_id": "A", "symbol": "SYN1",
                                                  "sequence": seq, "bid_ticks": 100, "ask_ticks": 101,
                                                  "bid_quantity": 1, "ask_quantity": 1}))
                    await asyncio.sleep(0.005)
            t = asyncio.create_task(feed())
            try:
                while (obj := await protocol.read_frame(reader)) is not None:
                    self.pings.append((self.sessions, obj["id"]))
                    if self.reply_with_previous_id:
                        writer.write(protocol.encode({"type": "pong", "id": obj["id"] - 1}))
            except (ConnectionError, protocol.FrameError):
                pass
            finally:
                t.cancel()
                writer.transport.abort()

        self.server = await asyncio.start_server(silent_venue, "127.0.0.1", 0, limit=protocol.MAX_FRAME)
        port = self.server.sockets[0].getsockname()[1]
        self.book = Book("SYN1", ["A"], 50_000_000)
        self.agg = Aggregator(self.book, {"A": ("127.0.0.1", port)})
        await self.agg.start()

    async def asyncTearDown(self):
        await self.agg.stop()
        self.server.close()
        await self.server.wait_closed()
        agg_mod.PING_INTERVAL_S, agg_mod.PING_TIMEOUT_S = self._saved

    async def test_single_outstanding_and_timeouts_counted(self):
        await wait_until(lambda: self.agg.feed_errors["A"].get("ping_timeout", 0) >= 3, 3)
        ids = [i for s, i in self.pings if s == 1]
        self.assertEqual(ids, list(range(1, len(ids) + 1)))
        # one ping per timeout window, not one per interval: no unbounded outstanding pings
        self.assertLessEqual(len(ids), self.agg.feed_errors["A"]["ping_timeout"] + 1)
        self.assertEqual(self.agg.feed_rtt_ns["A"].count, 0)

    async def test_mismatched_pong_not_recorded_as_rtt(self):
        self.reply_with_previous_id = True
        await wait_until(lambda: self.agg.feed_errors["A"].get("unmatched_pong", 0) >= 2, 3)
        self.assertEqual(self.agg.feed_rtt_ns["A"].count, 0)
        self.assertEqual(self.agg.feed_rtt_log.count, 0)

if __name__ == "__main__":
    unittest.main()
