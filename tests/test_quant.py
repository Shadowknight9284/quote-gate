"""Exact acceptance tests for contract Q1 (signal, policy, ledger, execution, experiments)."""
import unittest
from dataclasses import replace

from quote_gate import backtest as bt
from quote_gate.book import Book, Quote
from quote_gate.ledger import Fill, Ledger, LedgerError, replay
from quote_gate.market_sim import MarketConfig, generate
from quote_gate.quant import (OrderIntent, PolicyConfig, Strategy, imbalance_bp, reference_signal,
                              target_position)
from quote_gate.router import Side

CFG = PolicyConfig(theta_bp=2000, q_max=5, max_order_qty=5, cooldown_steps=0)


class Signal(unittest.TestCase):
    def test_sign_zero_truncation(self):
        self.assertEqual(imbalance_bp(30, 10), 5000)
        self.assertEqual(imbalance_bp(10, 30), -5000)
        self.assertEqual(imbalance_bp(7, 7), 0)
        self.assertEqual(imbalance_bp(2, 1), 3333)      # 3333.33 -> 3333
        self.assertEqual(imbalance_bp(1, 2), -3333)     # truncation toward zero, not floor
        self.assertEqual(imbalance_bp(100, 1), 9801)

    def test_invalid(self):
        for bq, aq in ((0, 1), (1, 0), (-1, 5), (True, 1), (1.0, 1)):
            with self.subTest(bq=bq, aq=aq), self.assertRaises(ValueError):
                imbalance_bp(bq, aq)

    def test_reference_signal_requires_fresh(self):
        book = Book("SYN1", ["A", "B"], 50)
        e = book.on_connect("A")
        self.assertIsNone(reference_signal(book, "A", 0))          # no quote
        book.on_quote("A", e, Quote("A", "SYN1", 1, 100, 101, 30, 10), 0)
        self.assertEqual(reference_signal(book, "A", 49), 5000)
        self.assertIsNone(reference_signal(book, "A", 50))         # stale
        book.on_quote("A", e, Quote("A", "SYN1", 2, 101, 100, 30, 10), 60)   # crossed -> invalid
        self.assertIsNone(reference_signal(book, "A", 61))
        book.on_disconnect("A", e)
        self.assertIsNone(reference_signal(book, "A", 61))


class Policy(unittest.TestCase):
    def test_threshold_equality_maps_to_zero(self):
        self.assertEqual(target_position(2000, CFG), 0)
        self.assertEqual(target_position(2001, CFG), 5)
        self.assertEqual(target_position(-2000, CFG), 0)
        self.assertEqual(target_position(-2001, CFG), -5)
        self.assertIsNone(target_position(None, CFG))

    def test_no_repeated_orders_after_target(self):
        s = Strategy(CFG)
        reason, intent = s.decide(5000, 0)
        self.assertEqual((reason, intent), ("order", OrderIntent(Side.BUY, 5, 5, 5000, 0)))
        s.on_sent("o0")
        s.on_resolved("o0", 5, 0)
        self.assertEqual(s.decide(6000, 1), ("at_target", None))
        self.assertEqual(s.decide(2500, 2), ("at_target", None))
        reason, intent = s.decide(-3000, 3)       # flip: -5 - 5 = -10, clipped to 5
        self.assertEqual((intent.side, intent.qty, intent.target), (Side.SELL, 5, -5))

    def test_outstanding_blocks_and_rejection_unblocks(self):
        s = Strategy(CFG)
        s.decide(5000, 0)
        s.on_sent("o0")
        self.assertEqual(s.decide(5000, 1), ("outstanding", None))
        s.on_resolved("o0", 0, 2)                 # rejected: position unchanged
        self.assertEqual(s.position, 0)
        self.assertEqual(s.decide(5000, 3)[0], "order")

    def test_unknown_halts_until_reconciled_not_by_time(self):
        s = Strategy(CFG)
        s.decide(5000, 0)
        s.on_sent("o0")
        s.on_unknown("o0")
        for step in (1, 50, 10_000):
            self.assertEqual(s.decide(5000, step), ("halted_unknown", None))
        s.on_resolved("o0", 5, 10_001)
        self.assertEqual(s.position, 5)
        self.assertEqual(s.decide(5000, 10_002), ("at_target", None))

    def test_stale_signal_holds_position(self):
        s = Strategy(CFG)
        s.position = 5
        self.assertEqual(s.decide(None, 0), ("no_signal", None))
        self.assertEqual(s.position, 5)

    def test_cooldown(self):
        s = Strategy(replace(CFG, cooldown_steps=3))
        s.decide(5000, 0)
        s.on_sent("o0")
        s.on_resolved("o0", 5, 1)
        self.assertEqual(s.decide(-5000, 3), ("cooldown", None))
        self.assertEqual(s.decide(-5000, 4)[0], "order")


