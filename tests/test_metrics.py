import unittest

from quote_gate.metrics import Ring, Samples, percentile


class RingTests(unittest.TestCase):
    def test_bounded_and_ordered(self):
        r = Ring(3)
        for i in range(5):
            r.add((i,))
        self.assertEqual(r.items(), [(2,), (3,), (4,)])
        self.assertEqual((r.count, r.dropped), (5, 2))

    def test_under_capacity(self):
        r = Ring(4)
        r.add((1,))
        self.assertEqual((r.items(), r.dropped), ([(1,)], 0))


class SampleTests(unittest.TestCase):
    def test_nearest_rank(self):
        vals = list(range(1, 101))
        self.assertEqual([percentile(vals, p) for p in (50, 95, 99)], [50, 95, 99])
        self.assertEqual(percentile([7], 99), 7)

    def test_summary_window(self):
        s = Samples(2)
        for v in (5, 1, 9):
            s.add(v)
        self.assertEqual(s.summary()["count"], 3)
        self.assertEqual(s.summary()["retained"], 2)


if __name__ == "__main__":
    unittest.main()
