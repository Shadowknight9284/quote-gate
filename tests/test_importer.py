"""DB-free tests of the evidence importer (no psycopg, no network, temp files only)."""
import json
import os
import stat
import tempfile
import unittest

from quote_gate import importer as im
from quote_gate.bench import summarize

HDR = {"schema": "qg.route_obs.v2", "observer": "bench@vm1", "anchor_wall_ns": 1_800_000_000_000_000_000,
       "anchor_mono_ns": 1_000_000_000, "fields": ["scheduled_mono_ns", "sent_mono_ns",
                                                   "completed_mono_ns", "outcome", "quote_age_ns"]}


def write_jsonl(path, rows):
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def make_run(root, label, v2=True, with_agg=False):
    exp = os.path.join(root, "m4", "exp1")
    run_id = f"20261010T160000Z-{label}"
    d = os.path.join(exp, "bench", run_id)
    os.makedirs(d)
    start = 1_000_000_000
    obs = [(start + i * 1_000_000, start + i * 1_000_000 + 5_000 + i,
            start + i * 1_000_000 + 150_000 + 7 * i, "A" if i % 3 else "NO_ROUTE",
            2_000_000 if i % 3 else None) for i in range(1500)]
    trial = {"start_ns": start, "offered": 1500, "not_sent_backlog": 0, "observations": obs,
             "generator_lag_ns": [100] * 1500, "server_after": {}}
    s = summarize(trial, 0.5)
    s["trial"] = 0
    meta = {"run_id": run_id, "label": label, "rate": 1000, "duration_s": 1.5, "warmup_s": 0.5,
            "connections": 4, "trials": 1, "git": "abc"}
    with open(os.path.join(d, "summary.json"), "w") as f:
        json.dump({"meta": meta, "trials": [s]}, f)
    rows = [list(o) if v2 else list(o[:4]) for o in obs]
    write_jsonl(os.path.join(d, "trial0.raw.jsonl"), ([HDR] if v2 else []) + rows)
    if with_agg:
        a = os.path.join(exp, "aggregator")
        os.makedirs(a)
        h = {**HDR, "schema": "qg.feed_rtt.v1", "observer": "aggregator@vm1"}
        write_jsonl(os.path.join(a, "feed_rtt.jsonl"), [h, [2_000_000_000, "A", 120_000], [2_100_000_000, "B", 570_000]])
        h = {**HDR, "schema": "qg.venue_events.v1", "observer": "aggregator@vm1"}
        write_jsonl(os.path.join(a, "venue_events.jsonl"),
                    [h, [1_500_000_000, "A", "status", {"from": "fresh", "to": "stale", "detect_delay_ns": 400_000}],
                     [1_600_000_000, "B", "disconnect", {"epoch": 1, "reason": "ConnectionError"}]])
    return d


class Plan(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_v2_counts_timed_and_verify_local(self):
        d = make_run(self.root, "m3-remoteB", v2=True, with_agg=True)
        self.assertEqual(im.discover(self.root), [d])
        plan = im.build_plan(d, {"m3-remoteB": "cross_vm_lan"})
        self.assertEqual({t: len(plan[t]) for t in im.TABLES},
                         {"experiment_runs": 1, "latency_samples": 1500 + 2, "latency_samples_untimed": 0,
                          "venue_events": 2, "run_metric_summaries": 16})
        run = plan["experiment_runs"][0]
        self.assertEqual((run["placement"], run["run_started_source"]), ("cross_vm_lan", "run_id_utc_second"))
        s0 = plan["latency_samples"][0]
        self.assertEqual(s0["observed_at_utc_ns"], HDR["anchor_wall_ns"] + (s0["observed_mono_ns"] - HDR["anchor_mono_ns"]))
        res = im.verify_local(plan)
        self.assertEqual(len(res), 8)                       # 2 client metrics x 4 stats
        self.assertTrue(all(ok for *_, ok in res), res)

    def test_v1_goes_untimed_with_no_invented_time(self):
        d = make_run(self.root, "smoke", v2=False)
        plan = im.build_plan(d, {})
        self.assertEqual(len(plan["latency_samples"]), 0)
        self.assertEqual(len(plan["latency_samples_untimed"]), 1500)
        self.assertTrue(all(s["observed_at_utc_ns"] is None and s["observer"] is None
                            for s in plan["latency_samples_untimed"]))
        self.assertEqual(plan["experiment_runs"][0]["placement"], "unknown")

    def test_reimport_keys_stable_and_unique(self):
        d = make_run(self.root, "x", with_agg=True)
        a, b = im.build_plan(d, {}), im.build_plan(d, {})
        for t, key in (("latency_samples", "sample_key"), ("venue_events", "event_key"),
                       ("run_metric_summaries", "metric_key")):
            ka, kb = [r[key] for r in a[t]], [r[key] for r in b[t]]
            self.assertEqual(ka, kb)
            self.assertEqual(len(set(ka)), len(ka))

    def test_malformed_artifact_fails_explicitly(self):
        d = make_run(self.root, "bad")
        with open(os.path.join(d, "trial0.raw.jsonl"), "a") as f:
            f.write(json.dumps([5, 4, 3, "A", None]) + "\n")
        with self.assertRaises(im.normalize.NormalizationError):
            im.build_plan(d, {})
        with self.assertRaises(SystemExit) as cm:
            im.main(["plan", "--root", self.root])
        self.assertEqual(cm.exception.code, 1)

    def test_bad_feed_header_rejected(self):
        with self.assertRaises(im.normalize.NormalizationError):
            im.feed_rows("R", [{"schema": "nope"}, [1, "A", 2]])


class TigerEnv(unittest.TestCase):
    def write(self, text, mode=0o600):
        fd, path = tempfile.mkstemp()
        os.write(fd, text.encode())
        os.close(fd)
        os.chmod(path, mode)
        self.addCleanup(os.unlink, path)
        return path

    GOOD = "# c\nPGHOST=h.example\nPGPORT=5432\nPGDATABASE=tsdb\nPGUSER=u\nPGPASSWORD='p$`x\"y'\n"

    def test_parse_without_shell_evaluation(self):
        p = im.load_tiger_env(self.write(self.GOOD))
        self.assertEqual(p["password"], 'p$`x"y')
        self.assertEqual((p["port"], p["sslmode"]), (5432, "require"))
        self.assertNotIn("p$", im.target_identifier(p))

    def test_rejects_loose_permissions_unknown_and_missing(self):
        with self.assertRaises(im.ImportErrorExplicit):
            im.load_tiger_env(self.write(self.GOOD, 0o644))
        with self.assertRaises(im.ImportErrorExplicit):
            im.load_tiger_env(self.write(self.GOOD + "EXTRA=1\n"))
        with self.assertRaises(im.ImportErrorExplicit):
            im.load_tiger_env(self.write("PGHOST=h\n"))

    def test_apply_requires_confirm(self):
        with self.assertRaises(SystemExit) as cm:
            im.main(["apply", "--root", tempfile.gettempdir() + "/qg-none"])
        self.assertIn("confirm", str(cm.exception.code))


if __name__ == "__main__":
    unittest.main()