def F(i, q, p, fee=0, oid=None):
    return Fill(f"f{i}", oid or f"o{i}", "A", q, p, fee, i, i)


class LedgerTests(unittest.TestCase):
    def test_buy_sell_cash_inventory(self):
        led = Ledger(10)
        led.apply(F(1, 3, 100, fee=3))
        self.assertEqual((led.cash_ticks, led.position, led.fees_ticks), (-303, 3, 3))
        led.apply(F(2, -2, 105, fee=2))
        self.assertEqual((led.cash_ticks, led.position), (-303 + 210 - 2, 1))
        self.assertEqual(led.realized_gross_ticks, 10)                 # 2 units * (105-100)
        self.assertEqual(led.unrealized_ticks(104), 4)

    def test_round_trip_spread_loss_and_fees(self):
        led = Ledger(5)
        led.apply(F(1, 5, 101, fee=5))      # buy at ask
        led.apply(F(2, -5, 100, fee=5))     # sell at bid, price unchanged
        self.assertEqual(led.position, 0)
        self.assertEqual(led.realized_gross_ticks, -5)   # spread 1 tick * 5 units
        self.assertEqual(led.cash_ticks, -15)            # spread loss + 10 fees
        self.assertEqual(led.equity_ticks(None), -15)

    def test_short_fifo_and_identity(self):
        led = Ledger(5)
        led.apply(F(1, -2, 100))
        led.apply(F(2, -1, 102))
        led.apply(F(3, 4, 99))              # covers 3 short (FIFO), opens 1 long
        self.assertEqual(led.position, 1)
        self.assertEqual(led.realized_gross_ticks, (100 - 99) * 2 + (102 - 99) * 1)
        mark = 103
        self.assertEqual(led.equity_ticks(mark),
                         led.realized_gross_ticks - led.fees_ticks + led.unrealized_ticks(mark))

    def test_duplicate_and_conflicting_ids(self):
        led = Ledger(5)
        self.assertEqual(led.apply(F(1, 2, 100)), "applied")
        self.assertEqual(led.apply(F(1, 2, 100)), "duplicate")
        self.assertEqual((led.position, led.cash_ticks), (2, -200))
        with self.assertRaises(LedgerError):
            led.apply(F(1, 2, 101))
        self.assertEqual((led.position, led.cash_ticks), (2, -200))

    def test_limit_and_invalid(self):
        led = Ledger(5)
        led.apply(F(1, 5, 100))
        with self.assertRaises(LedgerError):
            led.apply(F(2, 1, 100))
        self.assertEqual((led.position, len(led.fills)), (5, 1))
        for bad in (F(3, 0, 100), F(3, 1, 0), F(3, 1, 100, fee=-1), Fill("", "o", "A", 1, 1, 0, 0, 0)):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                led.apply(bad)

    def test_replay_and_missing_mark(self):
        led = Ledger(5)
        for f in (F(1, 3, 100, 1), F(2, -1, 101, 1)):
            led.apply(f)
        r = replay(led.fills.values(), 5)
        self.assertEqual((r.cash_ticks, r.position, r.fees_ticks, r.realized_gross_ticks),
                         (led.cash_ticks, led.position, led.fees_ticks, led.realized_gross_ticks))
        self.assertIsNone(led.equity_ticks(led.mark_price(None, 105)))     # long, bid unknown


