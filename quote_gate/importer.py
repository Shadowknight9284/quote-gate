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


def _db_params(table, row):
    p = dict(row)
    if table == "experiment_runs":
        p["run_started_utc"] = _utc(p.pop("run_started_utc_ns"))
    if "observed_at_utc_ns" in p:
        p["observed_at"] = _utc(p.pop("observed_at_utc_ns"))
    if table == "venue_events":
        p["detail"] = json.dumps(p["detail"])
    return p


def apply_plan(conn, plan):
    """One transaction per run; idempotent inserts; verifies counts. Rolls back on any error."""
    run_id = plan["experiment_runs"][0]["run_id"]
    try:
        with conn.transaction():
            with conn.cursor() as cur:
                for t in TABLES:
                    if plan[t]:
                        cur.executemany(INSERTS[t], [_db_params(t, r) for r in plan[t]])
                counts = {}
                for t in TABLES:
                    cur.execute(COUNT_SQL[t] if t != "experiment_runs" else
                                "SELECT count(*) FROM quotegate.experiment_runs WHERE run_id = %s", (run_id,))
                    counts[t] = cur.fetchone()[0]
                expected = {t: len(plan[t]) for t in TABLES}
                if counts != expected:
                    raise ImportErrorExplicit(f"{run_id}: count mismatch db={counts} source={expected}")
    except ImportErrorExplicit:
        raise
    except Exception as e:      # psycopg errors, timeouts
        raise ImportErrorExplicit(f"{run_id}: {type(e).__name__}") from e
    return counts


def main(argv=None):
    p = argparse.ArgumentParser(prog="quote_gate.importer")
    p.add_argument("cmd", choices=["plan", "verify-local", "init-schema", "apply"])
    p.add_argument("--root", default="artifacts")
    p.add_argument("--confirm", action="store_true", help="required for apply")
    a = p.parse_args(argv)
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
        if not a.confirm:
            sys.exit("apply requires --confirm")
        params = load_tiger_env()
        print(json.dumps({"target": target_identifier(params)}))
        import psycopg      # optional dependency
        with psycopg.connect(**params, connect_timeout=10, autocommit=False,
                             options="-c statement_timeout=60000") as conn:
            for pl in plans:
                try:
                    print(json.dumps({"run_id": pl["experiment_runs"][0]["run_id"],
                                      "committed": apply_plan(conn, pl)}))
                except ImportErrorExplicit as e:
                    failures.append({"run_id": pl["experiment_runs"][0]["run_id"], "error": str(e)})
    for f in failures:
        print(json.dumps({"FAILED": f}), file=sys.stderr)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
