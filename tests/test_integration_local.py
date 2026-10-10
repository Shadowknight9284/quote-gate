"""Loopback integration: venue simulators -> aggregator -> client port.

Uses real sockets and real monotonic time; assertions are on behavior, not
timing magnitudes (timing belongs in benchmarks).
"""
import asyncio
import time
import unittest

from quote_gate import protocol
from quote_gate.aggregator import Aggregator
from quote_gate.book import Book, FeedStatus
from quote_gate.synthetic import GeneratorConfig, generate_quotes
from quote_gate.venue import Venue

STALE_NS = 50_000_000


async def wait_until(cond, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met before timeout")
        await asyncio.sleep(0.005)


class LocalSlice(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.va = Venue("A", seed=1, rate_hz=500, start_bid_ticks=10000)
        self.vb = Venue("B", seed=2, rate_hz=500, start_bid_ticks=10000)
        pa, pb = await self.va.start(), await self.vb.start()
        self.book = Book("SYN1", ["A", "B"], STALE_NS)
        self.agg = Aggregator(self.book, {"A": ("127.0.0.1", pa), "B": ("127.0.0.1", pb)},
                              max_clients=2)
        self.port = await self.agg.start()
        await wait_until(lambda: self.book.venues["A"].accepted > 5 and self.book.venues["B"].accepted > 5)

    async def asyncTearDown(self):
        await self.agg.stop()
        await self.va.stop()
        await self.vb.stop()

    async def request(self, obj, conn=None):
        reader, writer = conn or await asyncio.open_connection("127.0.0.1", self.port,
                                                               limit=protocol.MAX_FRAME)
        writer.write(protocol.encode(obj))
        await writer.drain()
        reply = await asyncio.wait_for(protocol.read_frame(reader), 2)
        if conn is None:
            writer.close()
        return reply

    async def test_state_matches_generator_exactly(self):
        st = self.book.venues["A"]
        n = st.last_sequence
        cfg = GeneratorConfig(venue_id="A", start_bid_ticks=10000)
        expected = generate_quotes(1 * 1_000_003 + 1, n, cfg)[-1]
        self.assertEqual((st.quote.bid_ticks, st.quote.ask_ticks, st.quote.bid_quantity),
                         (expected.bid_ticks, expected.ask_ticks, expected.bid_quantity))
        self.assertEqual(st.rejected, {})
        self.assertEqual(st.gaps, 0)

    async def test_feed_ping_rtt_and_session_recorded(self):
        await wait_until(lambda: self.agg.feed_rtt_ns["A"].count >= 2 and self.agg.feed_rtt_ns["B"].count >= 2)
        self.assertEqual(self.agg.venue_session, {"A": 1, "B": 1})
        self.assertGreaterEqual(self.va.pongs, 2)
        self.assertEqual(self.agg.feed_errors["A"], {})
        self.va.drop_connection()
        await wait_until(lambda: self.agg.venue_session["A"] == 2)

    async def test_events_and_stale_detection_recorded(self):
        self.va.pause()
        await wait_until(lambda: any(e[1] == "A" and e[2] == "status" and e[3]["to"] == "stale"
                                     for e in self.agg.events.items()))
        ev = [e for e in self.agg.events.items() if e[1] == "A" and e[3].get("to") == "stale"][-1]
        self.assertGreaterEqual(ev[3]["detect_delay_ns"], 0)
        self.assertGreaterEqual(ev[3]["quote_age_ns"], STALE_NS)
        self.va.resume()
        self.va.drop_connection()
        await wait_until(lambda: [e[2] for e in self.agg.events.items() if e[1] == "A"].count("connect") == 2)
        kinds = [e[2] for e in self.agg.events.items() if e[1] == "A"]
        self.assertIn("disconnect", kinds)
        r = await self.request({"type": "route", "side": "buy", "quantity": 1})
        if r["venue_id"] is not None:
            self.assertLess(r["quote_age_ns"], STALE_NS)

    async def test_dump_artifacts(self):
        import json, os, tempfile
        await wait_until(lambda: self.agg.feed_rtt_log.count >= 2)
        with tempfile.TemporaryDirectory() as d:
            self.agg.dump_artifacts(d)
            with open(os.path.join(d, "feed_rtt.jsonl")) as f:
                lines = f.read().splitlines()
            hdr = json.loads(lines[0])
            self.assertEqual(hdr["schema"], "qg.feed_rtt.v1")
            self.assertEqual(hdr["retained"], len(lines) - 1)
            with open(os.path.join(d, "venue_events.jsonl")) as f:
                ev = f.read().splitlines()
            self.assertEqual(json.loads(ev[0])["schema"], "qg.venue_events.v1")
            self.assertGreaterEqual(len(ev), 3)      # header + connect A + connect B (+ status)

    async def test_route_over_client_port(self):
        r = await self.request({"type": "route", "id": 7, "side": "buy", "quantity": 1})
        self.assertEqual(r["type"], "route_result")
        self.assertEqual(r["id"], 7)
        self.assertIn(r["venue_id"], ("A", "B"))
        bad = await self.request({"type": "route", "id": 8, "side": "hold", "quantity": 1})
        self.assertEqual(bad["type"], "error")
        s = await self.request({"type": "stats"})
        self.assertGreater(s["process_ns"]["count"], 0)

    async def test_paused_feed_goes_stale_and_is_excluded(self):
        self.va.pause()
        await wait_until(lambda: self.book.status("A", time.monotonic_ns()) is FeedStatus.STALE)
        r = await self.request({"type": "route", "side": "sell", "quantity": 1})
        self.assertEqual(r["venue_id"], "B")
        self.assertIn(["A", "stale"], r["excluded"])
        self.va.resume()
        await wait_until(lambda: self.book.status("A", time.monotonic_ns()) is FeedStatus.FRESH)

    async def test_disconnect_reconnects_with_new_epoch_and_sequence_reset(self):
        e1 = self.book.venues["A"].connection_epoch
        self.va.drop_connection()
        await wait_until(lambda: self.book.venues["A"].connection_epoch == e1 + 1
                         and self.book.venues["A"].quote is not None)
        self.assertEqual(self.va.session, 2)
        self.assertEqual(self.agg.connects["A"], 2)
        self.assertLess(self.book.venues["A"].last_sequence, 200)   # restarted from 1

    async def test_oversized_frame_closes_and_recovers(self):
        e1 = self.book.venues["B"].connection_epoch
        self.vb.inject_raw(b"x" * (protocol.MAX_FRAME + 10))
        await wait_until(lambda: self.book.venues["B"].connection_epoch == e1 + 1)
        self.assertEqual(self.agg.feed_errors["B"].get("FrameError"), 1)

    async def test_invalid_quote_on_wire_rejected(self):
        self.va.pause()
        await asyncio.sleep(0.01)
        seq = self.book.venues["A"].last_sequence + 1000
        self.va.inject_raw(protocol.encode({"type": "quote", "venue_id": "A", "symbol": "SYN1",
                                            "sequence": seq, "bid_ticks": 5, "ask_ticks": 4,
                                            "bid_quantity": 1, "ask_quantity": 1}))
        await wait_until(lambda: self.book.venues["A"].rejected.get("crossed") == 1)
        self.assertEqual(self.book.status("A", time.monotonic_ns()), FeedStatus.INVALID)

    async def test_duplicate_and_old_sequence_on_wire_do_not_roll_back(self):
        self.va.pause()
        await asyncio.sleep(0.01)
        st = self.book.venues["A"]
        last = st.quote
        for seq in (last.sequence, last.sequence - 1):
            self.va.inject_raw(protocol.encode({"type": "quote", "venue_id": "A", "symbol": "SYN1",
                                                "sequence": seq, "bid_ticks": 1, "ask_ticks": 2,
                                                "bid_quantity": 1, "ask_quantity": 1}))
        await wait_until(lambda: st.rejected.get("duplicate") == 1 and st.rejected.get("out_of_order") == 1)
        self.assertEqual(st.quote, last)

    async def test_slow_consumer_does_not_stall_feed(self):
        # Client floods stats requests and never reads replies; its own task blocks on drain.
        r, w = await asyncio.open_connection("127.0.0.1", self.port)
        for _ in range(20_000):
            w.write(protocol.encode({"type": "stats"}))
        before = self.book.venues["A"].accepted
        await asyncio.sleep(0.3)
        self.assertGreater(self.book.venues["A"].accepted - before, 50)   # ~150 expected at 500/s
        ok = await self.request({"type": "route", "side": "buy", "quantity": 1})
        self.assertEqual(ok["type"], "route_result")
        w.transport.abort()

    async def test_client_limit_enforced(self):
        conns = [await asyncio.open_connection("127.0.0.1", self.port) for _ in range(2)]
        await wait_until(lambda: self.agg.clients == 2)
        r, w = await asyncio.open_connection("127.0.0.1", self.port)
        self.assertEqual(await r.read(), b"")      # refused: closed immediately
        self.assertEqual(self.agg.refused_clients, 1)
        for _, cw in conns + [(r, w)]:
            cw.close()


if __name__ == "__main__":
    unittest.main()
