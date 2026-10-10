"""Deterministic tests of benchmark summarization (no sockets, no timing)."""
import unittest

from quote_gate.bench import summarize


class Summarize(unittest.TestCase):
    def test_warmup_excluded_and_outcomes_counted(self):
        start = 1_000_000_000
        obs = [(start + i * 1_000_000, start + i * 1_000_000 + 10_000,
                start + i * 1_000_000 + 30_000, "A" if i % 2 else "NO_ROUTE") for i in range(2000)]
        trial = {"start_ns": start, "offered": 2001, "not_sent_backlog": 1, "observations": obs,
                 "generator_lag_ns": [5_000] * 2000, "server_after": {}}
        s = summarize(trial, warmup_s=0.5)       # first 500 scheduled are warm-up
        self.assertEqual(s["steady_samples"], 1500)
        self.assertEqual(s["completed"], 2000)
        self.assertEqual(s["not_sent_backlog"], 1)
        self.assertEqual(s["outcomes"], {"NO_ROUTE": 1000, "A": 1000})
        self.assertEqual(s["send_to_completion_us"]["p99"], 20.0)
        self.assertEqual(s["schedule_to_completion_us"]["p50"], 30.0)
        self.assertEqual(s["schedule_to_send_us"]["p50"], 10.0)
        self.assertEqual(s["generator_delay_us"]["p50"], 5.0)
        self.assertTrue(s["send_to_completion_us"]["p99_meaningful"])

    def test_small_sample_flagged(self):
        obs = [(0, 1, 2, "A")] * 10
        s = summarize({"start_ns": 0, "offered": 10, "not_sent_backlog": 0, "observations": obs,
                       "generator_lag_ns": [0] * 10, "server_after": {}}, 0)
        self.assertFalse(s["send_to_completion_us"]["p99_meaningful"])


if __name__ == "__main__":
    unittest.main()
