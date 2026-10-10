"""Post-run evidence importer: local artifacts -> normalized rows -> (optionally) Tiger Data.

Runs as a separate process after experiments; never imported by the runtime path.

  python3 -m quote_gate.importer plan [--root artifacts]          # DB-free: row counts per table
  python3 -m quote_gate.importer verify-local [--root artifacts]  # DB-free: recompute summaries
  .venv/bin/python -m quote_gate.importer init-schema --confirm   # additive DDL from db/schema.sql
  .venv/bin/python -m quote_gate.importer apply --confirm         # reads docs/private/tiger.env

Placement is not recorded in artifacts; it comes from db/placements.json (label -> placement),
defaulting to "unknown". Missing wall-clock anchors are left NULL (untimed tables), never invented.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from datetime import datetime, timezone

from . import normalize
from .metrics import percentile

FEED_SCHEMA = "qg.feed_rtt.v1"
EVENT_SCHEMA = "qg.venue_events.v1"
TOLERANCE_NS = 50          # local summaries are rounded to 0.1 µs
TABLES = ("experiment_runs", "latency_samples", "latency_samples_untimed", "venue_events",
          "run_metric_summaries")


class ImportErrorExplicit(RuntimeError):
    """Import failed; nothing from the failing run was committed."""


def _read_jsonl(path):
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def _utc(ns):
    return None if ns is None else datetime.fromtimestamp(ns / 1e9, tz=timezone.utc)


def _run_started(meta):
    if type(meta.get("run_started_utc_ns")) is int:
        return meta["run_started_utc_ns"], "meta.run_started_utc_ns"
    stamp = meta["run_id"].split("-", 1)[0].rstrip("Z")
    try:
        dt = datetime.strptime(stamp, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None, None
    # Earlier run_ids used VM local time, which was UTC+0000 when checked; second resolution.
    return int(dt.timestamp()) * 10**9, "run_id_utc_second"


def discover(root):
    """Bench run directories (containing summary.json), sorted."""
    return sorted(os.path.dirname(p) for p in glob.glob(os.path.join(root, "**", "summary.json"),
                                                        recursive=True))


def _aggregator_dir(run_dir):
    """M4-style layout: <exp>/bench/<run_id>/ next to <exp>/aggregator/."""
    d = os.path.join(os.path.dirname(os.path.dirname(run_dir)), "aggregator")
    return d if os.path.isdir(d) else None


def feed_rows(run_id, lines):
    if not lines:
        return []
    hdr = lines[0]
    if not isinstance(hdr, dict) or hdr.get("schema") != FEED_SCHEMA:
        raise normalize.NormalizationError("feed_rtt: bad or missing header")
    out = []
    for i, row in enumerate(lines[1:]):
        if not (isinstance(row, list) and len(row) == 3 and type(row[0]) is int
                and isinstance(row[1], str) and type(row[2]) is int and row[2] >= 0):
            raise normalize.NormalizationError(f"feed_rtt: malformed row {i}")
        mono, venue, rtt = row
        out.append({"sample_key": f"{run_id}:feed:{i}", "run_id": run_id, "trial": None,
                    "kind": "feed_rtt", "venue_id": venue, "value_ns": rtt,
                    "schedule_to_completion_ns": None, "schedule_to_send_ns": None,
                    "outcome": None, "quote_age_ns": None, "warmup": None,
                    "observed_mono_ns": mono, "observer": hdr["observer"],
                    "observed_at_utc_ns": hdr["anchor_wall_ns"] + (mono - hdr["anchor_mono_ns"])})
    return out


def event_rows(run_id, lines):
    if not lines:
        return []
    hdr = lines[0]
    if not isinstance(hdr, dict) or hdr.get("schema") != EVENT_SCHEMA:
        raise normalize.NormalizationError("venue_events: bad or missing header")
    out = []
    for i, row in enumerate(lines[1:]):
        if not (isinstance(row, list) and len(row) == 4 and type(row[0]) is int
                and isinstance(row[1], str) and isinstance(row[2], str) and isinstance(row[3], dict)):
            raise normalize.NormalizationError(f"venue_events: malformed row {i}")
        mono, venue, kind, detail = row
        out.append({"event_key": f"{run_id}:event:{i}", "run_id": run_id, "venue_id": venue,
                    "kind": kind, "detail": detail, "observed_mono_ns": mono,
                    "observer": hdr["observer"],
                    "observed_at_utc_ns": hdr["anchor_wall_ns"] + (mono - hdr["anchor_mono_ns"])})
    return out


def _route_to_sample(r):
    return {"sample_key": r["sample_key"], "run_id": r["run_id"], "trial": r["trial"],
            "kind": "route", "venue_id": r["outcome"] if r["outcome"] not in ("NO_ROUTE", "ERROR")
            and not r["outcome"].startswith("FAIL:") else None,
            "value_ns": r["send_to_completion_ns"],
            "schedule_to_completion_ns": r["schedule_to_completion_ns"],
            "schedule_to_send_ns": r["schedule_to_send_ns"], "outcome": r["outcome"],
            "quote_age_ns": r["quote_age_ns"], "warmup": r["warmup"],
            "observed_mono_ns": r["completed_mono_ns"], "observer": r["observer"],
            "observed_at_utc_ns": r["completed_at_utc_ns"]}


def build_plan(run_dir, placements):
    """All rows for one bench run directory, grouped by table. Raises NormalizationError."""
    with open(os.path.join(run_dir, "summary.json")) as f:
        summary = json.load(f)
    meta = summary["meta"] if isinstance(summary, dict) and "meta" in summary else {}
    placement = placements.get(meta.get("label"), "unknown")
    run = normalize.run_row(summary, placement)
    started, source = _run_started(meta)
    run.update(run_started_utc_ns=started, run_started_source=source, source_artifact=run_dir)
    plan = {t: [] for t in TABLES}
    plan["experiment_runs"].append(run)
    for t, trial_summary in enumerate(summary["trials"]):
        trial = trial_summary.get("trial", t)
        raw = os.path.join(run_dir, f"trial{trial}.raw.jsonl")
        if os.path.exists(raw):
            for r in normalize.route_sample_rows(run["run_id"], trial, _read_jsonl(raw), run["warmup_s"]):
                s = _route_to_sample(r)
                plan["latency_samples" if s["observed_at_utc_ns"] is not None
                     else "latency_samples_untimed"].append(s)
        plan["run_metric_summaries"] += normalize.summary_metric_rows(run["run_id"], trial_summary)
    agg = _aggregator_dir(run_dir)
    if agg:
        if os.path.exists(os.path.join(agg, "feed_rtt.jsonl")):
            plan["latency_samples"] += feed_rows(run["run_id"], _read_jsonl(os.path.join(agg, "feed_rtt.jsonl")))
        if os.path.exists(os.path.join(agg, "venue_events.jsonl")):
            plan["venue_events"] += event_rows(run["run_id"], _read_jsonl(os.path.join(agg, "venue_events.jsonl")))
    return plan


def verify_local(plan):
    """Recompute per-trial client percentiles from normalized samples; compare to recorded summaries.

    Returns a list of (metric_key, recorded_ns, recomputed_ns, ok). Uses the same nearest-rank
    method as the benchmark and as SQL percentile_disc.
    """
    samples = [s for s in plan["latency_samples"] + plan["latency_samples_untimed"]
               if s["kind"] == "route" and not s["warmup"]]
    by_trial = {}
    for s in samples:
        by_trial.setdefault(s["trial"], []).append(s)
    out = []
    for m in plan["run_metric_summaries"]:
        col = {"send_to_completion_ns": "value_ns",
               "schedule_to_completion_ns": "schedule_to_completion_ns"}.get(m["metric"])
        if m["source"] != "client" or col is None or m["stat"] not in ("p50", "p95", "p99", "max"):
            continue
        vals = sorted(s[col] for s in by_trial.get(m["trial"], []))
        if not vals:
            continue
        got = vals[-1] if m["stat"] == "max" else percentile(vals, int(m["stat"][1:]))
        out.append((m["metric_key"], m["value_ns"], got, abs(got - m["value_ns"]) <= TOLERANCE_NS))
    return out


def load_placements(path="db/placements.json"):
    try:
        with open(path) as f:
            return {k: v for k, v in json.load(f).items() if not k.startswith("_")}
    except FileNotFoundError:
        return {}


# ---------------- database side (optional; requires psycopg in .venv) ----------------

TIGER_ENV = "docs/private/tiger.env"
TIGER_KEYS_REQUIRED = ("PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD")
TIGER_KEYS_OPTIONAL = ("PGSSLMODE",)


def load_tiger_env(path=TIGER_ENV):
    """Parse KEY=VALUE lines without any shell evaluation; values are never logged.

    Blank lines and '#' comments are ignored; one optional pair of surrounding single or
    double quotes is stripped; no escapes, interpolation or command substitution.
    Refuses files readable by group/other, unknown keys, and missing required keys.
    """
    st = os.stat(path)
    if st.st_mode & 0o077:
        raise ImportErrorExplicit(f"{path} must not be group/other accessible (chmod 600)")
    vals = {}
    with open(path) as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, sep, val = line.partition("=")
            key = key.strip()
            if not sep or key not in TIGER_KEYS_REQUIRED + TIGER_KEYS_OPTIONAL:
                raise ImportErrorExplicit(f"{path}:{n}: unexpected line (key {key!r})")
            val = val.strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
                val = val[1:-1]
            vals[key] = val
    missing = [k for k in TIGER_KEYS_REQUIRED if not vals.get(k)]
    if missing:
        raise ImportErrorExplicit(f"{path}: missing {missing}")
    return {"host": vals["PGHOST"], "port": int(vals["PGPORT"]), "dbname": vals["PGDATABASE"],
            "user": vals["PGUSER"], "password": vals["PGPASSWORD"],
            "sslmode": vals.get("PGSSLMODE", "require")}


READ_ONLY_CHECK_SQL = [
    ("server", "SELECT current_database(), current_user, split_part(version(), ' ', 2)"),
    ("read_only_session", "SHOW transaction_read_only"),
    ("timescaledb", "SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'"),
    ("create_hypertable_available", "SELECT count(*) > 0 FROM pg_proc WHERE proname = 'create_hypertable'"),
    ("quotegate_schema_exists", "SELECT count(*) > 0 FROM pg_namespace WHERE nspname = 'quotegate'"),
    ("can_create_in_database", "SELECT has_database_privilege(current_user, current_database(), 'CREATE')"),
]


def read_only_check(params) -> dict:
    """Read-only connection check. Session is forced read-only; only catalog SELECTs are run."""
    import psycopg      # optional dependency
    out = {}
    with psycopg.connect(**params, connect_timeout=10, autocommit=False,
                         options="-c default_transaction_read_only=on -c statement_timeout=10000") as conn:
        with conn.cursor() as cur:
            for name, sql in READ_ONLY_CHECK_SQL:
                cur.execute(sql)
                row = cur.fetchone()
                out[name] = row[0] if row and len(row) == 1 else row
            cols, pks, others = inspect_existing_schema(cur)
            out["existing_quotegate_tables"] = sorted(cols)
            out["schema_problems"] = validate_existing_schema(cols, pks, others)
        conn.rollback()
    return out


def target_identifier(params):
    """Safe description for reports: no password."""
    return f"{params['user']}@{params['host']}:{params['port']}/{params['dbname']} sslmode={params['sslmode']}"

INSERTS = {
    "experiment_runs": (
        "INSERT INTO quotegate.experiment_runs (run_id,label,git,python,operation,target,placement,"
        "rate_rps,duration_s,warmup_s,connections,trials,run_started_utc,run_started_source,"
        "source_artifact) VALUES (%(run_id)s,%(label)s,%(git)s,%(python)s,%(operation)s,%(target)s,"
        "%(placement)s,%(rate_rps)s,%(duration_s)s,%(warmup_s)s,%(connections)s,%(trials)s,"
        "%(run_started_utc)s,%(run_started_source)s,%(source_artifact)s) ON CONFLICT DO NOTHING"),
    "latency_samples": (
        "INSERT INTO quotegate.latency_samples (observed_at,sample_key,run_id,trial,kind,venue_id,"
        "value_ns,schedule_to_completion_ns,schedule_to_send_ns,outcome,quote_age_ns,warmup,"
        "observed_mono_ns,observer) VALUES (%(observed_at)s,%(sample_key)s,%(run_id)s,%(trial)s,"
        "%(kind)s,%(venue_id)s,%(value_ns)s,%(schedule_to_completion_ns)s,%(schedule_to_send_ns)s,"
        "%(outcome)s,%(quote_age_ns)s,%(warmup)s,%(observed_mono_ns)s,%(observer)s) "
        "ON CONFLICT DO NOTHING"),
    "latency_samples_untimed": (
        "INSERT INTO quotegate.latency_samples_untimed (sample_key,run_id,trial,kind,venue_id,"
        "value_ns,schedule_to_completion_ns,schedule_to_send_ns,outcome,quote_age_ns,warmup,"
        "observed_mono_ns,observer) VALUES (%(sample_key)s,%(run_id)s,%(trial)s,%(kind)s,"
        "%(venue_id)s,%(value_ns)s,%(schedule_to_completion_ns)s,%(schedule_to_send_ns)s,"
        "%(outcome)s,%(quote_age_ns)s,%(warmup)s,%(observed_mono_ns)s,%(observer)s) "
        "ON CONFLICT DO NOTHING"),
    "venue_events": (
        "INSERT INTO quotegate.venue_events (observed_at,event_key,run_id,venue_id,kind,detail,"
        "observed_mono_ns,observer) VALUES (%(observed_at)s,%(event_key)s,%(run_id)s,%(venue_id)s,"
        "%(kind)s,%(detail)s,%(observed_mono_ns)s,%(observer)s) ON CONFLICT DO NOTHING"),
    "run_metric_summaries": (
        "INSERT INTO quotegate.run_metric_summaries (metric_key,run_id,trial,source,metric,stat,"
        "value_ns) VALUES (%(metric_key)s,%(run_id)s,%(trial)s,%(source)s,%(metric)s,%(stat)s,"
        "%(value_ns)s) ON CONFLICT DO NOTHING"),
}
COUNT_SQL = {t: f"SELECT count(*) FROM quotegate.{t} WHERE run_id = %s" for t in TABLES}

# ---- Safeguard 1: conflicting reuse of a key with different content ----
KEYS = {"experiment_runs": "run_id", "latency_samples": "sample_key",
        "latency_samples_untimed": "sample_key", "venue_events": "event_key",
        "run_metric_summaries": "metric_key"}
COMPARE_EXCLUDE = {"imported_at"}


def _norm(v):
    if isinstance(v, str) and v[:1] in "{[":
        try:
            return json.loads(v)
        except ValueError:
            return v
    if isinstance(v, float) and v.is_integer():
        return v
    return v


def find_conflicts(table: str, existing: dict, planned_params: list[dict]) -> list[dict]:
    """existing: key -> row dict as stored. Returns rows whose key exists with different content.

    Pure. Compared columns: every planned column except COMPARE_EXCLUDE; JSON text and dicts compare
    by value; timestamps must be equal datetimes.
    """
    key = KEYS[table]
    out = []
    for row in planned_params:
        old = existing.get(row[key])
        if old is None:
            continue
        diff = {c: (old.get(c), row[c]) for c in row if c not in COMPARE_EXCLUDE
                and _norm(old.get(c)) != _norm(row[c])}
        if diff:
            out.append({"key": row[key], "diff": {c: [repr(a)[:60], repr(b)[:60]] for c, (a, b) in diff.items()}})
    return out


def fetch_existing(cur, table: str, keys: list) -> dict:
    key = KEYS[table]
    cur.execute(f"SELECT * FROM quotegate.{table} WHERE {key} = ANY(%s)", (keys,))
    cols = [d.name for d in cur.description]
    return {r[cols.index(key)]: dict(zip(cols, r)) for r in cur.fetchall()}


# ---- Safeguard 2: validate pre-existing objects before any DDL / hypertable conversion ----
# (column, information_schema data_type, is_nullable) in db/schema.sql order; kept in sync by test.
EXPECTED_SCHEMA = {
    "experiment_runs": ([("run_id", "text", "NO"), ("label", "text", "NO"), ("git", "text", "YES"),
                         ("python", "text", "YES"), ("operation", "text", "YES"), ("target", "text", "YES"),
                         ("placement", "text", "NO"), ("rate_rps", "double precision", "NO"),
                         ("duration_s", "double precision", "NO"), ("warmup_s", "double precision", "NO"),
                         ("connections", "integer", "NO"), ("trials", "integer", "NO"),
                         ("run_started_utc", "timestamp with time zone", "YES"),
                         ("run_started_source", "text", "YES"), ("source_artifact", "text", "NO"),
                         ("imported_at", "timestamp with time zone", "NO")], ["run_id"]),
    "latency_samples": ([("observed_at", "timestamp with time zone", "NO"), ("sample_key", "text", "NO"),
                         ("run_id", "text", "NO"), ("trial", "integer", "YES"), ("kind", "text", "NO"),
                         ("venue_id", "text", "YES"), ("value_ns", "bigint", "NO"),
                         ("schedule_to_completion_ns", "bigint", "YES"), ("schedule_to_send_ns", "bigint", "YES"),
                         ("outcome", "text", "YES"), ("quote_age_ns", "bigint", "YES"), ("warmup", "boolean", "YES"),
                         ("observed_mono_ns", "bigint", "NO"), ("observer", "text", "NO")],
                        ["sample_key", "observed_at"]),
    "latency_samples_untimed": ([("sample_key", "text", "NO"), ("run_id", "text", "NO"), ("trial", "integer", "YES"),
                                 ("kind", "text", "NO"), ("venue_id", "text", "YES"), ("value_ns", "bigint", "NO"),
                                 ("schedule_to_completion_ns", "bigint", "YES"),
                                 ("schedule_to_send_ns", "bigint", "YES"), ("outcome", "text", "YES"),
                                 ("quote_age_ns", "bigint", "YES"), ("warmup", "boolean", "YES"),
                                 ("observed_mono_ns", "bigint", "NO"), ("observer", "text", "YES")], ["sample_key"]),
    "venue_events": ([("observed_at", "timestamp with time zone", "NO"), ("event_key", "text", "NO"),
                      ("run_id", "text", "NO"), ("venue_id", "text", "NO"), ("kind", "text", "NO"),
                      ("detail", "jsonb", "NO"), ("observed_mono_ns", "bigint", "NO"), ("observer", "text", "NO")],
                     ["event_key", "observed_at"]),
    "run_metric_summaries": ([("metric_key", "text", "NO"), ("run_id", "text", "NO"), ("trial", "integer", "NO"),
                              ("source", "text", "NO"), ("metric", "text", "NO"), ("stat", "text", "NO"),
                              ("value_ns", "bigint", "NO")], ["metric_key"]),
}


def validate_existing_schema(columns: dict, primary_keys: dict, other_objects: list) -> list[str]:
    """Pure. columns: table -> [(name, data_type, is_nullable)] for tables that already exist in
    schema quotegate; primary_keys: table -> [cols in key order]; other_objects: names of any other
    relations in the schema. Returns problems; empty means safe to run the additive DDL."""
    problems = [f"unexpected object quotegate.{o}" for o in sorted(other_objects)]
    for t, cols in sorted(columns.items()):
        if t not in EXPECTED_SCHEMA:
            problems.append(f"unexpected table quotegate.{t}")
            continue
        exp_cols, exp_pk = EXPECTED_SCHEMA[t]
        if sorted(cols) != sorted(exp_cols):
            missing = sorted(set(exp_cols) - set(cols))
            extra = sorted(set(cols) - set(exp_cols))
            problems.append(f"quotegate.{t}: columns differ (missing {missing}, unexpected {extra})")
        if list(primary_keys.get(t, [])) != exp_pk:
            problems.append(f"quotegate.{t}: primary key {primary_keys.get(t)} != {exp_pk}")
    return problems


def inspect_existing_schema(cur) -> tuple[dict, dict, list]:
    """Read-only catalog queries for schema quotegate."""
    cur.execute("SELECT table_name, column_name, data_type, is_nullable FROM information_schema.columns "
                "WHERE table_schema = 'quotegate' ORDER BY table_name, ordinal_position")
    columns = {}
    for t, c, dt, nl in cur.fetchall():
        columns.setdefault(t, []).append((c, dt, nl))
    cur.execute("SELECT kcu.table_name, kcu.column_name FROM information_schema.table_constraints tc "
                "JOIN information_schema.key_column_usage kcu ON tc.constraint_name = kcu.constraint_name "
                "AND tc.table_schema = kcu.table_schema WHERE tc.table_schema = 'quotegate' "
                "AND tc.constraint_type = 'PRIMARY KEY' ORDER BY kcu.table_name, kcu.ordinal_position")
    pks = {}
    for t, c in cur.fetchall():
        pks.setdefault(t, []).append(c)
    cur.execute("SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'quotegate' AND c.relkind IN ('r','v','m','f','p') ")
    rels = [r[0] for r in cur.fetchall()]
    others = [r for r in rels if r not in columns]
    return columns, pks, others


def _db_params(table, row):
    p = dict(row)
    if table == "experiment_runs":
        p["run_started_utc"] = _utc(p.pop("run_started_utc_ns"))
    if "observed_at_utc_ns" in p:
        p["observed_at"] = _utc(p.pop("observed_at_utc_ns"))
    if table == "venue_events":
        p["detail"] = json.dumps(p["detail"])
    return p


def _counts(cur, run_id) -> dict:
    out = {}
    for t in TABLES:
        cur.execute(COUNT_SQL[t], (run_id,))
        out[t] = cur.fetchone()[0]
    return out


def apply_plan(conn, plan):
    """One transaction per run. Returns {"pre", "inserted", "final"} per table (counts for this run).

    Order inside the transaction: pre-existing counts -> conflict check (abort before any INSERT) ->
    idempotent inserts -> final counts must equal source counts. Any error rolls the whole run back:
    no row changes are committed for that run.
    """
    run_id = plan["experiment_runs"][0]["run_id"]
    try:
        with conn.transaction():
            with conn.cursor() as cur:
                pre = _counts(cur, run_id)
                conflicts = []
                for t in TABLES:
                    if plan[t]:
                        params = [_db_params(t, r) for r in plan[t]]
                        existing = fetch_existing(cur, t, [p[KEYS[t]] for p in params])
                        conflicts += [{"table": t, **c} for c in find_conflicts(t, existing, params)]
                if conflicts:
                    raise ImportErrorExplicit(f"{run_id}: {len(conflicts)} conflicting key reuse(s), "
                                              f"first: {json.dumps(conflicts[0])[:300]}")
                for t in TABLES:
                    if plan[t]:
                        cur.executemany(INSERTS[t], [_db_params(t, r) for r in plan[t]])
                final = _counts(cur, run_id)
                expected = {t: len(plan[t]) for t in TABLES}
                if final != expected:
                    raise ImportErrorExplicit(f"{run_id}: count mismatch db={final} source={expected}")
    except ImportErrorExplicit:
        raise
    except Exception as e:      # psycopg errors, timeouts; message deliberately omitted
        raise ImportErrorExplicit(f"{run_id}: {type(e).__name__}") from e
    return {"pre": pre, "inserted": {t: final[t] - pre[t] for t in TABLES}, "final": final}


def conflict_probe(conn, plan, table: str = "run_metric_summaries", column: str = "value_ns") -> dict:
    """Bounded probe: change ONE non-key value of ONE already-imported row in memory, attempt the import,
    and require the content-conflict error; then verify stored content and counts are unchanged.
    The source artifact and all identifiers are untouched."""
    import copy
    run_id = plan["experiment_runs"][0]["run_id"]
    target = plan[table][0]
    key = target[KEYS[table]]
    original = _db_params(table, target)
    with conn.transaction():
        with conn.cursor() as cur:
            before_counts = _counts(cur, run_id)
            before_row = fetch_existing(cur, table, [key]).get(key)
    if before_row is None or find_conflicts(table, {key: before_row}, [original]):
        return {"result": "NOT_RUN", "reason": "row absent or already differs from source"}
    changed = copy.deepcopy(plan)
    changed[table][0] = {**target, column: target[column] + 1}
    try:
        apply_plan(conn, changed)
        error = None
    except ImportErrorExplicit as e:
        error = str(e)
    with conn.transaction():
        with conn.cursor() as cur:
            after_counts = _counts(cur, run_id)
            after_row = fetch_existing(cur, table, [key]).get(key)
    expected_error = error is not None and "conflicting key reuse" in error
    content_unchanged = after_row is not None and not find_conflicts(table, {key: after_row}, [original])
    ok = expected_error and content_unchanged and after_counts == before_counts
    return {"result": "PASS" if ok else "FAIL", "table": table, "key": key, "column": column,
            "source_value": target[column], "probe_value": target[column] + 1,
            "stored_value_after": None if after_row is None else after_row.get(column),
            "error_was_content_conflict": expected_error, "error": (error or "")[:300],
            "counts_unchanged": after_counts == before_counts, "content_unchanged": content_unchanged,
            "counts": after_counts}


INSPECT_SQL = [
    ("hypertables", "SELECT hypertable_name, num_dimensions FROM timescaledb_information.hypertables "
                    "WHERE hypertable_schema = 'quotegate' ORDER BY 1"),
    ("dimensions", "SELECT hypertable_name, column_name, time_interval::text FROM "
                   "timescaledb_information.dimensions WHERE hypertable_schema = 'quotegate' ORDER BY 1"),
    ("table_counts", "SELECT 'experiment_runs', count(*) FROM quotegate.experiment_runs "
                     "UNION ALL SELECT 'latency_samples', count(*) FROM quotegate.latency_samples "
                     "UNION ALL SELECT 'latency_samples_untimed', count(*) FROM quotegate.latency_samples_untimed "
                     "UNION ALL SELECT 'venue_events', count(*) FROM quotegate.venue_events "
                     "UNION ALL SELECT 'run_metric_summaries', count(*) FROM quotegate.run_metric_summaries"),
]


def _connect(params, read_only: bool):
    import psycopg      # optional dependency
    opts = "-c statement_timeout=60000" + (" -c default_transaction_read_only=on" if read_only else "")
    return psycopg.connect(**params, connect_timeout=10, autocommit=False, options=opts)


def inspect_objects(params) -> dict:
    out = {}
    with _connect(params, read_only=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SHOW transaction_read_only")
            out["read_only_session"] = cur.fetchone()[0]
            cols, pks, others = inspect_existing_schema(cur)
            out["tables"] = sorted(cols)
            out["schema_problems"] = validate_existing_schema(cols, pks, others)
            if out["tables"]:
                for name, sql in INSPECT_SQL:
                    cur.execute(sql)
                    out[name] = [list(r) for r in cur.fetchall()]
        conn.rollback()
    return out


def main(argv=None):
    p = argparse.ArgumentParser(prog="quote_gate.importer")
    p.add_argument("cmd", choices=["plan", "verify-local", "check", "inspect", "init-schema", "apply", "probe"])
    p.add_argument("--root", default="artifacts")
    p.add_argument("--confirm", action="store_true", help="required for any database command")
    p.add_argument("--expect-target", default=None,
                   help="database commands refuse to run unless the resolved target equals this")
    p.add_argument("--run-id", action="append", default=[],
                   help="apply/probe: explicit allow-list of run_ids (required)")
    a = p.parse_args(argv)
    if a.cmd in ("check", "inspect", "init-schema", "apply", "probe"):
        if not a.confirm:
            sys.exit(f"{a.cmd} requires --confirm")
        _params = load_tiger_env()
        if a.expect_target is None or target_identifier(_params) != a.expect_target:
            sys.exit("refusing: resolved target does not equal --expect-target")
        if a.cmd in ("apply", "probe") and not a.run_id:
            sys.exit(f"{a.cmd} requires at least one --run-id")
    if a.cmd == "inspect":
        print(json.dumps({"target": target_identifier(_params)}))
        print(json.dumps(inspect_objects(_params), indent=1, default=str))
        return
    if a.cmd == "check":
        if not a.confirm:
            sys.exit("check requires --confirm (it opens a read-only connection)")
        params = load_tiger_env()
        print(json.dumps({"target": target_identifier(params)}))
        print(json.dumps(read_only_check(params), indent=1, default=str))
        return
    if a.cmd == "init-schema":
        if not a.confirm:
            sys.exit("init-schema requires --confirm")
        params = load_tiger_env()
        print(json.dumps({"target": target_identifier(params)}))
        import psycopg      # optional dependency
        with open("db/schema.sql") as f:
            ddl = f.read()
        with psycopg.connect(**params, connect_timeout=10, autocommit=False,
                             options="-c statement_timeout=60000") as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    problems = validate_existing_schema(*inspect_existing_schema(cur))
                if problems:
                    raise ImportErrorExplicit("pre-existing quotegate objects incompatible; no DDL run: "
                                              + "; ".join(problems)[:500])
                conn.execute(ddl)
        print(json.dumps({"schema": "quotegate", "status": "ensured"}))
        return
    placements = load_placements()
    plans, failures = [], []
    for d in discover(a.root):
        try:
            plans.append(build_plan(d, placements))
        except (normalize.NormalizationError, KeyError, ValueError, OSError) as e:
            failures.append({"run_dir": d, "error": f"{type(e).__name__}: {e}"[:300]})
    if a.run_id:
        wanted = set(a.run_id)
        found = {pl["experiment_runs"][0]["run_id"] for pl in plans}
        if wanted - found:
            sys.exit(f"unknown --run-id: {sorted(wanted - found)}")
        plans = [pl for pl in plans if pl["experiment_runs"][0]["run_id"] in wanted]
        plans.sort(key=lambda pl: a.run_id.index(pl["experiment_runs"][0]["run_id"]))
    if a.cmd == "probe":
        print(json.dumps({"target": target_identifier(_params)}))
        with _connect(_params, read_only=False) as conn:
            for pl in plans:
                r = conflict_probe(conn, pl)
                print(json.dumps({"run_id": pl["experiment_runs"][0]["run_id"], **r}, default=str))
                if r["result"] != "PASS":
                    sys.exit(1)
        return
    if a.cmd == "plan":
        for pl in plans:
            r = pl["experiment_runs"][0]
            print(json.dumps({"run_id": r["run_id"], "placement": r["placement"],
                              **{t: len(pl[t]) for t in TABLES}}))
    elif a.cmd == "verify-local":
        for pl in plans:
            res = verify_local(pl)
            bad = [x for x in res if not x[3]]
            print(json.dumps({"run_id": pl["experiment_runs"][0]["run_id"], "compared": len(res),
                              "mismatches": bad[:5], "tolerance_ns": TOLERANCE_NS}))
    else:
        print(json.dumps({"target": target_identifier(_params)}))
        with _connect(_params, read_only=False) as conn:
            for pl in plans:
                try:
                    print(json.dumps({"run_id": pl["experiment_runs"][0]["run_id"],
                                      "committed": apply_plan(conn, pl)}))
                except ImportErrorExplicit as e:
                    failures.append({"run_id": pl["experiment_runs"][0]["run_id"], "error": str(e),
                                     "committed_row_changes": "none (transaction rolled back)"})
                    break           # stop on the first failed run; remaining runs are not attempted
    for f in failures:
        print(json.dumps({"FAILED": f}), file=sys.stderr)
    sys.exit(1 if failures else 0)


def safe_main(argv=None):
    """Entry point: any unexpected error is reported by type only (no messages that could carry
    hosts, DSNs or credentials)."""
    try:
        main(argv)
    except SystemExit:
        raise
    except ImportErrorExplicit as e:
        print(json.dumps({"FAILED": str(e)[:300]}), file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(json.dumps({"FAILED": type(e).__name__, "detail": "suppressed"}), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    safe_main()
