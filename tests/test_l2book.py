"""Hand-authored fixture tests for Coinbase level2_batch book reconstruction (contract R1)."""
import unittest
from decimal import Decimal

from quote_gate.l2book import L2Book, Top, Units

U = Units(Decimal("0.01"), Decimal("0.00000001"))
SNAP_B = [["100.00", "1.5"], ["99.99", "2"], ["99.50", "0.00000001"]]
SNAP_A = [["100.01", "0.25"], ["100.05", "3"]]


def fresh():
    b = L2Book(U)
    b.new_session()
    return b


class Units_(unittest.TestCase):
    def test_exact_fractional_units(self):
        self.assertEqual(U.price("83040.55"), 8304055)
        self.assertEqual(U.size("0.00000001"), 1)
        self.assertEqual(U.size("3.35341040"), 335341040)
        self.assertEqual(U.size("0"), 0)
        self.assertEqual(U.size("1e-8"), 1)

    def test_rejects_off_increment_and_malformed(self):
        for bad in ("100.001", "abc", "", "-1", "NaN", "Infinity", 100, None):
            with self.subTest(bad=bad), self.assertRaises(Exception):
                U.price(bad)
        with self.assertRaises(Exception):
            U.size("0.000000001")


class Snapshot(unittest.TestCase):
    def test_snapshot_required_before_updates(self):
        b = fresh()
        self.assertIsNone(b.apply_batch([["buy", "100.00", "1"]]))
        self.assertEqual(b.stats["updates_ignored_invalid"], 1)
        self.assertEqual(b.state, "EMPTY")

    def test_snapshot_top(self):
        b = fresh()
        self.assertEqual(b.apply_snapshot(SNAP_B, SNAP_A), Top(10000, 150000000, 10001, 25000000))
        self.assertEqual(b.state, "VALID")

    def test_snapshot_with_zero_or_duplicate_level_invalid(self):
        for bids in ([["100.00", "0"]], [["100.00", "1"], ["100.00", "2"]]):
            b = fresh()
            self.assertIsNone(b.apply_snapshot(bids, SNAP_A))
            self.assertEqual(b.state, "INVALID")

    def test_crossed_snapshot_invalid(self):
        b = fresh()
        self.assertIsNone(b.apply_snapshot([["100.02", "1"]], SNAP_A))
        self.assertEqual(b.invalid_reason, "crossed_or_locked")


class Updates(unittest.TestCase):
    def setUp(self):
        self.b = fresh()
        self.b.apply_snapshot(SNAP_B, SNAP_A)

    def test_absolute_size_not_delta(self):
        t = self.b.apply_batch([["buy", "100.00", "0.5"]])
        self.assertEqual(t.bid_units, 50000000)            # replaced, not 1.5 + 0.5

    def test_zero_deletes_and_best_moves(self):
        t = self.b.apply_batch([["buy", "100.00", "0"]])
        self.assertEqual((t.bid_ticks, t.bid_units), (9999, 200000000))
        t = self.b.apply_batch([["sell", "100.01", "0"], ["sell", "100.03", "0.1"]])
        self.assertEqual((t.ask_ticks, t.ask_units), (10003, 10000000))
        self.assertEqual(self.b.stats["deletes"], 2)

    def test_delete_absent_level_counted(self):
        self.b.apply_batch([["buy", "50.00", "0"]])
        self.assertEqual(self.b.stats["delete_absent"], 1)
        self.assertEqual(self.b.state, "VALID")

    def test_resize_of_best_level(self):
        t = self.b.apply_batch([["buy", "100.00", "9"]])
        self.assertEqual(t.bid_units, 900000000)

    def test_batch_is_atomic(self):
        before = (dict(self.b.bids), dict(self.b.asks))
        t = self.b.apply_batch([["buy", "100.00", "7"], ["sell", "100.011", "1"]])   # 2nd off-tick
        self.assertIsNone(t)
        self.assertEqual(self.b.state, "INVALID")
        self.assertNotEqual((self.b.bids, self.b.asks), before)  # cleared, not partially applied
        self.assertEqual(self.b.bids, {})

    def test_top_emitted_after_whole_batch(self):
        # Intermediate state (bid 100.01 vs ask 100.01) would be locked; the full batch is not.
        t = self.b.apply_batch([["buy", "100.01", "1"], ["sell", "100.01", "0"]])
        self.assertEqual((t.bid_ticks, t.ask_ticks), (10001, 10005))

    def test_crossing_update_invalidates(self):
        self.assertIsNone(self.b.apply_batch([["buy", "100.02", "1"]]))
        self.assertEqual(self.b.invalid_reason, "crossed_or_locked")

    def test_malformed_changes(self):
        for bad in ([["hold", "100.00", "1"]], [["buy", "100.00"]], [], "x", [["buy", "100.00", "-1"]]):
            b = fresh()
            b.apply_snapshot(SNAP_B, SNAP_A)
            with self.subTest(bad=bad):
                self.assertIsNone(b.apply_batch(bad))
                self.assertEqual(b.state, "INVALID")

    def test_level_bound_not_silently_truncated(self):
        b = L2Book(U, max_levels=3)
        b.new_session()
        b.apply_snapshot(SNAP_B, SNAP_A)
        self.assertIsNone(b.apply_batch([["buy", "90.00", "1"]]))
        self.assertEqual(b.invalid_reason, "level bound exceeded")


class Sessions(unittest.TestCase):
    def test_disconnect_invalidates_and_rebuild_requires_snapshot(self):
        b = fresh()
        b.apply_snapshot(SNAP_B, SNAP_A)
        b.disconnect()
        self.assertEqual((b.state, b.top()), ("INVALID", None))
        self.assertIsNone(b.apply_batch([["buy", "100.00", "1"]]))     # ignored
        self.assertIsNone(b.apply_snapshot(SNAP_B, SNAP_A))           # same session: refused
        b.new_session()
        self.assertEqual(b.apply_snapshot([["99.00", "1"]], [["99.01", "1"]]), Top(9900, 100000000, 9901, 100000000))
        self.assertEqual(b.stats["snapshots"], 2)

    def test_deterministic(self):
        def run():
            b = fresh()
            out = [b.apply_snapshot(SNAP_B, SNAP_A)]
            for ch in ([["buy", "100.00", "0"]], [["sell", "100.02", "1"]], [["buy", "99.99", "0.1"]]):
                out.append(b.apply_batch(ch))
            return out, b.stats
        self.assertEqual(run(), run())


if __name__ == "__main__":
    unittest.main()
