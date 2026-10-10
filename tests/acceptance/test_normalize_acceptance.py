"""Claude-authored acceptance tests for contract T3 (docs/contracts/T3-normalize.md)."""
import unittest

from quote_gate import normalize as n

META = {"run_id": "R", "label": "lbl", "git": "abc", "python": "3.12.3", "rate": 2000,
        "duration_s": 10, "warmup_s": 2, "connections": 8, "trials": 3, "side": "buy",
        "operation": "route lookup", "target": "127.0.0.1:19200"}
HDR = {"schema": "qg.route_obs.v2", "observer": "bench@vm1", "anchor_wall_ns": 5_000_000_000,
       "anchor_mono_ns": 1_000, "fields": ["scheduled_mono_ns", "sent_mono_ns",
                                           "completed_mono_ns", "outcome", "quote_age_ns"]}


class RunRow(unittest.TestCase):
    def test_exact(self):
        r = n.run_row({"meta": META, "trials": [{}, {}]}, "cross_vm_lan")
        self.assertEqual(r, {"run_id": "R", "label": "lbl", "git": "abc", "python": "3.12.3",
                             "operation": "route lookup", "target": "127.0.0.1:19200",
                             "placement": "cross_vm_lan", "rate_rps": 2000.0, "duration_s": 10.0,
                             "warmup_s": 2.0, "connections": 8, "trials": 2})
        self.assertIs(type(r["rate_rps"]), float)

    def test_optional_absent_is_none(self):
        m = {k: META[k] for k in ("run_id", "label", "rate", "duration_s", "warmup_s", "connections")}
        r = n.run_row({"meta": m, "trials": []}, "loopback")
        self.assertIsNone(r["git"])
        self.assertIsNone(r["target"])

    def test_invalid(self):
        bad = [({"meta": {**META, "run_id": ""}, "trials": []}, "loopback"),
               ({"meta": {k: v for k, v in META.items() if k != "rate"}, "trials": []}, "loopback"),
               ({"meta": {**META, "connections": True}, "trials": []}, "loopback"),
               ({"meta": {**META, "rate": 0}, "trials": []}, "loopback"),
               ({"meta": META, "trials": []}, "datacenter")]
        for s, p in bad:
            with self.subTest(p=p), self.assertRaises(n.NormalizationError):
                n.run_row(s, p)
        self.assertTrue(issubclass(n.NormalizationError, ValueError))


class RouteRows(unittest.TestCase):
    def test_v1_exact_and_no_fabricated_time(self):
        lines = [[1000, 1010, 1100, "A"], [3_000_000_000, 3_000_000_005, 3_000_000_105, "NO_ROUTE"]]
        rows = n.route_sample_rows("R", 0, lines, 2.0)
        self.assertEqual(rows[0], {
            "sample_key": "R:route:0:0", "run_id": "R", "trial": 0, "row_index": 0, "schema": "v1",
            "observer": None, "scheduled_mono_ns": 1000, "sent_mono_ns": 1010,
            "completed_mono_ns": 1100, "send_to_completion_ns": 90,
            "schedule_to_completion_ns": 100, "schedule_to_send_ns": 10, "outcome": "A",
            "quote_age_ns": None, "warmup": True, "completed_at_utc_ns": None})
        self.assertFalse(rows[1]["warmup"])
        self.assertIsNone(rows[1]["completed_at_utc_ns"])

    def test_v2_utc_derivation(self):
        rows = n.route_sample_rows("R", 1, [HDR, [2000, 2100, 2500, "B", 7], [2600, 2600, 2700, "NO_ROUTE", None]], 0)
        self.assertEqual(rows[0]["completed_at_utc_ns"], 5_000_000_000 + 2500 - 1000)
        self.assertEqual((rows[0]["schema"], rows[0]["observer"], rows[0]["quote_age_ns"]), ("v2", "bench@vm1", 7))
        self.assertEqual([r["sample_key"] for r in rows], ["R:route:1:0", "R:route:1:1"])
        self.assertIsNone(rows[1]["quote_age_ns"])

    def test_warmup_boundary_and_start_is_min_scheduled(self):
        # completion order differs from schedule order; start = min(scheduled) = 100
        lines = [[100 + 2_000_000_000, 100 + 2_000_000_000, 100 + 2_000_000_001, "A"], [100, 100, 101, "A"]]
        rows = n.route_sample_rows("R", 0, lines, 2.0)
        self.assertFalse(rows[0]["warmup"])      # exactly start + warmup -> not warm-up
        self.assertTrue(rows[1]["warmup"])

    def test_malformed_rows_name_index(self):
        good = [1, 2, 3, "A"]
        for bad in ([1, 2, 3], [1.0, 2, 3, "A"], [True, 2, 3, "A"], [-1, 2, 3, "A"],
                    [5, 4, 6, "A"], [1, 2, 3, ""], [1, 2, 3, 7]):
            with self.subTest(bad=bad):
                with self.assertRaises(n.NormalizationError) as cm:
                    n.route_sample_rows("R", 0, [good, bad], 0)
                self.assertIn("1", str(cm.exception))

    def test_bad_v2_header(self):
        for hdr in ({**HDR, "schema": "qg.route_obs.v3"}, {**HDR, "fields": HDR["fields"][:4]}):
            with self.assertRaises(n.NormalizationError):
                n.route_sample_rows("R", 0, [hdr, [1, 2, 3, "A", None]], 0)
        with self.assertRaises(n.NormalizationError):     # v2 data row with v1 arity
            n.route_sample_rows("R", 0, [HDR, [1, 2, 3, "A"]], 0)

    def test_empty_stable_and_args(self):
        self.assertEqual(n.route_sample_rows("R", 0, [], 0), [])
        lines = [[i, i + 1, i + 2, "A"] for i in range(50)]
        a, b = n.route_sample_rows("R", 2, lines, 0), n.route_sample_rows("R", 2, lines, 0)
        self.assertEqual(a, b)
        self.assertEqual(len({r["sample_key"] for r in a}), 50)
        for trial in (-1, True, 1.0):
            with self.assertRaises(n.NormalizationError):
                n.route_sample_rows("R", trial, lines, 0)