class Execution(unittest.TestCase):
    def venue(self):
        v = bt.SimVenue("A", fee_per_unit=1)
        v.on_quote(Quote("A", "SYN1", 1, 100, 101, 4, 3))
        return v

    def order(self, oid="o1", side=Side.BUY, qty=2, limit=101, epoch=1):
        return bt.Order(oid, "A", side, qty, limit, epoch, 0)

    def test_fill_consumes_and_replenishes(self):
        v = self.venue()
        r = v.submit(self.order(), 0)
        self.assertEqual((r["status"], r["fill"].price_ticks, r["fill"].fee_ticks), ("FILLED", 101, 2))
        self.assertEqual(v.submit(self.order("o2", qty=2), 0),
                         {"status": "REJECTED", "reason": "insufficient_quantity"})   # 1 left
        v.on_quote(Quote("A", "SYN1", 2, 100, 101, 4, 3))
        self.assertEqual(v.submit(self.order("o3", qty=2), 1)["status"], "FILLED")

    def test_price_session_and_idempotent_ids(self):
        v = self.venue()
        self.assertEqual(v.submit(self.order("o1", limit=100), 0)["reason"], "price_moved")
        self.assertEqual(v.submit(self.order("o2", side=Side.SELL, limit=101), 0)["reason"], "price_moved")
        self.assertEqual(v.submit(self.order("o3", epoch=2), 0)["reason"], "session")
        first = v.submit(self.order("o4"), 0)
        self.assertIs(v.submit(self.order("o4"), 0), first)                 # same payload
        self.assertEqual(v.submit(self.order("o4", qty=1), 0)["reason"], "conflicting_order_id")


class Backtest(unittest.TestCase):
    M = MarketConfig(seed=3, steps=400, beta_pct=50)
    P = CFG

    def test_market_stream_matched_across_configs(self):
        a = [(s.quotes, s.latent_price) for s in generate(self.M)]
        bt.run(self.M, self.P, bt.ExecConfig(exec_delay_steps=2, drop_ack_pct=30, fault_seed=9))
        b = [(s.quotes, s.latent_price) for s in generate(self.M)]
        self.assertEqual(a, b)

    def test_no_future_observation(self):
        # Decisions over the first 200 steps are identical whether or not the future exists.
        full = bt.run(self.M, self.P, bt.ExecConfig())
        short = bt.run(replace(self.M, steps=200), self.P, bt.ExecConfig())
        f200 = [f for f in full.ledger.fills.values() if f.step < 200]
        self.assertEqual(f200, list(short.ledger.fills.values()))

    def test_no_trade_baseline(self):
        r = bt.run(self.M, self.P, bt.ExecConfig(trade=False))
        self.assertEqual((r.fills, r.final["position"], r.final["equity_marked_ticks"]), (0, 0, 0))

    def test_unknown_outcomes_reconciled_without_invented_fills(self):
        r = bt.run(self.M, self.P, bt.ExecConfig(drop_ack_pct=50, fault_seed=1, ack_timeout_steps=3,
                                                 reconcile_after_steps=5))
        self.assertGreater(r.unknown, 0)
        self.assertEqual(r.reconciled + r.final["halted_unknown_at_end"], r.unknown)
        led = r.ledger
        self.assertEqual(sum(f.signed_qty for f in led.fills.values()), led.position)
        self.assertLessEqual(r.max_abs_position, self.P.q_max)

    def test_stale_reference_blocks_orders(self):
        r = bt.run(self.M, self.P, bt.ExecConfig(outage_every=100, outage_len=50, stale_after_ns=15_000_000))
        self.assertGreater(r.decisions.get("no_signal", 0), 0)
        self.assertGreater(r.marks_missing, 0)
        orders_during = [f for f in r.ledger.fills.values()
                         if f.decision_ref % 100 >= 2 and f.decision_ref % 100 < 50]
        self.assertEqual(orders_during, [])       # A stale from 2nd silent step (15 ms > 10 ms step)


if __name__ == "__main__":
    unittest.main()
