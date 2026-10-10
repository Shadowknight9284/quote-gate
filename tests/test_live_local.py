"""Loopback tests for the networked order path (contract O1). Real TCP on 127.0.0.1."""
import asyncio
import time
import unittest

from quote_gate import protocol
from quote_gate.aggregator import Aggregator
from quote_gate.book import Book
from quote_gate.live import LiveRunner, venue_reconciliation
from quote_gate.market_sim import MarketConfig
from quote_gate.quant import PolicyConfig
from quote_gate.router import Side
from quote_gate.venue import Venue

MKT = MarketConfig(seed=7, steps=200_000, beta_pct=50)


async def wait_until(cond, timeout=4.0):
    deadline = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met before timeout")
        await asyncio.sleep(0.005)


class RawClient:
    """Minimal subscriber used to exercise the venue protocol directly."""

    async def connect(self, port):
        self.r, self.w = await asyncio.open_connection("127.0.0.1", port, limit=protocol.MAX_FRAME)
        self.hello = await protocol.read_frame(self.r)
        self.frames = asyncio.Queue()
        self.task = asyncio.create_task(self._pump())
        return self

    async def _pump(self):
        while (f := await protocol.read_frame(self.r)) is not None:
            if f.get("type") != "quote":
                await self.frames.put(f)

    def order(self, oid, side="buy", qty=1, limit=10**9, **over):
        f = {"type": "order", "client_order_id": oid, "symbol": "SYN1", "venue_id": "A",
             "instance": self.hello["instance"], "session": self.hello["session"], "side": side,
             "quantity": qty, "limit_ticks": limit if side == "buy" else 1, **over}
        self.w.write(protocol.encode(f))

    def send(self, f):
        self.w.write(protocol.encode(f))

    async def next(self):
        return await asyncio.wait_for(self.frames.get(), 2)

    def close(self):
        self.task.cancel()
        self.w.transport.abort()