class MetricRows(unittest.TestCase):
    TRIAL = {"trial": 2,
             "send_to_completion_us": {"p50": 150.4, "p95": 192.5, "p99": 227.7, "max": 342.5,
                                       "p99_meaningful": True},
             "schedule_to_completion_us": {"p50": 635.3, "p95": 1214.4, "p99": 1238.0, "max": 1336.7},
             "unknown_us": {"p50": 1},
             "server_cumulative": {
                 "route_ns": {"count": 9, "retained": 9, "p50": 3597, "p95": 4188, "p99": 6011, "max": 41168},
                 "process_ns": {"count": 4, "p50": 1, "p95": 2, "p99": 3, "max": 4},
                 "feed_rtt_ns": {"B": {"count": 2, "p50": 5, "p95": 6, "p99": 7, "max": 8},
                                 "A": {"count": 3, "p50": 1, "p95": 2, "p99": 3, "max": 4}}}}

    def test_conversion_order_and_keys(self):
        rows = n.summary_metric_rows("R", self.TRIAL)
        got = [(r["source"], r["metric"], r["stat"], r["value_ns"]) for r in rows]
        expect = [("client", "schedule_to_completion_ns", s, v) for s, v in
                  (("p50", 635300), ("p95", 1214400), ("p99", 1238000), ("max", 1336700))]
        expect += [("client", "send_to_completion_ns", s, v) for s, v in
                   (("p50", 150400), ("p95", 192500), ("p99", 227700), ("max", 342500))]
        expect += [("aggregator", "route_ns", s, v) for s, v in
                   (("p50", 3597), ("p95", 4188), ("p99", 6011), ("max", 41168), ("count", 9))]
        expect += [("aggregator", "process_ns", s, v) for s, v in
                   (("p50", 1), ("p95", 2), ("p99", 3), ("max", 4), ("count", 4))]
        expect += [("aggregator", "feed_rtt_ns:A", s, v) for s, v in
                   (("p50", 1), ("p95", 2), ("p99", 3), ("max", 4), ("count", 3))]
        expect += [("aggregator", "feed_rtt_ns:B", s, v) for s, v in
                   (("p50", 5), ("p95", 6), ("p99", 7), ("max", 8), ("count", 2))]
        self.assertEqual(got, expect)
        self.assertEqual(rows[0]["metric_key"], "R:2:schedule_to_completion_ns:p50")
        self.assertTrue(all(type(r["value_ns"]) is int and r["run_id"] == "R" and r["trial"] == 2
                            for r in rows))

    def test_malformed_units(self):
        for bad in ({"trial": 0, "send_to_completion_us": {"p50": "1", "p95": 1, "p99": 1, "max": 1}},
                    {"trial": 0, "send_to_completion_us": {"p50": -1, "p95": 1, "p99": 1, "max": 1}},
                    {"trial": 0, "server_cumulative": {"route_ns": {"count": 1, "p50": 1.5, "p95": 1,
                                                                    "p99": 1, "max": 1}}}):
            with self.subTest(bad=bad), self.assertRaises(n.NormalizationError):
                n.summary_metric_rows("R", bad)


if __name__ == "__main__":
    unittest.main()
