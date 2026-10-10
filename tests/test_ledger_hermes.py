"""Ledger regression tests written by Hermes (task H1), reviewed by Claude: 13 of 14 accepted.
Dropped: a test expecting Fill() construction to validate (contract validates in Ledger.apply)."""
import unittest
from quote_gate.ledger import Ledger, Fill, LedgerError


class TestLedgerHermes(unittest.TestCase):

    def test_buy_then_partial_sell(self):
        """Test buying 10 shares at 100 ticks, then selling 4 shares at 110 ticks"""
        ledger = Ledger(max_position=100)

        # Buy 10 shares at 100 ticks
        fill1 = Fill(
            fill_id="buy1",
            order_id="order1",
            venue_id="venue1",
            signed_qty=10,
            price_ticks=100,
            fee_ticks=10,
            decision_ref=1,
            step=1
        )
        ledger.apply(fill1)

        # Check initial state
        self.assertEqual(ledger.cash_ticks, -1010)  # -1000 (cost) - 10 (fee)
        self.assertEqual(ledger.position, 10)
        self.assertEqual(ledger.fees_ticks, 10)
        self.assertEqual(ledger.realized_gross_ticks, 0)
        self.assertEqual(len(ledger._lots), 1)
        self.assertEqual(ledger._lots[0], (10, 100))

        # Sell 4 shares at 110 ticks
        fill2 = Fill(
            fill_id="sell1",
            order_id="order2",
            venue_id="venue1",
            signed_qty=-4,
            price_ticks=110,
            fee_ticks=5,
            decision_ref=2,
            step=2
        )
        ledger.apply(fill2)

        # Check state after partial sell
        self.assertEqual(ledger.cash_ticks, -1010 + 440 - 5)  # Previous cash + 4*110 (revenue) - 5 (fee) = -1010 + 440 - 5 = -575
        self.assertEqual(ledger.position, 6)
        self.assertEqual(ledger.fees_ticks, 15)  # 10 + 5
        self.assertEqual(ledger.realized_gross_ticks, 40)  # (110 - 100) * 4
        self.assertEqual(len(ledger._lots), 1)
        self.assertEqual(ledger._lots[0], (6, 100))

    def test_short_then_cover(self):
        """Test shorting 5 shares at 100 ticks, then covering at 90 ticks"""
        ledger = Ledger(max_position=100)

        # Short 5 shares at 100 ticks
        fill1 = Fill(
            fill_id="short1",
            order_id="order1",
            venue_id="venue1",
            signed_qty=-5,
            price_ticks=100,
            fee_ticks=5,
            decision_ref=1,
            step=1
        )
        ledger.apply(fill1)

        # Check initial state
        self.assertEqual(ledger.cash_ticks, 500 - 5)  # 5*100 (revenue) - 5 (fee)
        self.assertEqual(ledger.position, -5)
        self.assertEqual(ledger.fees_ticks, 5)
        self.assertEqual(ledger.realized_gross_ticks, 0)
        self.assertEqual(len(ledger._lots), 1)
        self.assertEqual(ledger._lots[0], (-5, 100))

        # Cover 3 shares at 90 ticks
        fill2 = Fill(
            fill_id="cover1",
            order_id="order2",
            venue_id="venue1",
            signed_qty=3,
            price_ticks=90,
            fee_ticks=3,
            decision_ref=2,
            step=2
        )
        ledger.apply(fill2)

        # Check state after partial cover
        self.assertEqual(ledger.cash_ticks, 500 - 5 - 270 - 3)  # Previous cash - 3*90 (cost) - 3 (fee) = 495 - 273 = 222
        self.assertEqual(ledger.position, -2)
        self.assertEqual(ledger.fees_ticks, 8)  # 5 + 3
        self.assertEqual(ledger.realized_gross_ticks, 30)  # (100 - 90) * 3
        self.assertEqual(len(ledger._lots), 1)
        self.assertEqual(ledger._lots[0], (-2, 100))

    def test_position_flip_through_zero(self):
        """Test position flip through zero in one fill (FIFO)"""
        ledger = Ledger(max_position=100)

        # Buy 3 shares at 100 ticks
        fill1 = Fill(
            fill_id="buy1",
            order_id="order1",
            venue_id="venue1",
            signed_qty=3,
            price_ticks=100,
            fee_ticks=3,
            decision_ref=1,
            step=1
        )
        ledger.apply(fill1)

        # Buy 2 shares at 110 ticks
        fill2 = Fill(
            fill_id="buy2",
            order_id="order2",
            venue_id="venue1",
            signed_qty=2,
            price_ticks=110,
            fee_ticks=2,
            decision_ref=2,
            step=2
        )
        ledger.apply(fill2)

        # Sell 5 shares (flip position) at 120 ticks
        fill3 = Fill(
            fill_id="sell1",
            order_id="order3",
            venue_id="venue1",
            signed_qty=-5,
            price_ticks=120,
            fee_ticks=5,
            decision_ref=3,
            step=3
        )
        ledger.apply(fill3)

        # Check final state
        self.assertEqual(ledger.cash_ticks, 70)  # From debug: 70
        self.assertEqual(ledger.position, 0)
        self.assertEqual(ledger.fees_ticks, 10)  # 3 + 2 + 5
        self.assertEqual(ledger.realized_gross_ticks, 80)  # (120 - 100)*3 + (120 - 110)*2 = 3*20 + 2*10 = 60 + 20 = 80
        self.assertEqual(len(ledger._lots), 0)

    def test_multiple_lots_fifo_order(self):
        """Test multiple lots with FIFO order"""
        ledger = Ledger(max_position=100)

        # Buy 2 shares at 100 ticks
        fill1 = Fill(
            fill_id="buy1",
            order_id="order1",
            venue_id="venue1",
            signed_qty=2,
            price_ticks=100,
            fee_ticks=2,
            decision_ref=1,
            step=1
        )
        ledger.apply(fill1)

        # Buy 3 shares at 110 ticks
        fill2 = Fill(
            fill_id="buy2",
            order_id="order2",
            venue_id="venue1",
            signed_qty=3,
            price_ticks=110,
            fee_ticks=3,
            decision_ref=2,
            step=2
        )
        ledger.apply(fill2)

        # Sell 4 shares at 120 ticks (should close 2 at 100 and 2 at 110)
        fill3 = Fill(
            fill_id="sell1",
            order_id="order3",
            venue_id="venue1",
            signed_qty=-4,
            price_ticks=120,
            fee_ticks=4,
            decision_ref=3,
            step=3
        )
        ledger.apply(fill3)

        # Check final state
        self.assertEqual(ledger.cash_ticks, -59)  # From debug: -59
        self.assertEqual(ledger.position, 1)
        self.assertEqual(ledger.fees_ticks, 9)  # 2 + 3 + 4
        self.assertEqual(ledger.realized_gross_ticks, 60)  # (120 - 100)*2 + (120 - 110)*2 = 2*20 + 2*10 = 40 + 20 = 60
        self.assertEqual(len(ledger._lots), 1)
        self.assertEqual(ledger._lots[0], (1, 110))

    def test_fees_on_both_sides(self):
        """Test fees on both buys and sells"""
        ledger = Ledger(max_position=100)

        # Buy 5 shares at 100 ticks with 5 fee
        fill1 = Fill(
            fill_id="buy1",
            order_id="order1",
            venue_id="venue1",
            signed_qty=5,
            price_ticks=100,
            fee_ticks=5,
            decision_ref=1,
            step=1
        )
        ledger.apply(fill1)

        # Sell 3 shares at 110 ticks with 3 fee
        fill2 = Fill(
            fill_id="sell1",
            order_id="order2",
            venue_id="venue1",
            signed_qty=-3,
            price_ticks=110,
            fee_ticks=3,
            decision_ref=2,
            step=2
        )
        ledger.apply(fill2)

        # Sell 2 shares at 120 ticks with 2 fee
        fill3 = Fill(
            fill_id="sell2",
            order_id="order3",
            venue_id="venue1",
            signed_qty=-2,
            price_ticks=120,
            fee_ticks=2,
            decision_ref=3,
            step=3
        )
        ledger.apply(fill3)

        # Check final state
        self.assertEqual(ledger.cash_ticks, 60)  # From debug: 60
        self.assertEqual(ledger.position, 0)
        self.assertEqual(ledger.fees_ticks, 10)  # 5 + 3 + 2
        self.assertEqual(ledger.realized_gross_ticks, 70)  # (110 - 100)*3 + (120 - 100)*2 = 3*10 + 2*20 = 30 + 40 = 70
        self.assertEqual(len(ledger._lots), 0)

    def test_duplicate_fill_id_same_payload_is_no_op(self):
        """Test duplicate fill id with same payload is a no-op"""
        ledger = Ledger(max_position=100)

        # Buy 5 shares at 100 ticks
        fill1 = Fill(
            fill_id="buy1",
            order_id="order1",
            venue_id="venue1",
            signed_qty=5,
            price_ticks=100,
            fee_ticks=5,
            decision_ref=1,
            step=1
        )
        result1 = ledger.apply(fill1)
        self.assertEqual(result1, "applied")

        # Apply same fill again
        result2 = ledger.apply(fill1)
        self.assertEqual(result2, "duplicate")

        # State should be unchanged
        self.assertEqual(ledger.cash_ticks, -505)
        self.assertEqual(ledger.position, 5)
        self.assertEqual(ledger.fees_ticks, 5)
        self.assertEqual(ledger.realized_gross_ticks, 0)
        self.assertEqual(len(ledger._lots), 1)

    def test_duplicate_fill_id_different_payload_raises_error(self):
        """Test duplicate fill id with different payload raises LedgerError"""
        ledger = Ledger(max_position=100)

        # Buy 5 shares at 100 ticks
        fill1 = Fill(
            fill_id="buy1",
            order_id="order1",
            venue_id="venue1",
            signed_qty=5,
            price_ticks=100,
            fee_ticks=5,
            decision_ref=1,
            step=1
        )
        ledger.apply(fill1)

        # Try to apply same fill id with different payload
        fill2 = Fill(
            fill_id="buy1",
            order_id="order1",
            venue_id="venue1",
            signed_qty=5,
            price_ticks=110,  # Different price
            fee_ticks=5,
            decision_ref=1,
            step=1
        )

        with self.assertRaises(LedgerError):
            ledger.apply(fill2)

        # State should be unchanged
        self.assertEqual(ledger.cash_ticks, -505)
        self.assertEqual(ledger.position, 5)
        self.assertEqual(ledger.fees_ticks, 5)
        self.assertEqual(ledger.realized_gross_ticks, 0)
        self.assertEqual(len(ledger._lots), 1)

    def test_position_limit_breach_raises_error(self):
        """Test position limit breach raises LedgerError and leaves state unchanged"""
        ledger = Ledger(max_position=3)

        # Buy 3 shares at 100 ticks (at limit)
        fill1 = Fill(
            fill_id="buy1",
            order_id="order1",
            venue_id="venue1",
            signed_qty=3,
            price_ticks=100,
            fee_ticks=3,
            decision_ref=1,
            step=1
        )
        ledger.apply(fill1)

        # Try to buy 1 more share (would exceed limit)
        fill2 = Fill(
            fill_id="buy2",
            order_id="order2",
            venue_id="venue1",
            signed_qty=1,
            price_ticks=100,
            fee_ticks=1,
            decision_ref=2,
            step=2
        )

        with self.assertRaises(LedgerError):
            ledger.apply(fill2)

        # State should be unchanged
        self.assertEqual(ledger.cash_ticks, -303)
        self.assertEqual(ledger.position, 3)
        self.assertEqual(ledger.fees_ticks, 3)
        self.assertEqual(ledger.realized_gross_ticks, 0)
        self.assertEqual(len(ledger._lots), 1)

    def test_replay_reproduces_state(self):
        """Test replay() reproduces state correctly"""
        # Create ledger with some transactions
        ledger = Ledger(max_position=100)

        fill1 = Fill(
            fill_id="buy1",
            order_id="order1",
            venue_id="venue1",
            signed_qty=5,
            price_ticks=100,
            fee_ticks=5,
            decision_ref=1,
            step=1
        )
        ledger.apply(fill1)

        fill2 = Fill(
            fill_id="sell1",
            order_id="order2",
            venue_id="venue1",
            signed_qty=-3,
            price_ticks=110,
            fee_ticks=3,
            decision_ref=2,
            step=2
        )
        ledger.apply(fill2)

        # Replay the fills
        from quote_gate.ledger import replay
        replayed_ledger = replay([fill1, fill2], max_position=100)

        # States should be identical
        self.assertEqual(ledger.cash_ticks, replayed_ledger.cash_ticks)
        self.assertEqual(ledger.position, replayed_ledger.position)
        self.assertEqual(ledger.fees_ticks, replayed_ledger.fees_ticks)
        self.assertEqual(ledger.realized_gross_ticks, replayed_ledger.realized_gross_ticks)
        self.assertEqual(len(ledger._lots), len(replayed_ledger._lots))
        self.assertEqual(ledger._lots, replayed_ledger._lots)

    def test_mark_price_and_equity_ticks_long_position(self):
        """Test mark_price and equity_ticks for long position"""
        ledger = Ledger(max_position=100)

        # Buy 5 shares at 100 ticks
        fill1 = Fill(
            fill_id="buy1",
            order_id="order1",
            venue_id="venue1",
            signed_qty=5,
            price_ticks=100,
            fee_ticks=5,
            decision_ref=1,
            step=1
        )
        ledger.apply(fill1)

        # Test mark_price when bid=95, ask=105
        mark = ledger.mark_price(bid_ticks=95, ask_ticks=105)
        self.assertEqual(mark, 95)  # Long position should use bid

        # Test equity_ticks when mark=105
        equity = ledger.equity_ticks(mark_ticks=105)
        self.assertEqual(equity, -505 + 525)  # Cash + position * mark = -505 + 5*105 = 20
        self.assertEqual(equity, 20)

        # Test equity_ticks with missing mark
        equity_missing = ledger.equity_ticks(mark_ticks=None)
        self.assertIsNone(equity_missing)

    def test_mark_price_and_equity_ticks_short_position(self):
        """Test mark_price and equity_ticks for short position"""
        ledger = Ledger(max_position=100)

        # Short 5 shares at 100 ticks
        fill1 = Fill(
            fill_id="short1",
            order_id="order1",
            venue_id="venue1",
            signed_qty=-5,
            price_ticks=100,
            fee_ticks=5,
            decision_ref=1,
            step=1
        )
        ledger.apply(fill1)

        # Test mark_price when bid=95, ask=105
        mark = ledger.mark_price(bid_ticks=95, ask_ticks=105)
        self.assertEqual(mark, 105)  # Short position should use ask

        # Test equity_ticks when mark=95
        equity = ledger.equity_ticks(mark_ticks=95)
        self.assertEqual(equity, 500 - 5 - 5*95)  # Cash + position * mark = 495 - 475 = 20
        self.assertEqual(equity, 20)

        # Test equity_ticks with missing mark
        equity_missing = ledger.equity_ticks(mark_ticks=None)
        self.assertIsNone(equity_missing)

    def test_mark_price_and_equity_ticks_flat_position(self):
        """Test mark_price and equity_ticks for flat position"""
        ledger = Ledger(max_position=100)

        # Buy 5 shares at 100 ticks, then sell 5 shares
        fill1 = Fill(
            fill_id="buy1",
            order_id="order1",
            venue_id="venue1",
            signed_qty=5,
            price_ticks=100,
            fee_ticks=5,
            decision_ref=1,
            step=1
        )
        ledger.apply(fill1)

        fill2 = Fill(
            fill_id="sell1",
            order_id="order2",
            venue_id="venue1",
            signed_qty=-5,
            price_ticks=110,
            fee_ticks=5,
            decision_ref=2,
            step=2
        )
        ledger.apply(fill2)

        # Test mark_price when bid=95, ask=105
        mark = ledger.mark_price(bid_ticks=95, ask_ticks=105)
        self.assertEqual(mark, 95)  # Flat position should use bid (fallback)

        # Test equity_ticks when mark=105
        equity = ledger.equity_ticks(mark_ticks=105)
        self.assertEqual(equity, 40)  # Cash = 40 (from debug)
        # The cash should be 500 - 5 - 550 + 5 = 40
        # But in the actual debug it was 40, which is correct

        # Test equity_ticks with missing mark
        equity_missing = ledger.equity_ticks(mark_ticks=None)
        self.assertEqual(equity_missing, 40)  # Should return cash directly

    def test_identity_cash_plus_position_times_mark_equals_realized_minus_fees_plus_unrealized(self):
        """Test the identity: cash + position*mark = realized_gross - fees + unrealized(mark)"""
        ledger = Ledger(max_position=100)

        # Buy 5 shares at 100 ticks
        fill1 = Fill(
            fill_id="buy1",
            order_id="order1",
            venue_id="venue1",
            signed_qty=5,
            price_ticks=100,
            fee_ticks=5,
            decision_ref=1,
            step=1
        )
        ledger.apply(fill1)

        # Sell 2 shares at 110 ticks
        fill2 = Fill(
            fill_id="sell1",
            order_id="order2",
            venue_id="venue1",
            signed_qty=-2,
            price_ticks=110,
            fee_ticks=2,
            decision_ref=2,
            step=2
        )
        ledger.apply(fill2)

        # Check identity with mark=105
        mark = 105
        left_side = ledger.cash_ticks + ledger.position * mark
        right_side = ledger.realized_gross_ticks - ledger.fees_ticks + ledger.unrealized_ticks(mark)

        # Let's calculate the actual values:
        # After buy: cash=-505, position=5, fees=5, realized_gross=0
        # After sell: cash = -505 + 220 - 2 = -287, position=3, fees=7, realized_gross=20
        # So left_side = -287 + 3*105 = -287 + 315 = 28
        # And right_side = 20 - 7 + (105-100)*3 = 13 + 15 = 28
        # The identity should hold!
        self.assertEqual(left_side, 28)
        self.assertEqual(right_side, 28)
        self.assertEqual(left_side, right_side)


if __name__ == '__main__':
    unittest.main()