class VenueProtocol(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.v = Venue("A", rate_hz=200, market=MKT, fee_per_unit=1, store_capacity=2)
        self.port = await self.v.start()
        self.c = await RawClient().connect(self.port)
        await wait_until(lambda: self.v.quote is not None)

    async def asyncTearDown(self):
        self.c.close()
        await self.v.stop()

    async def test_fill_and_explicit_rejection(self):
        self.c.order("o1")
        e = await self.c.next()
        self.assertEqual((e["type"], e["status"], e["quantity"], e["fee_ticks"]), ("exec", "FILLED", 1, 1))
        from quote_gate.market_sim import generate
        from dataclasses import replace
        stream = {st.quotes[0].sequence: st.quotes[0] for st in generate(replace(MKT, steps=e["quote_sequence"]))}
        self.assertEqual(e["fill_price_ticks"], stream[e["quote_sequence"]].ask_ticks)   # buy at ask
        self.c.order("o2", limit=1)                         # buy limit far below ask
        e = await self.c.next()
        self.assertEqual((e["status"], e["reason"], e["quantity"]), ("REJECTED", "price_moved", 0))

    async def test_same_id_same_payload_executes_once(self):
        self.c.order("o1")
        first = await self.c.next()
        self.c.order("o1")
        again = await self.c.next()
        self.assertEqual(first, again)
        self.assertEqual(self.v.orders_executed, 1)
        self.assertEqual(len(self.v.fill_log), 1)

    async def test_same_id_different_payload_conflict(self):
        self.c.order("o1")
        await self.c.next()
        self.c.order("o1", qty=2)
        e = await self.c.next()
        self.assertEqual((e["status"], e["reason"]), ("REJECTED", "conflicting_order_id"))
        self.assertEqual(self.v.orders_executed, 1)

    async def test_bool_quantity_and_bad_fields_rejected(self):
        for oid, over in (("b1", {"quantity": True}), ("b2", {"symbol": "X"}), ("b3", {"instance": "nope"}),
                          ("b4", {"limit_ticks": 1.5})):
            self.c.order(oid, **over)
            e = await self.c.next()
            self.assertEqual((e["status"], e["reason"]), ("REJECTED", "bad_order"), oid)
        self.assertEqual(self.v.orders_executed, 0)

    async def test_store_capacity_backpressure_and_release(self):
        self.c.order("o1")
        await self.c.next()
        self.c.order("o2", limit=1)
        await self.c.next()                                 # rejection also occupies a record
        self.c.order("o3")
        e = await self.c.next()
        self.assertEqual((e["status"], e["reason"]), ("REJECTED", "store_full"))
        self.assertEqual(self.v.orders_executed, 1)          # o3 not executed
        self.c.send({"type": "release", "client_order_id": "o1"})
        self.c.order("o4")
        e = await self.c.next()
        self.assertEqual(e["status"], "FILLED")
        self.assertEqual(self.v.store.evicted, 1)
        self.c.send({"type": "order_status", "client_order_id": "o2"})   # unreleased: still kept
        s = await self.c.next()
        self.assertEqual((s["type"], s["found"], s["reason"]), ("status", True, "price_moved"))

    async def test_reuse_of_released_and_evicted_id_executes_again(self):
        # Documented dedup lifetime: an ID is deduplicated from first receipt until its record is
        # released by the client AND evicted to make room. After eviction the venue has no memory
        # of it: the same ID with the same payload is treated as a new order and executes again.
        self.c.order("o1")
        await self.c.next()
        self.c.order("o2")
        await self.c.next()
        self.c.send({"type": "release", "client_order_id": "o1"})
        self.c.order("o3")                                   # store full (2) -> evicts released o1
        await self.c.next()
        self.assertEqual(self.v.store.evicted, 1)
        self.assertIsNone(self.v.store.get("o1"))
        self.c.send({"type": "order_status", "client_order_id": "o1"})
        self.assertFalse((await self.c.next())["found"])
        executed = self.v.orders_executed
        self.c.send({"type": "release", "client_order_id": "o2"})
        self.c.order("o1")                                   # same ID, same payload, after eviction
        e = await self.c.next()
        self.assertEqual(e["status"], "FILLED")
        self.assertEqual(self.v.orders_executed, executed + 1)   # executed again: not deduplicated

    async def test_unreleased_id_is_never_evicted(self):
        self.c.order("o1")
        await self.c.next()
        self.c.order("o2")
        await self.c.next()
        self.c.order("o3")
        self.assertEqual((await self.c.next())["reason"], "store_full")
        self.c.order("o1")                                   # still deduplicated
        self.assertEqual((await self.c.next())["status"], "FILLED")
        self.assertEqual(self.v.orders_executed, 2)

    async def test_status_query_does_not_execute(self):
        self.c.send({"type": "order_status", "client_order_id": "never"})
        s = await self.c.next()
        self.assertEqual((s["found"], self.v.orders_executed), (False, 0))


class FakeWriter:
    def __init__(self):
        self.frames = []

    def write(self, data):
        import json
        self.frames.append(json.loads(data))


class RunnerResponses(unittest.IsolatedAsyncioTestCase):
    """Response validation with a fake connection (no sockets): exact, deterministic."""

    async def asyncSetUp(self):
        self.agg = Aggregator(Book("SYN1", ["A"], 50_000_000), {"A": ("127.0.0.1", 9)})
        self.w = FakeWriter()
        self.agg.conn["A"] = {"writer": self.w, "epoch": 1, "session": 3, "instance": "I1"}
        self.r = LiveRunner(self.agg, PolicyConfig(), run_tag="t", ack_timeout_s=0.05, status_interval_s=0.02)
        from quote_gate.book import Quote
        e = self.agg.book.on_connect("A")
        q = Quote("A", "SYN1", 1, 100, 101, 90, 10)          # imbalance 0.8 -> buy 5
        self.agg.book.on_quote("A", e, q, time.monotonic_ns())
        self.r.on_quote("A", q, time.monotonic_ns())
        self.oid = self.w.frames[-1]["client_order_id"]

    def exec_frame(self, **over):
        return {"type": "exec", "client_order_id": self.oid, "venue_id": "A", "instance": "I1",
                "session": 3, "status": "FILLED", "reason": None, "fill_price_ticks": 101,
                "quantity": 5, "fee_ticks": 5, "quote_sequence": 1, **over}

    async def test_wrong_session_id_instance_not_applied(self):
        self.r.on_frame("A", self.exec_frame(session=4))
        self.r.on_frame("A", self.exec_frame(client_order_id="other"))
        self.r.on_frame("A", self.exec_frame(instance="I2"))
        self.assertEqual(self.r.ledger.position, 0)
        self.assertEqual(self.r.c.invalid_response,
                         {"session_mismatch": 1, "unknown_order_id": 1, "venue_or_instance_mismatch": 1})
        self.assertIn(self.oid, self.r.pending)

    async def test_duplicate_exec_does_not_double_book(self):
        self.r.on_frame("A", self.exec_frame())
        self.r.on_frame("A", self.exec_frame())
        self.assertEqual((self.r.ledger.position, self.r.ledger.cash_ticks), (5, -510))
        self.assertEqual(self.r.c.duplicate_exec, 1)
        self.assertEqual(self.w.frames[-1], {"type": "release", "client_order_id": self.oid})

    async def test_timeout_unknown_halts_then_status_resolves(self):
        await wait_until(lambda: self.r.c.timeouts == 1)
        self.assertIn(self.oid, self.r.strategy.halted_unknown)
        await wait_until(lambda: any(f["type"] == "order_status" for f in self.w.frames))
        self.assertEqual(sum(f["type"] == "order" for f in self.w.frames), 1)     # never resent
        self.r.on_frame("A", {**self.exec_frame(), "type": "status", "found": True})
        self.assertEqual((self.r.ledger.position, self.r.c.resolved_by_status), (5, 1))
        self.assertFalse(self.r.strategy.halted_unknown)

    async def test_not_found_same_instance_vs_restarted_instance(self):
        await wait_until(lambda: self.r.c.timeouts == 1)
        self.r.on_frame("A", {"type": "status", "client_order_id": self.oid, "venue_id": "A",
                              "instance": "I2", "found": False})
        self.assertEqual(self.r.c.lost_record, 1)
        self.assertIn(self.oid, self.r.strategy.halted_unknown)     # stays UNKNOWN, halted
        self.assertIn(self.oid, self.r.pending)


class LiveLoopback(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.v = Venue("A", rate_hz=300, market=MKT, fee_per_unit=1)
        port = await self.v.start()
        self.agg = Aggregator(Book("SYN1", ["A"], 50_000_000), {"A": ("127.0.0.1", port)})
        self.r = LiveRunner(self.agg, PolicyConfig(), run_tag="L", ack_timeout_s=0.1, status_interval_s=0.05)

    async def asyncTearDown(self):
        await self.agg.stop()
        await self.v.stop()

    async def quiesce(self):
        """Stop new decisions and wait until no order is pending, so the venue/ledger comparison is
        taken at a quiescent point (otherwise a just-filled order may have its exec in flight)."""
        self.agg.quote_listener = None
        await wait_until(lambda: not self.r.pending)

    def check_agreement(self):
        rec = venue_reconciliation(self.r, self.v)
        self.assertEqual((rec["ledger_not_at_venue"], rec["mismatched"]), (0, 0))
        self.assertTrue(rec["unbooked_all_unresolved"])
        self.assertLessEqual(abs(self.r.ledger.position), 5)
        return rec

    async def test_normal_flow_coexists_with_quotes_and_pings(self):
        await self.agg.start()
        await wait_until(lambda: self.r.c.filled >= 20 and self.agg.feed_rtt_ns["A"].count >= 2)
        self.assertEqual(self.agg.feed_errors["A"], {})
        self.assertEqual(self.r.c.invalid_response, {})
        self.assertEqual(self.r.c.timeouts, 0)
        self.assertGreater(self.r.exec_rtt_ns.count, 0)
        await self.quiesce()
        self.assertEqual(self.check_agreement()["unbooked_venue_fills"], 0)

    async def test_dropped_ack_after_fill_recovered_by_status(self):
        self.v.drop_next_acks = 1
        await self.agg.start()
        await wait_until(lambda: self.r.c.resolved_by_status >= 1)
        self.assertEqual(self.r.c.timeouts, 1)
        self.assertEqual(self.r.reconcile_ns.count, 1)
        self.assertEqual(self.v.orders_executed, len(self.v.fill_log))
        await wait_until(lambda: self.r.c.filled >= 5)
        await self.quiesce()
        rec = self.check_agreement()
        self.assertEqual(rec["unbooked_venue_fills"], 0)

    async def test_disconnect_after_execution_before_ack(self):
        self.v.disconnect_after_exec = 1
        await self.agg.start()
        await wait_until(lambda: self.r.c.unknown_disconnect == 1 and self.r.c.resolved_by_status == 1)
        self.assertEqual(self.agg.connects["A"], 2)
        await self.quiesce()
        rec = self.check_agreement()
        self.assertEqual(rec["unbooked_venue_fills"], 0)

    async def test_venue_restart_with_lost_record_stays_unresolved(self):
        self.v.drop_next_acks = 1
        await self.agg.start()
        await wait_until(lambda: self.r.c.timeouts == 1)
        oid = self.r.unresolved()[0].oid
        outcome = self.v.store.get(oid)[1]                    # venue truth before the record is lost
        self.v.restart_lose_store()
        await wait_until(lambda: self.r.c.lost_record == 1)
        sent = self.r.c.sent
        await asyncio.sleep(0.3)
        self.assertEqual(self.r.c.sent, sent)                 # halted: no new orders
        self.assertTrue(self.r.strategy.halted_unknown)
        self.assertEqual(len(self.r.unresolved()), 1)
        rec = self.check_agreement()
        filled = outcome["status"] == "FILLED"
        self.assertEqual(oid in dict(self.v.fill_log), filled)
        self.assertEqual(rec["unbooked_venue_fills"], 1 if filled else 0)   # real fill, reported unbooked


if __name__ == "__main__":
    unittest.main()
