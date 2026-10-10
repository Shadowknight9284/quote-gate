"""L2 book edge-case regression tests written by Hermes (task H4), reviewed by Claude:
10/10 accepted after a 6/6 mutation check (see docs/delegation-log.md)."""
import unittest
from decimal import Decimal
from quote_gate.l2book import L2Book, Top, Units

U = Units(Decimal("0.01"), Decimal("0.00000001"))
b = L2Book(U)


class TestL2BookHermes(unittest.TestCase):

    def setUp(self):
        global b
        b = L2Book(U)
        b.new_session()

    def test_delete_best_two_bid_levels(self):
        """deleting the best two bid levels in one batch moves the best bid to the third level"""
        # Setup: create 3 bid levels and 1 ask level to enable top() to return a value
        b.apply_snapshot([["100.10", "1"], ["100.00", "2"], ["99.90", "3"]], [["100.20", "1"]])
        top = b.top()
        self.assertEqual(top.bid_ticks, 10010)
        self.assertEqual(top.bid_units, 100000000)  # 1 * 10^8

        # Delete the best two bid levels
        b.apply_batch([["buy", "100.10", "0"], ["buy", "100.00", "0"]])
        top = b.top()
        self.assertEqual(top.bid_ticks, 9990)
        self.assertEqual(top.bid_units, 300000000)  # 3 * 10^8

    def test_readd_level_same_batch_last_wins(self):
        """re-adding a level at a price that was deleted earlier in the SAME batch (order matters: last wins)"""
        # Setup: create 2 bid levels and 1 ask level to enable top() to return a value
        b.apply_snapshot([["100.10", "1"], ["100.00", "2"]], [["100.20", "1"]])
        top = b.top()
        self.assertEqual(top.bid_ticks, 10010)
        self.assertEqual(top.bid_units, 100000000)  # 1 * 10^8

        # Delete and re-add the same level in same batch (last wins)
        b.apply_batch([["buy", "100.10", "0"], ["buy", "100.10", "5"]])
        top = b.top()
        self.assertEqual(top.bid_ticks, 10010)
        self.assertEqual(top.bid_units, 500000000)  # 5 * 10^8

    def test_trailing_zeros_same_price_tick(self):
        """trailing zeros: "100.10" and "100.1" are the same price tick (10010)"""
        # Setup: create bid level with "100.10" and an ask level to enable top() to return a value
        b.apply_snapshot([["100.10", "1"]], [["100.20", "1"]])
        top = b.top()
        self.assertEqual(top.bid_ticks, 10010)
        self.assertEqual(top.bid_units, 100000000)  # 1 * 10^8

        # Add same price with different trailing zeros
        b.apply_batch([["buy", "100.1", "2"]])
        top = b.top()
        self.assertEqual(top.bid_ticks, 10010)
        self.assertEqual(top.bid_units, 200000000)  # 2 * 10^8

    def test_one_sided_snapshot_no_asks(self):
        """one-sided snapshot (no asks): state VALID but top() is None"""
        b.apply_snapshot([["100.10", "1"]], [])
        self.assertEqual(b.state, "VALID")
        top = b.top()
        self.assertIsNone(top)

    def test_remove_last_ask_leaves_none(self):
        """an update that removes the last ask leaves state VALID and top() None"""
        # Setup: create bid and ask levels
        b.apply_snapshot([["100.10", "1"]], [["100.20", "1"]])
        top = b.top()
        self.assertIsNotNone(top)

        # Remove the last ask
        b.apply_batch([["sell", "100.20", "0"]])
        self.assertEqual(b.state, "VALID")
        top = b.top()
        self.assertIsNone(top)

    def test_delete_absent_level(self):
        """delete of an absent level increments stats["delete_absent"] and changes nothing else"""
        # Setup: create bid levels
        b.apply_snapshot([["100.10", "1"]], [["100.20", "1"]])
        initial_stats = b.stats.copy()

        # Try to delete an absent level
        b.apply_batch([["buy", "100.30", "0"]])

        # Verify stats incremented correctly
        self.assertEqual(b.stats["delete_absent"], initial_stats["delete_absent"] + 1)
        self.assertEqual(b.stats["deletes"], initial_stats["deletes"])
        self.assertEqual(b.stats["changes_applied"], initial_stats["changes_applied"] + 1)
        self.assertEqual(b.state, "VALID")

    def test_invalid_change_in_batch(self):
        """an invalid change anywhere in a batch (e.g. size "1.000000001") leaves bids/asks EMPTY and state INVALID"""
        # Setup: create bid levels
        b.apply_snapshot([["100.10", "1"]], [["100.20", "1"]])
        self.assertEqual(b.state, "VALID")

        # Apply an invalid change in batch
        b.apply_batch([["buy", "100.10", "1"], ["buy", "100.20", "1.000000001"]])

        # Should be invalid and empty
        self.assertEqual(b.state, "INVALID")
        self.assertEqual(b.bids, {})
        self.assertEqual(b.asks, {})

    def test_after_invalid_apply_batch_increments_ignored(self):
        """after INVALID, apply_batch increments stats["updates_ignored_invalid"] each call"""
        # Setup: create bid levels and make invalid
        b.apply_snapshot([["100.10", "1"]], [["100.20", "1"]])
        b.apply_batch([["buy", "100.10", "1"], ["buy", "100.20", "1.000000001"]])
        self.assertEqual(b.state, "INVALID")

        initial_ignored = b.stats["updates_ignored_invalid"]

        # Apply another batch while invalid
        b.apply_batch([["buy", "100.30", "1"]])
        self.assertEqual(b.stats["updates_ignored_invalid"], initial_ignored + 1)

    def test_new_session_after_invalid_returns_new_top(self):
        """new_session() after INVALID followed by a valid snapshot returns the new top; stats["snapshots"] counts both sessions"""
        # Setup: create bid levels and make invalid
        b.apply_snapshot([["100.10", "1"]], [["100.20", "1"]])
        b.apply_batch([["buy", "100.10", "1"], ["buy", "100.20", "1.000000001"]])
        self.assertEqual(b.state, "INVALID")

        # New session should reset state
        b.new_session()
        self.assertEqual(b.state, "EMPTY")

        # Apply valid snapshot
        top = b.apply_snapshot([["100.20", "2"]], [["100.30", "1"]])
        self.assertEqual(b.state, "VALID")
        self.assertEqual(top.bid_ticks, 10020)
        self.assertEqual(top.bid_units, 200000000)  # 2 * 10^8

        # Stats should include both snapshots
        self.assertEqual(b.stats["snapshots"], 2)

    def test_large_valid_size_converts_correctly(self):
        """very large but valid size "123456.12345678" converts to 12345612345678 units"""
        # Setup: create bid levels with an ask level to enable top() to return a value
        b.apply_snapshot([["100.10", "123456.12345678"]], [["100.20", "1"]])
        top = b.top()
        self.assertEqual(top.bid_ticks, 10010)
        self.assertEqual(top.bid_units, 12345612345678)  # Direct conversion


if __name__ == '__main__':
    unittest.main()