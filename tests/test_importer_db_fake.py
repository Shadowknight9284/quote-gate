"""Import reporting, re-import, conflict probe and rollback against an in-memory fake database.
No psycopg, no network, no private configuration."""
import contextlib
import copy
import re
import unittest
from unittest import mock

from quote_gate import importer as im


class FakeDB:
    def __init__(self):
        self.tables = {t: {} for t in im.TABLES}
        self.drop_inserts = False         # fault: silently lose inserted rows

    def connection(self):
        return FakeConn(self)


class FakeConn:
    def __init__(self, db):
        self.db = db

    @contextlib.contextmanager
    def transaction(self):
        snap = copy.deepcopy(self.db.tables)
        try:
            yield
        except BaseException:
            self.db.tables = snap          # rollback
            raise

    def cursor(self):
        return FakeCursor(self.db)


class FakeCursor:
    def __init__(self, db):
        self.db, self._rows, self.description = db, [], None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        m = re.match(r"SELECT count\(\*\) FROM quotegate\.(\w+) WHERE run_id = %s", sql)
        if m:
            self._rows = [(sum(1 for r in self.db.tables[m.group(1)].values() if r["run_id"] == params[0]),)]
            return
        m = re.match(r"SELECT \* FROM quotegate\.(\w+) WHERE (\w+) = ANY", sql)
        if m:
            rows = [r for k, r in self.db.tables[m.group(1)].items() if k in set(params[0])]
            cols = sorted({c for r in rows for c in r}) or [m.group(2)]
            self.description = [type("D", (), {"name": c})() for c in cols]
            self._rows = [tuple(r.get(c) for c in cols) for r in rows]
            return
        raise AssertionError("unexpected SQL " + sql[:60])

    def executemany(self, sql, rows):
        t = re.match(r"INSERT INTO quotegate\.(\w+)", sql).group(1)
        if self.db.drop_inserts:
            return
        for r in rows:
            self.db.tables[t].setdefault(r[im.KEYS[t]], dict(r))      # ON CONFLICT DO NOTHING

    def fetchone(self):
        return self._rows[0]

    def fetchall(self):
        return self._rows


def make_plan(run_id="R1", n=5):
    plan = {t: [] for t in im.TABLES}
    plan["experiment_runs"] = [{"run_id": run_id, "label": "l", "git": None, "python": None, "operation": None,
                                "target": None, "placement": "loopback", "rate_rps": 1.0, "duration_s": 1.0,
                                "warmup_s": 0.0, "connections": 1, "trials": 1, "run_started_utc_ns": None,
                                "run_started_source": None, "source_artifact": "x"}]
    plan["run_metric_summaries"] = [{"metric_key": f"{run_id}:0:route_ns:p{i}", "run_id": run_id, "trial": 0,
                                     "source": "aggregator", "metric": "route_ns", "stat": f"p{i}",
                                     "value_ns": 1000 + i} for i in range(n)]
    return plan


class Reporting(unittest.TestCase):
    def test_first_import_then_unchanged_reimport(self):
        db = FakeDB()
        conn = db.connection()
        r1 = im.apply_plan(conn, make_plan())
        self.assertEqual(r1["pre"]["run_metric_summaries"], 0)
        self.assertEqual(r1["inserted"], {"experiment_runs": 1, "latency_samples": 0, "latency_samples_untimed": 0,
                                          "venue_events": 0, "run_metric_summaries": 5})
        r2 = im.apply_plan(conn, make_plan())
        self.assertEqual(r2["pre"], r1["final"])
        self.assertEqual(set(r2["inserted"].values()), {0})
        self.assertEqual(r2["final"], r1["final"])

    def test_failed_transaction_commits_no_row_changes(self):
        db = FakeDB()
        im.apply_plan(db.connection(), make_plan("R0"))
        before = copy.deepcopy(db.tables)
        db.drop_inserts = True                        # final counts will not match -> abort
        with self.assertRaises(im.ImportErrorExplicit) as cm:
            im.apply_plan(db.connection(), make_plan("R1"))
        self.assertIn("count mismatch", str(cm.exception))
        self.assertEqual(db.tables, before)


class Probe(unittest.TestCase):
    def test_probe_passes_and_content_is_unchanged(self):
        db = FakeDB()
        conn = db.connection()
        im.apply_plan(conn, make_plan())
        before = copy.deepcopy(db.tables)
        r = im.conflict_probe(conn, make_plan())
        self.assertEqual(r["result"], "PASS", r)
        self.assertTrue(r["error_was_content_conflict"] and r["content_unchanged"] and r["counts_unchanged"])
        self.assertEqual((r["source_value"], r["probe_value"], r["stored_value_after"]), (1000, 1001, 1000))
        self.assertEqual(db.tables, before)

    def test_probe_fails_if_conflicts_were_not_detected(self):
        db = FakeDB()
        conn = db.connection()
        im.apply_plan(conn, make_plan())
        # simpler: disable detection entirely; the probe must then report FAIL (silently ignored change)
        with mock.patch.object(im, "find_conflicts", return_value=[]):
            r = im.conflict_probe(conn, make_plan())
        self.assertEqual(r["result"], "FAIL")
        self.assertFalse(r["error_was_content_conflict"])

    def test_probe_not_run_when_row_absent(self):
        r = im.conflict_probe(FakeDB().connection(), make_plan())
        self.assertEqual(r["result"], "NOT_RUN")


class Guards(unittest.TestCase):
    PARAMS = {"host": "h.example", "port": 5432, "dbname": "d", "user": "u", "password": "secret-pw", "sslmode": "require"}

    def run_main(self, *args):
        with mock.patch.object(im, "load_tiger_env", return_value=self.PARAMS):
            with self.assertRaises(SystemExit) as cm:
                im.main(list(args))
        return str(cm.exception.code)

    def test_target_mismatch_refuses(self):
        msg = self.run_main("inspect", "--confirm", "--expect-target", "other@h:1/d sslmode=require")
        self.assertIn("refusing", msg)
        self.assertNotIn("secret-pw", msg)

    def test_apply_requires_run_ids_and_confirm(self):
        tgt = im.target_identifier(self.PARAMS)
        self.assertIn("--run-id", self.run_main("apply", "--confirm", "--expect-target", tgt))
        self.assertIn("--confirm", self.run_main("apply", "--expect-target", tgt))

    def test_safe_main_suppresses_error_detail(self):
        import io
        from contextlib import redirect_stderr
        err = io.StringIO()
        with mock.patch.object(im, "main", side_effect=RuntimeError("password=secret-pw host=h")):
            with redirect_stderr(err), self.assertRaises(SystemExit):
                im.safe_main([])
        self.assertNotIn("secret-pw", err.getvalue())
        self.assertIn("RuntimeError", err.getvalue())


if __name__ == "__main__":
    unittest.main()
