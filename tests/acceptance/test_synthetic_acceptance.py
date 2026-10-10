"""Claude-authored acceptance tests for contract T1 (independent of the delegated tests)."""
import random
import unittest

from quote_gate import synthetic as s


def reference(seed, count, cfg):
    rng = random.Random(seed)
    bid = cfg.start_bid_ticks
    out = []
    for i in range(count):
        if i > 0:
            bid = max(1, bid + rng.randint(-cfg.max_step_ticks, cfg.max_step_ticks))
        spread = rng.randint(cfg.min_spread_ticks, cfg.max_spread_ticks)
        bq = rng.randint(cfg.min_quantity, cfg.max_quantity)
        aq = rng.randint(cfg.min_quantity, cfg.max_quantity)
        out.append((cfg.venue_id, cfg.symbol, cfg.start_sequence + i, bid, bid + spread, bq, aq))
    return out


def as_tuples(events):
    return [(e.venue_id, e.symbol, e.sequence, e.bid_ticks, e.ask_ticks,
             e.bid_quantity, e.ask_quantity) for e in events]


class SyntheticAcceptance(unittest.TestCase):
    def test_matches_reference_draw_order(self):
        for cfg in (s.GeneratorConfig(),
                    s.GeneratorConfig(venue_id="B", start_bid_ticks=3, max_step_ticks=5,
                                      min_spread_ticks=2, max_spread_ticks=2,
                                      min_quantity=7, max_quantity=9, start_sequence=100)):
            for seed in (0, 1, 42, 2**40):
                self.assertEqual(as_tuples(s.generate_quotes(seed, 500, cfg)),
                                 reference(seed, 500, cfg))

    def test_default_config_and_prefix(self):
        self.assertEqual(s.generate_quotes(7, 100), s.generate_quotes(7, 100, s.GeneratorConfig()))
        self.assertEqual(s.generate_quotes(7, 100)[:30], s.generate_quotes(7, 30))

    def test_types_and_invariants(self):
        evs = s.generate_quotes(3, 2000, s.GeneratorConfig(start_bid_ticks=1, max_step_ticks=3))
        self.assertEqual(len(evs), 2000)
        for i, e in enumerate(evs):
            self.assertIs(type(e), s.QuoteEvent)
            for f in (e.sequence, e.bid_ticks, e.ask_ticks, e.bid_quantity, e.ask_quantity):
                self.assertIs(type(f), int)
            self.assertEqual(e.sequence, 1 + i)
            self.assertGreaterEqual(e.bid_ticks, 1)
            self.assertTrue(1 <= e.ask_ticks - e.bid_ticks <= 4)
            self.assertTrue(1 <= e.bid_quantity <= 1000 and 1 <= e.ask_quantity <= 1000)

    def test_zero_and_frozen(self):
        self.assertEqual(s.generate_quotes(1, 0), [])
        e = s.generate_quotes(1, 1)[0]
        with self.assertRaises(Exception):
            e.bid_ticks = 5

    def test_eager_validation(self):
        with self.assertRaises(ValueError):
            s.iter_quotes(0, -1)
        with self.assertRaises(ValueError):
            s.iter_quotes(0, s.MAX_EVENTS + 1)
        with self.assertRaises(TypeError):
            s.iter_quotes(True, 1)
        with self.assertRaises(TypeError):
            s.iter_quotes(0, False)
        with self.assertRaises(TypeError):
            s.iter_quotes(0, 1.0)
        with self.assertRaises(TypeError):
            s.iter_quotes(0, 1, config={})

    def test_config_validation(self):
        bad_values = [dict(venue_id=""), dict(symbol=""), dict(start_bid_ticks=0),
                      dict(max_step_ticks=-1), dict(min_spread_ticks=0),
                      dict(min_spread_ticks=3, max_spread_ticks=2), dict(min_quantity=0),
                      dict(min_quantity=5, max_quantity=4), dict(start_sequence=0)]
        for kw in bad_values:
            with self.subTest(kw=kw), self.assertRaises(ValueError):
                s.GeneratorConfig(**kw)
        bad_types = [dict(venue_id=1), dict(symbol=None), dict(start_bid_ticks=1.0),
                     dict(max_quantity=True), dict(start_sequence="1")]
        for kw in bad_types:
            with self.subTest(kw=kw), self.assertRaises(TypeError):
                s.GeneratorConfig(**kw)


if __name__ == "__main__":
    unittest.main()
