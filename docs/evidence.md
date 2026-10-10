# Experiment evidence store (Tiger Data)

Pipeline: local artifacts → `quote_gate.importer` (post-run, separate process) → Tiger Data
(schema `quotegate`, `db/schema.sql`) → SQL (`db/queries.sql`) → Markdown report.
Routing never reads the database; core tests and demo run without it.

## Tables
| table | kind | content |
|---|---|---|
| experiment_runs | relational | one row per benchmark run; placement from `db/placements.json` |
| latency_samples | hypertable on observed_at | per-observation route RTT and feed RTT with derived UTC time |
| latency_samples_untimed | relational | same columns, for v1 artifacts with no wall-clock anchor |
| venue_events | hypertable on observed_at | connect / disconnect / status transitions (incl. stale-detection delay) |
| run_metric_summaries | relational | summary stats recorded by the run (only source for M3 feed RTT) |

## Time semantics
- v2 artifacts carry a header with one paired reading `(anchor_wall_ns, anchor_mono_ns)` taken
  when the observer (bench client or aggregator) started, plus an `observer` alias.
  `observed_at = anchor_wall_ns + (mono − anchor_mono_ns)`. This is **derived** time: wall-clock
  steps/slew after the anchor are not reflected; monotonic stamps are the primary measurement.
- v1 artifacts (runs before M4) have no anchor and no observer: rows go to
  `latency_samples_untimed` with NULLs. No per-sample time is invented.
- `run_started_utc` comes from `meta.run_started_utc_ns` when present, otherwise from the run_id
  string (VM local time, which was UTC+0000 when checked; second resolution), labelled by
  `run_started_source`.
- Cross-host clocks are never subtracted. Feed RTT and route RTT are single-host round trips.

## Keys and idempotency
`sample_key = run_id:route:trial:row` / `run_id:feed:row`, `event_key = run_id:event:row`,
`metric_key = run_id:trial:metric:stat`. Inserts use `ON CONFLICT DO NOTHING`; re-importing the
same artifacts adds no rows. One transaction per run; after insert, per-table counts for the run
must equal the source counts or the transaction rolls back and the run is reported FAILED.

## Open before any database write
- Conflict detection: `ON CONFLICT DO NOTHING` alone would silently keep an old row if a key were
  reused with different content. The importer must compare existing rows for the run's keys and
  fail on any difference before inserting. Not yet implemented.
- Pre-existing object validation: before `create_hypertable`, verify any existing
  `quotegate.*` table has exactly the expected columns/types/constraints; stop otherwise.
  `CREATE TABLE IF NOT EXISTS` is not validation. Not yet implemented.
- Remote feed RTT is summary-only (M3); no per-sample remote timeline or time-bucketed chart may
  be produced from it.

## Verification
`verify-local` recomputes client percentiles from normalized samples (nearest rank, same as
SQL `percentile_disc`) and compares to recorded summaries; tolerance 50 ns because summaries are
rounded to 0.1 µs. Result on current artifacts: 88 comparisons, 0 mismatches. Pre-rename runs
(smoke, sweep4000–16000) use old field names, so their summaries are not compared.

## Credentials
`docs/private/tiger.env` (gitignored, must be mode 600) is parsed as KEY=VALUE by the importer,
never sourced by a shell. Keys: see `db/tiger.env.example`. The password is never printed; reports
show `user@host:port/db` only. Database support needs `.venv` with `requirements-db.txt`.
