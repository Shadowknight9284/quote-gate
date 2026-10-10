"""M1 deterministic rule tests: validation, sequencing, epochs, freshness, routing.

Time is injected as integer nanoseconds; no real clock is read.
"""
import unittest

from quote_gate.book import Book, FeedStatus, Quote, Reject
from quote_gate.router import INSUFFICIENT_QUANTITY, Side, route

MS = 1_000_000
STALE = 100 * MS


def q(venue, seq, bid, ask, bq=10, aq=10, symbol="SYN1"):
    return Quote(venue, symbol, seq, bid, ask, bq, aq)


class Fixture(unittest.TestCase):
    """The specified two-venue fixture: A ask 10001, B ask 10003."""

    def setUp(self):
        self.book = Book("SYN1", ["A", "B"], STALE)
        self.ea = self.book.on_connect("A")
        self.eb = self.book.on_connect("B")
        self.assertIsNone(self.book.on_quote("A", self.ea, q("A", 1, 9999, 10001), 0))
        self.assertIsNone(self.book.on_quote("B", self.eb, q("B", 1, 10000, 10003), 50 * MS))

    def test_both_fresh_buy_selects_a(self):
        d = route(self.book, Side.BUY, 5, 60 * MS)
        self.assertEqual((d.venue_id, d.price_ticks, d.quote_sequence, d.connection_epoch),
                         ("A", 10001, 1, self.ea))

    def test_a_stale_buy_selects_b(self):
        d = route(self.book, Side.BUY, 5, 100 * MS)      # A age == STALE -> stale
        self.assertEqual(d.venue_id, "B")
        self.assertIn(("A", "stale"), d.excluded)

    def test_a_fresh_just_before_threshold(self):
        self.assertEqual(route(self.book, Side.BUY, 5, 100 * MS - 1).venue_id, "A")

    def test_both_stale_no_route(self):
        d = route(self.book, Side.BUY, 5, 150 * MS)
        self.assertFalse(d.routed)
        self.assertIsNone(d.price_ticks)
        self.assertEqual(d.excluded, (("A", "stale"), ("B", "stale")))

    def test_both_disconnected_no_route(self):
        self.book.on_disconnect("A", self.ea)
        self.book.on_disconnect("B", self.eb)
        d = route(self.book, Side.SELL, 1, 60 * MS)
        self.assertFalse(d.routed)
        self.assertEqual(d.excluded, (("A", "disconnected"), ("B", "disconnected")))

    def test_sell_selects_highest_bid(self):
        self.assertEqual(route(self.book, Side.SELL, 5, 60 * MS).venue_id, "B")

    def test_sell_b_stale_selects_a(self):
        self.book.on_quote("A", self.ea, q("A", 2, 9999, 10001), 120 * MS)
        self.assertEqual(route(self.book, Side.SELL, 5, 151 * MS).venue_id, "A")

    def test_tie_breaks_to_lowest_venue_id(self):
        self.book.on_quote("B", self.eb, q("B", 2, 9999, 10001), 60 * MS)
        self.assertEqual(route(self.book, Side.BUY, 1, 61 * MS).venue_id, "A")
        self.assertEqual(route(self.book, Side.SELL, 1, 61 * MS).venue_id, "A")

    def test_insufficient_quantity_skips_venue(self):
        self.book.on_quote("A", self.ea, q("A", 2, 9999, 10001, aq=3), 60 * MS)
        d = route(self.book, Side.BUY, 4, 61 * MS)
        self.assertEqual(d.venue_id, "B")
        self.assertIn(("A", INSUFFICIENT_QUANTITY), d.excluded)
        self.assertEqual(route(self.book, Side.BUY, 3, 61 * MS).venue_id, "A")   # exact fill ok

    def test_insufficient_everywhere_no_route(self):
        self.assertFalse(route(self.book, Side.BUY, 11, 60 * MS).routed)

    def test_invalid_order_args(self):
        for side, qty in ((Side.BUY, 0), (Side.BUY, -1), (Side.BUY, 1.0), (Side.BUY, True),
                          ("buy", 1)):
            with self.subTest(side=side, qty=qty), self.assertRaises(ValueError):
                route(self.book, side, qty, 60 * MS)


