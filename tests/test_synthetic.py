import unittest
from quote_gate.synthetic import (
    MAX_EVENTS,
    QuoteEvent,
    GeneratorConfig,
    iter_quotes,
    generate_quotes
)


class TestSynthetic(unittest.TestCase):

    def test_same_seed_identical_events(self):
        """Test that same seed + config gives identical events"""
        config = GeneratorConfig()
        events1 = generate_quotes(42, 50, config)
        events2 = generate_quotes(42, 50, config)
        self.assertEqual(events1, events2)

    def test_different_seeds_different_events(self):
        """Test that different seeds give different events"""
        config = GeneratorConfig()
        events1 = generate_quotes(42, 50, config)
        events2 = generate_quotes(43, 50, config)
        # They should be different (very high probability)
        self.assertNotEqual(events1, events2)

    def test_output_length(self):
        """Test that output length equals requested count"""
        # Test count 0
        events = generate_quotes(42, 0)
        self.assertEqual(len(events), 0)

        # Test count 1
        events = generate_quotes(42, 1)
        self.assertEqual(len(events), 1)

        # Test count 1000
        events = generate_quotes(42, 1000)
        self.assertEqual(len(events), 1000)

    def test_sequence_numbers(self):
        """Test that sequence numbers start at start_sequence and increase by exactly 1"""
        config = GeneratorConfig(start_sequence=100)
        events = generate_quotes(42, 10, config)
        expected_sequences = list(range(100, 110))
        actual_sequences = [event.sequence for event in events]
        self.assertEqual(actual_sequences, expected_sequences)

    def test_spread_rule_and_bid_bounds(self):
        """Test that spread rule holds and bid >= 1"""
        config = GeneratorConfig()
        events = generate_quotes(42, 100, config)

        for event in events:
            # Check bid >= 1
            self.assertGreaterEqual(event.bid_ticks, 1)

            # Check spread rule
            spread = event.ask_ticks - event.bid_ticks
            self.assertGreaterEqual(spread, config.min_spread_ticks)
            self.assertLessEqual(spread, config.max_spread_ticks)

    def test_quantities_bounds(self):
        """Test that quantities are positive integers within configured bounds"""
        config = GeneratorConfig()
        events = generate_quotes(42, 100, config)

        for event in events:
            # Check quantities are positive integers within bounds
            self.assertGreaterEqual(event.bid_quantity, config.min_quantity)
            self.assertLessEqual(event.bid_quantity, config.max_quantity)
            self.assertGreaterEqual(event.ask_quantity, config.min_quantity)
            self.assertLessEqual(event.ask_quantity, config.max_quantity)
            self.assertIsInstance(event.bid_quantity, int)
            self.assertIsInstance(event.ask_quantity, int)

    def test_invalid_arguments_raise_correct_exceptions(self):
        """Test that invalid arguments raise the specified exception types"""
        # Test invalid seed (should be int)
        with self.assertRaises(TypeError):
            generate_quotes(42.0, 10)  # float instead of int

        # Test invalid count (should be int)
        with self.assertRaises(TypeError):
            generate_quotes(42, 10.0)  # float instead of int

        # Test invalid config (should be None or GeneratorConfig)
        with self.assertRaises(TypeError):
            generate_quotes(42, 10, "invalid_config")  # string instead of GeneratorConfig

        # Test negative count
        with self.assertRaises(ValueError):
            generate_quotes(42, -1)

        # Test count > MAX_EVENTS
        with self.assertRaises(ValueError):
            generate_quotes(42, MAX_EVENTS + 1)

        # Test invalid config fields
        # Empty venue_id
        with self.assertRaises(ValueError):
            GeneratorConfig(venue_id="")

        # Empty symbol
        with self.assertRaises(ValueError):
            GeneratorConfig(symbol="")

        # start_bid_ticks < 1
        with self.assertRaises(ValueError):
            GeneratorConfig(start_bid_ticks=0)

        # max_step_ticks < 0
        with self.assertRaises(ValueError):
            GeneratorConfig(max_step_ticks=-1)

        # min_spread_ticks < 1
        with self.assertRaises(ValueError):
            GeneratorConfig(min_spread_ticks=0)

        # max_spread_ticks < min_spread_ticks
        with self.assertRaises(ValueError):
            GeneratorConfig(min_spread_ticks=3, max_spread_ticks=2)

        # min_quantity < 1
        with self.assertRaises(ValueError):
            GeneratorConfig(min_quantity=0)

        # max_quantity < min_quantity
        with self.assertRaises(ValueError):
            GeneratorConfig(min_quantity=5, max_quantity=3)

        # start_sequence < 1
        with self.assertRaises(ValueError):
            GeneratorConfig(start_sequence=0)

        # Wrong types in config
        with self.assertRaises(TypeError):
            GeneratorConfig(venue_id=123)  # Should be str

        with self.assertRaises(TypeError):
            GeneratorConfig(symbol=123)  # Should be str

        with self.assertRaises(TypeError):
            GeneratorConfig(start_bid_ticks=123.0)  # Should be int

        with self.assertRaises(TypeError):
            GeneratorConfig(max_step_ticks=123.0)  # Should be int

    def test_zero_count_returns_empty_list(self):
        """Test that count == 0 returns []"""
        result = generate_quotes(42, 0)
        self.assertEqual(result, [])

    def test_validation_is_eager(self):
        """Test that validation happens at call time, not iteration time"""
        # This should raise ValueError immediately, not when iterating
        with self.assertRaises(ValueError):
            iter_quotes(0, -1)  # Negative count

    def test_bool_types_rejected(self):
        """Test that bool values are rejected as they're not int"""
        # Test bool seed
        with self.assertRaises(TypeError):
            iter_quotes(True, 1)

        # Test bool count
        with self.assertRaises(TypeError):
            iter_quotes(0, False)

        # Test bool in config
        with self.assertRaises(TypeError):
            GeneratorConfig(max_quantity=True)

    def test_prefix_property(self):
        """Test that generate_quotes(7, 100)[:30] == generate_quotes(7, 30)"""
        result_100 = generate_quotes(7, 100)
        result_30 = generate_quotes(7, 30)
        self.assertEqual(result_100[:30], result_30)


if __name__ == '__main__':
    unittest.main()
