"""Claude-authored acceptance tests for contract T2."""
import unittest

from quote_gate.report import render_markdown

HEADER = ("| trial | offered | completed | not_sent_backlog | steady_samples | throughput rps | "
          "send-to-completion p50/p95/p99 µs | schedule-to-completion p50/p95/p99 µs | generator-delay p99 µs | errors |")


def lat(p50, p95, p99, ok=True):
    return {"p50": p50, "p95": p95, "p99": p99, "max": p99, "p99_meaningful": ok}


SUMMARY = {
    "meta": {"run_id": "r1", "label": "lbl", "git": "abc1234", "python": "3.12.3", "rate": 100,
             "duration_s": 5, "warmup_s": 1, "connections": 2, "trials": 2,
             "instrumentation": "inst"},
    "trials": [
        {"trial": 0, "offered": 500, "completed": 500, "not_sent_backlog": 0, "steady_samples": 400,
         "steady_throughput_rps": 100.0, "outcomes": {"B": 3, "A": 497},
         "send_to_completion_us": lat(1.5, 2.5, 3.5, False), "schedule_to_completion_us": lat(4, 5, 6, False),
         "generator_delay_us": lat(7, 8, 9, False)},
        {"trial": 1, "offered": 500, "completed": 497, "not_sent_backlog": 3, "steady_samples": 1200,
         "steady_throughput_rps": 99.4, "outcomes": {"A": 490, "ERROR": 2, "FAIL:TimeoutError": 5,
                                                     "NO_ROUTE": 4},
         "send_to_completion_us": lat(1, 2, 3), "generator_delay_us": lat(7, 8, 9)},
    ],
}


class ReportAcceptance(unittest.TestCase):
    def setUp(self):
        self.md = render_markdown(SUMMARY)
        self.lines = self.md.split("\n")

    def test_title_meta_and_ending(self):
        self.assertEqual(self.lines[0], "# Benchmark: lbl")
        self.assertIn("- offered rate: 100 req/s", self.lines)
        self.assertIn("- duration: 5 s (warm-up 1 s excluded)", self.lines)
        self.assertTrue(self.md.endswith("\n") and not self.md.endswith("\n\n"))

    def test_trial_rows(self):
        i = self.lines.index(HEADER)
        r0 = [c.strip() for c in self.lines[i + 2].strip("|").split("|")]
        r1 = [c.strip() for c in self.lines[i + 3].strip("|").split("|")]
        self.assertEqual(r0, ["0", "500", "500", "0", "400", "100.0", "1.5 / 2.5 / 3.5 *",
                              "4 / 5 / 6 *", "9 *", "0"])
        self.assertEqual(r1, ["1", "500", "497", "3", "1200", "99.4", "1 / 2 / 3", "n/a", "9", "7"])

    def test_outcomes_sorted(self):
        i = self.lines.index("| trial | outcome | count |")
        rows = [l for l in self.lines[i + 2:] if l.startswith("|")][:6]
        keys = [tuple(c.strip() for c in r.strip("|").split("|")) for r in rows]
        self.assertEqual(keys, [("0", "A", "497"), ("0", "B", "3"), ("1", "A", "490"),
                                ("1", "ERROR", "2"), ("1", "FAIL:TimeoutError", "5"),
                                ("1", "NO_ROUTE", "4")])

    def test_notes_and_missing_meta(self):
        self.assertIn("- Latency is measured on the client host only (monotonic clock); "
                      "no cross-host one-way latency is claimed.", self.lines)
        md = render_markdown({"meta": {}, "trials": []})
        self.assertIn("- git: n/a", md.split("\n"))

    def test_invalid(self):
        for bad in ({"meta": {}}, {"trials": []}, {"meta": {}, "trials": {}}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                render_markdown(bad)


if __name__ == "__main__":
    unittest.main()
