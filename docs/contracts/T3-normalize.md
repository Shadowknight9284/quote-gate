# Contract T3: pure normalization of route-benchmark artifacts

Implementer: Hermes. Allowed files: `quote_gate/normalize.py`, `tests/test_normalize.py`.
Python 3.12 stdlib only; tests use `unittest`. Pure functions: no file/network/DB access,
no clocks, no printing, no environment reads. Do not import other `quote_gate` modules.
Inputs are already-parsed JSON values (dicts/lists), never paths.

## Input artifacts (see `examples/`)

`summary` = parsed `summary.json`: `{"meta": {...}, "trials": [...]}`.
Required meta keys: `run_id` (str, non-empty), `label` (str), `rate` (int|float > 0),
`duration_s` (int|float > 0), `warmup_s` (int|float >= 0), `connections` (int >= 1).
Optional meta keys (None if absent): `git`, `python`, `operation`, `target`.

Raw route observations for one trial, parsed line by line from `trialN.raw.jsonl`:
- **v1** (no header): every line is a list `[scheduled_mono_ns, sent_mono_ns, completed_mono_ns, outcome]`.
- **v2**: first line is a header object
  `{"schema": "qg.route_obs.v2", "observer": str, "anchor_wall_ns": int, "anchor_mono_ns": int,
    "fields": ["scheduled_mono_ns", "sent_mono_ns", "completed_mono_ns", "outcome", "quote_age_ns"]}`
  followed by lists of 5 elements in that order (`quote_age_ns` is int or null).

"int" means `type(x) is int` (bool rejected). Monotonic values must be ints >= 0 and satisfy
`scheduled <= sent <= completed`. `outcome` is a non-empty str.

## API (exact)

```python
class NormalizationError(ValueError): ...

def run_row(summary: dict, placement: str) -> dict
def route_sample_rows(run_id: str, trial: int, lines: list, warmup_s: float) -> list[dict]
def summary_metric_rows(run_id: str, trial_summary: dict) -> list[dict]
```

### run_row
`placement` must be one of `"loopback"`, `"cross_vm_lan"`, `"unknown"`.
Returns exactly these keys:
`run_id, label, git, python, operation, target, placement, rate_rps, duration_s, warmup_s,
connections, trials` — `rate_rps`, `duration_s`, `warmup_s` as float; `connections` int;
`trials` = `len(summary["trials"])`. Raise `NormalizationError` on any missing/invalid required key.

### route_sample_rows
Detect v1 vs v2 from `lines[0]` (dict ⇒ v2 header; list ⇒ v1). Empty `lines` ⇒ `[]`.
v2 header: `schema` must equal `"qg.route_obs.v2"` and `fields` must equal the list above,
otherwise `NormalizationError`. Any v1/v2 row with wrong arity, wrong types, negative values,
or broken ordering ⇒ `NormalizationError` whose message contains the 0-based data-row index.

`start = min(scheduled)` over all data rows of the trial.
For data row index `i` (0-based, counting data rows only, in input order) return:
```
{"sample_key": f"{run_id}:route:{trial}:{i}",
 "run_id": run_id, "trial": trial, "row_index": i,
 "schema": "v1" | "v2",
 "observer": header["observer"] (v2) | None (v1),
 "scheduled_mono_ns", "sent_mono_ns", "completed_mono_ns": ints as given,
 "send_to_completion_ns": completed - sent,
 "schedule_to_completion_ns": completed - scheduled,
 "schedule_to_send_ns": sent - scheduled,
 "outcome": str,
 "quote_age_ns": int | None   (v1 ⇒ None),
 "warmup": scheduled < start + round(warmup_s * 1e9),
 "completed_at_utc_ns": anchor_wall_ns + (completed - anchor_mono_ns)  (v2)  | None (v1)}
```
v1 rows must have `completed_at_utc_ns = None`: **never fabricate a wall-clock time**.
`trial` must be an int >= 0; `warmup_s` int|float >= 0.

### summary_metric_rows
`trial_summary` is one element of `summary["trials"]`. For each present key among
`schedule_to_completion_us, send_to_completion_us, schedule_to_send_us, generator_delay_us`
(a dict with p50, p95, p99, max in **microseconds**) emit one row per statistic:
```
{"metric_key": f"{run_id}:{trial}:{metric}:{stat}", "run_id", "trial", "source": "client",
 "metric": metric with the "_us" suffix replaced by "_ns", "stat": "p50"|"p95"|"p99"|"max",
 "value_ns": round(value_us * 1000)}
```
Also for `server_cumulative` (if present): `route_ns` and `process_ns` (already ns) →
source `"aggregator"`, metric `"route_ns"`/`"process_ns"`; and each venue `V` in
`feed_rtt_ns` → source `"aggregator"`, metric `f"feed_rtt_ns:{V}"`. Stats p50, p95, p99, max;
plus a row with stat `"count"` holding the `count` value. Values in ns dicts must be ints.
A `*_us` value that is not int/float, is negative, or a `*_ns` value that is not int ⇒
`NormalizationError` (malformed unit). Unknown metric keys are ignored.
Row order: client metrics in the order listed above, then route_ns, process_ns, then feed
venues sorted by name; within each metric: p50, p95, p99, max, (count).

## Required tests (`tests/test_normalize.py`)
1. run_row on `examples/summary.json` (embed a dict literal) gives exact expected dict; missing `run_id` and bad `placement` raise.
2. v1 rows: exact keys/values for a 3-row example, `completed_at_utc_ns is None`, `observer is None`.
3. v2 rows: UTC derivation exact; bad schema string and bad fields list raise.
4. Malformed rows: wrong arity, float ns, bool, negative, sent < scheduled each raise with the row index in the message.
5. Warm-up flag boundary: scheduled exactly at start + warmup is NOT warm-up.
6. Stable IDs: calling twice gives identical sample_keys; keys unique within a trial.
7. summary_metric_rows: µs→ns conversion (e.g. 150.4 → 150400), malformed unit raises, counts present, ordering.
8. Empty lines → [].

Run: `python3 -m unittest discover -s tests -t .`  Deliverable: those two files + report < 10 lines.
