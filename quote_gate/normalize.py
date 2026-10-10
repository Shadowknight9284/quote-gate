"""Pure normalization of route-benchmark artifacts into rows. Contract: docs/contracts/T3-normalize.md."""
from __future__ import annotations

ROUTE_V2_SCHEMA = "qg.route_obs.v2"
ROUTE_V2_FIELDS = ["scheduled_mono_ns", "sent_mono_ns", "completed_mono_ns", "outcome", "quote_age_ns"]
PLACEMENTS = ("loopback", "cross_vm_lan", "unknown")
CLIENT_METRICS = ("schedule_to_completion_us", "send_to_completion_us", "schedule_to_send_us",
                  "generator_delay_us")
STATS = ("p50", "p95", "p99", "max")


class NormalizationError(ValueError):
    pass


def _is_int(x) -> bool:
    return type(x) is int


def _is_num(x) -> bool:
    return type(x) in (int, float)


def run_row(summary: dict, placement: str) -> dict:
    if not isinstance(summary, dict) or not isinstance(summary.get("meta"), dict) \
            or not isinstance(summary.get("trials"), list):
        raise NormalizationError("summary needs dict 'meta' and list 'trials'")
    if placement not in PLACEMENTS:
        raise NormalizationError(f"placement must be one of {PLACEMENTS}")
    m = summary["meta"]
    if not (isinstance(m.get("run_id"), str) and m["run_id"]):
        raise NormalizationError("meta.run_id must be a non-empty str")
    if not isinstance(m.get("label"), str):
        raise NormalizationError("meta.label must be a str")
    for k, lo, strict in (("rate", 0, True), ("duration_s", 0, True), ("warmup_s", 0, False)):
        v = m.get(k)
        if not _is_num(v) or (v <= lo if strict else v < lo):
            raise NormalizationError(f"meta.{k} invalid")
    if not _is_int(m.get("connections")) or m["connections"] < 1:
        raise NormalizationError("meta.connections must be int >= 1")
    return {"run_id": m["run_id"], "label": m["label"], "git": m.get("git"),
            "python": m.get("python"), "operation": m.get("operation"), "target": m.get("target"),
            "placement": placement, "rate_rps": float(m["rate"]), "duration_s": float(m["duration_s"]),
            "warmup_s": float(m["warmup_s"]), "connections": m["connections"],
            "trials": len(summary["trials"])}


def _check_row(i, row, arity):
    if not isinstance(row, list) or len(row) != arity:
        raise NormalizationError(f"row {i}: expected list of {arity}")
    s, t, c, outcome = row[:4]
    if not all(_is_int(v) and v >= 0 for v in (s, t, c)):
        raise NormalizationError(f"row {i}: monotonic values must be ints >= 0")
    if not s <= t <= c:
        raise NormalizationError(f"row {i}: requires scheduled <= sent <= completed")
    if not (isinstance(outcome, str) and outcome):
        raise NormalizationError(f"row {i}: outcome must be a non-empty str")
    if arity == 5 and row[4] is not None and not _is_int(row[4]):
        raise NormalizationError(f"row {i}: quote_age_ns must be int or null")


def route_sample_rows(run_id: str, trial: int, lines: list, warmup_s: float) -> list[dict]:
    if not _is_int(trial) or trial < 0:
        raise NormalizationError("trial must be int >= 0")
    if not _is_num(warmup_s) or warmup_s < 0:
        raise NormalizationError("warmup_s must be a number >= 0")
    if not lines:
        return []
    header = None
    data = lines
    if isinstance(lines[0], dict):
        header = lines[0]
        if header.get("schema") != ROUTE_V2_SCHEMA or header.get("fields") != ROUTE_V2_FIELDS \
                or not isinstance(header.get("observer"), str) \
                or not _is_int(header.get("anchor_wall_ns")) or not _is_int(header.get("anchor_mono_ns")):
            raise NormalizationError("invalid v2 header")
        data = lines[1:]
    arity = 5 if header else 4
    for i, row in enumerate(data):
        _check_row(i, row, arity)
    if not data:
        return []
    start = min(r[0] for r in data)
    cutoff = start + round(warmup_s * 1e9)
    out = []
    for i, row in enumerate(data):
        s, t, c, outcome = row[:4]
        out.append({
            "sample_key": f"{run_id}:route:{trial}:{i}", "run_id": run_id, "trial": trial,
            "row_index": i, "schema": "v2" if header else "v1",
            "observer": header["observer"] if header else None,
            "scheduled_mono_ns": s, "sent_mono_ns": t, "completed_mono_ns": c,
            "send_to_completion_ns": c - t, "schedule_to_completion_ns": c - s,
            "schedule_to_send_ns": t - s, "outcome": outcome,
            "quote_age_ns": row[4] if header else None, "warmup": s < cutoff,
            "completed_at_utc_ns": header["anchor_wall_ns"] + (c - header["anchor_mono_ns"]) if header else None,
        })
    return out


def _ns_stats(run_id, trial, metric, d, with_count):
    rows = []
    for stat in STATS + (("count",) if with_count else ()):
        if stat not in d:
            continue
        v = d[stat]
        if not _is_int(v) or v < 0:
            raise NormalizationError(f"{metric}.{stat}: ns value must be int >= 0")
        rows.append({"metric_key": f"{run_id}:{trial}:{metric}:{stat}", "run_id": run_id,
                     "trial": trial, "source": "aggregator", "metric": metric, "stat": stat,
                     "value_ns": v})
    return rows


def summary_metric_rows(run_id: str, trial_summary: dict) -> list[dict]:
    trial = trial_summary.get("trial")
    if not _is_int(trial) or trial < 0:
        raise NormalizationError("trial_summary.trial must be int >= 0")
    rows = []
    for key in CLIENT_METRICS:
        d = trial_summary.get(key)
        if d is None:
            continue
        metric = key[:-3] + "_ns"
        for stat in STATS:
            if stat not in d:
                continue
            v = d[stat]
            if not _is_num(v) or v < 0:
                raise NormalizationError(f"{key}.{stat}: µs value must be a number >= 0")
            rows.append({"metric_key": f"{run_id}:{trial}:{metric}:{stat}", "run_id": run_id,
                         "trial": trial, "source": "client", "metric": metric, "stat": stat,
                         "value_ns": round(v * 1000)})
    sc = trial_summary.get("server_cumulative") or {}
    for metric in ("route_ns", "process_ns"):
        if isinstance(sc.get(metric), dict):
            rows += _ns_stats(run_id, trial, metric, sc[metric], True)
    for venue in sorted(sc.get("feed_rtt_ns") or {}):
        d = sc["feed_rtt_ns"][venue]
        if isinstance(d, dict):
            rows += _ns_stats(run_id, trial, f"feed_rtt_ns:{venue}", d, True)
    return rows
