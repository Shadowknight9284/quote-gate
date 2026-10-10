"""DB-free tests for the two Tiger Data write safeguards (fake cursor; no psycopg, no network)."""
import contextlib
import re
import unittest
from datetime import datetime, timezone

from quote_gate import importer as im


class Conflicts(unittest.TestCase):
    ROW = {"metric_key": "R:0:route_ns:p50", "run_id": "R", "trial": 0, "source": "aggregator",
           "metric": "route_ns", "stat": "p50", "value_ns": 3597}

    def test_identical_reimport_is_not_a_conflict(self):
        self.assertEqual(im.find_conflicts("run_metric_summaries", {self.ROW["metric_key"]: dict(self.ROW)}, [self.ROW]), [])

    def test_changed_content_is_detected(self):
        changed = {**self.ROW, "value_ns": 3598}
        c = im.find_conflicts("run_metric_summaries", {self.ROW["metric_key"]: dict(self.ROW)}, [changed])
        self.assertEqual(len(c), 1)
        self.assertEqual(list(c[0]["diff"]), ["value_ns"])

    def test_new_key_is_not_a_conflict(self):
        self.assertEqual(im.find_conflicts("run_metric_summaries", {}, [self.ROW]), [])

    def test_json_and_timestamp_and_excluded_columns(self):
        t = datetime(2026, 10, 10, tzinfo=timezone.utc)
        ev = {"event_key": "R:event:0", "run_id": "R", "venue_id": "A", "kind": "status",
              "detail": '{"to": "stale"}', "observed_mono_ns": 1, "observer": "o", "observed_at": t}
        stored = {**ev, "detail": {"to": "stale"}, "imported_at": "ignored"}
        self.assertEqual(im.find_conflicts("venue_events", {"R:event:0": stored}, [ev]), [])
        self.assertEqual(len(im.find_conflicts("venue_events", {"R:event:0": {**stored, "detail": {"to": "fresh"}}}, [ev])), 1)
        later = datetime(2026, 10, 11, tzinfo=timezone.utc)
        self.assertEqual(len(im.find_conflicts("venue_events", {"R:event:0": {**stored, "observed_at": later}}, [ev])), 1)


class SchemaValidation(unittest.TestCase):
    def full(self):
        return ({t: list(c) for t, (c, _) in im.EXPECTED_SCHEMA.items()},
                {t: list(pk) for t, (_, pk) in im.EXPECTED_SCHEMA.items()})

    def test_empty_and_exact_are_ok(self):
        self.assertEqual(im.validate_existing_schema({}, {}, []), [])
        self.assertEqual(im.validate_existing_schema(*self.full(), []), [])

    def test_incompatible_objects_stop(self):
        cols, pks = self.full()
        cols["latency_samples"] = [c for c in cols["latency_samples"] if c[0] != "observer"]
        cols["venue_events"] = [(n, "text" if n == "detail" else t, nl) for n, t, nl in cols["venue_events"]]
        cols["experiment_runs"] = [(n, t, "YES" if n == "label" else nl) for n, t, nl in cols["experiment_runs"]]
        pks["run_metric_summaries"] = ["run_id"]
        cols["scratch"] = [("x", "text", "YES")]
        problems = im.validate_existing_schema(cols, pks, ["some_view"])
        joined = " | ".join(problems)
        for needle in ("unexpected object quotegate.some_view", "unexpected table quotegate.scratch",
                       "quotegate.latency_samples: columns differ", "quotegate.venue_events: columns differ",
                       "quotegate.experiment_runs: columns differ", "quotegate.run_metric_summaries: primary key"):
            self.assertIn(needle, joined)
        self.assertEqual(len(problems), 6)

    def test_expected_schema_matches_schema_sql(self):
        with open("db/schema.sql") as f:
            sql = f.read()
        types = {"text": "text", "double precision": "double precision", "integer": "integer",
                 "bigint": "bigint", "boolean": "boolean", "timestamptz": "timestamp with time zone",
                 "jsonb": "jsonb"}
        for t, body in re.findall(r"CREATE TABLE IF NOT EXISTS quotegate\.(\w+) \((.*?)\n\);", sql, re.S):
            cols, pk = [], []
            for line in body.splitlines():
                line = re.sub(r"--.*", "", line).strip().rstrip(",")
                if not line:
                    continue
                m = re.match(r"PRIMARY KEY \((.*)\)", line)
                if m:
                    pk = [c.strip() for c in m.group(1).split(",")]
                    continue
                name, rest = line.split(None, 1)
                ty = next(v for k, v in types.items() if rest.startswith(k))
                nullable = "NO" if ("NOT NULL" in rest or "PRIMARY KEY" in rest) else "YES"
                if "PRIMARY KEY" in rest:
                    pk = [name]
                cols.append((name, ty, nullable))
            self.assertEqual((cols, pk), im.EXPECTED_SCHEMA[t], t)
        self.assertEqual(len(re.findall(r"CREATE TABLE IF NOT EXISTS", sql)), len(im.EXPECTED_SCHEMA))


class FakeCursor:
    def __init__(self, existing):
        self.existing, self.sql, self._rows, self.description = existing, [], [], None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.sql.append(sql)
        if sql.startswith("SELECT * FROM quotegate.run_metric_summaries"):
            cols = list(Conflicts.ROW)
            self.description = [type("D", (), {"name": c})() for c in cols]
            self._rows = [tuple(r[c] for c in cols) for r in self.existing]
        else:
            self.description = [type("D", (), {"name": "run_id"})()]
            self._rows = []

    def executemany(self, sql, rows):
        self.sql.append(sql)

    def fetchall(self):
        return self._rows


class FakeConn:
    def __init__(self, cur):
        self.cur = cur

    def transaction(self):
        return contextlib.nullcontext()

    def cursor(self):
        return self.cur


class ApplyRefusesConflicts(unittest.TestCase):
    def test_conflict_aborts_before_any_insert(self):
        plan = {t: [] for t in im.TABLES}
        plan["experiment_runs"] = [{"run_id": "R", "run_started_utc_ns": None}]
        plan["run_metric_summaries"] = [{**Conflicts.ROW, "value_ns": 1}]     # changed content
        cur = FakeCursor([Conflicts.ROW])
        with self.assertRaises(im.ImportErrorExplicit) as cm:
            im.apply_plan(FakeConn(cur), plan)
        self.assertIn("conflicting key reuse", str(cm.exception))
        self.assertFalse(any(s.startswith("INSERT") for s in cur.sql))


if __name__ == "__main__":
    unittest.main()