class Sequencing(unittest.TestCase):
    def setUp(self):
        self.book = Book("SYN1", ["A"], STALE)
        self.e = self.book.on_connect("A")
        self.book.on_quote("A", self.e, q("A", 5, 100, 101), 0)

    def test_duplicate_and_old_do_not_roll_back(self):
        self.assertEqual(self.book.on_quote("A", self.e, q("A", 5, 50, 51), 1), Reject.DUPLICATE)
        self.assertEqual(self.book.on_quote("A", self.e, q("A", 4, 50, 51), 2), Reject.OUT_OF_ORDER)
        st = self.book.venues["A"]
        self.assertEqual((st.quote.bid_ticks, st.last_sequence, st.received_monotonic_ns), (100, 5, 0))
        self.assertEqual(self.book.status("A", 3), FeedStatus.FRESH)   # still usable
        self.assertEqual(st.rejected, {"duplicate": 1, "out_of_order": 1})

    def test_gap_accepted_and_counted(self):
        self.assertIsNone(self.book.on_quote("A", self.e, q("A", 8, 100, 102), 1))
        self.assertEqual(self.book.venues["A"].gaps, 1)

    def test_reconnect_resets_sequence_and_requires_fresh_quote(self):
        self.book.on_disconnect("A", self.e)
        self.assertEqual(self.book.status("A", 1), FeedStatus.DISCONNECTED)
        e2 = self.book.on_connect("A")
        self.assertEqual(e2, self.e + 1)
        self.assertEqual(self.book.status("A", 1), FeedStatus.NO_QUOTE)
        self.assertFalse(route(self.book, Side.BUY, 1, 1).routed)
        self.assertIsNone(self.book.on_quote("A", e2, q("A", 1, 200, 201), 2))  # seq reset ok
        self.assertEqual(route(self.book, Side.BUY, 1, 3).price_ticks, 201)

    def test_old_epoch_message_ignored(self):
        e2 = self.book.on_connect("A")       # reconnect without explicit disconnect
        self.assertEqual(self.book.on_quote("A", self.e, q("A", 9, 1, 2), 1), Reject.OLD_EPOCH)
        self.book.on_disconnect("A", self.e)  # late disconnect from old session: no effect
        self.assertEqual(self.book.status("A", 1), FeedStatus.NO_QUOTE)
        self.assertEqual(self.book.venues["A"].connection_epoch, e2)

    def test_quote_while_disconnected_rejected(self):
        self.book.on_disconnect("A", self.e)
        self.assertEqual(self.book.on_quote("A", self.e, q("A", 6, 1, 2), 1), Reject.NOT_CONNECTED)


class InvalidInput(unittest.TestCase):
    def setUp(self):
        self.book = Book("SYN1", ["A"], STALE)
        self.e = self.book.on_connect("A")
        self.book.on_quote("A", self.e, q("A", 1, 100, 101), 0)

    def test_rejections(self):
        cases = [
            (q("A", 2, 101, 100), Reject.CROSSED),
            (q("A", 2, 100, 100), Reject.CROSSED),            # locked
            (q("A", 2, 100, 101, bq=0), Reject.BAD_QUANTITY),
            (q("A", 2, 100, 101, aq=-5), Reject.BAD_QUANTITY),
            (q("A", 2, 100.0, 101), Reject.MALFORMED),
            (q("A", 2, True, 101), Reject.MALFORMED),
            (q("A", 0, 100, 101), Reject.MALFORMED),
            (q("A", 2, 0, 101), Reject.MALFORMED),
            (q("A", 2, 100, 101, symbol="OTHER"), Reject.WRONG_SYMBOL),
            (q("B", 2, 100, 101), Reject.WRONG_VENUE),
        ]
        for quote, reason in cases:
            with self.subTest(quote=quote):
                self.assertEqual(self.book.on_quote("A", self.e, quote, 1), reason)

    def test_invalid_quote_invalidates_until_next_valid(self):
        self.book.on_quote("A", self.e, q("A", 2, 101, 100), 1)
        self.assertEqual(self.book.status("A", 2), FeedStatus.INVALID)
        self.assertFalse(route(self.book, Side.BUY, 1, 2).routed)
        self.assertIsNone(self.book.on_quote("A", self.e, q("A", 3, 100, 102), 3))
        self.assertEqual(route(self.book, Side.BUY, 1, 4).price_ticks, 102)

    def test_stale_after_must_be_positive(self):
        with self.assertRaises(ValueError):
            Book("SYN1", ["A"], 0)


if __name__ == "__main__":
    unittest.main()
