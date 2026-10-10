# Contract T2: benchmark summary → Markdown report

Implementer: Hermes. Allowed files: `quote_gate/report.py`, `tests/test_report.py`. No others.
Stdlib only; tests use `unittest`. Pure function: no filesystem, network, clocks, printing.
Do not import other `quote_gate` modules.

## API

```python
def render_markdown(summary: dict) -> str
```

`summary` has the shape of `examples/summary.json` (a dict with `meta` and `trials`).
Raise `ValueError` if `meta` or `trials` is missing, or `trials` is not a list.

## Output (exact structure)

1. Line 1: `# Benchmark: <meta.label>` ; blank line.
2. A bullet list, one line each, in this order: `- run_id: …`, `- git: …`, `- python: …`,
   `- offered rate: <rate> req/s`, `- duration: <duration_s> s (warm-up <warmup_s> s excluded)`,
   `- connections: <connections>`, `- trials: <trials>`, `- instrumentation: <instrumentation>`.
   Missing meta keys render as `n/a`. Blank line.
3. `## Per-trial results` then a Markdown table with header exactly:
   `| trial | offered | completed | not_sent_backlog | steady_samples | throughput rps | send-to-completion p50/p95/p99 µs | schedule-to-completion p50/p95/p99 µs | generator-delay p99 µs | errors |`
   One row per trial in list order. Latency cells are `p50 / p95 / p99`; append ` *` to the
   cell when that metric's `p99_meaningful` is false. Missing metric → `n/a`.
   `errors` = sum of `outcomes` values whose key is `ERROR` or starts with `FAIL:`.
4. `## Routing outcomes` then a table `| trial | outcome | count |` rows sorted by trial, then outcome key.
5. `## Notes` with these exact bullets:
   - `- Latency is measured on the client host only (monotonic clock); no cross-host one-way latency is claimed.`
   - `- schedule-to-completion includes generator delay and connection wait; send-to-completion is the client-observed round trip, not server service time.`
   - `- * marks p99 computed from fewer than 1000 samples (not meaningful).`
6. Output ends with exactly one `\n`.

Numbers render via `str()` of the JSON value (no reformatting).

## Required tests
- Renders `examples/summary.json`-shaped input (embed a copy as a dict literal in the test) with correct title, header row, one row per trial.
- `*` marker appears when `p99_meaningful` is false and not otherwise.
- errors column counts `ERROR` and `FAIL:*` only.
- Missing meta key → `n/a`; missing metric → `n/a`.
- Missing `trials` → ValueError.
- Output ends with a single newline.

## Amendment (M3, Claude)
Latency fields renamed per review: service_us → send_to_completion_us, from_schedule_us →
schedule_to_completion_us, generator_lag_us → generator_delay_us; header and second note
updated accordingly. Expected rendering otherwise unchanged.
