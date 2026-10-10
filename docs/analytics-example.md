# Analytics example (Tiger Data, Phase 1) — sanitized

What a partner would see after the approved Phase 1 import, without credentials or local artifacts.
Source revision for all database actions: `b15602e`. Target: a Tiger Cloud service (identifier kept in
the gitignored `docs/private/`). Schema `quotegate`; TimescaleDB 2.30.2; PostgreSQL 18.6.

## Objects created (2026-10-10)
5 tables (`experiment_runs`, `latency_samples`, `latency_samples_untimed`, `venue_events`,
`run_metric_summaries`); hypertables `latency_samples` (chunk 1 h on `observed_at`) and
`venue_events` (chunk 1 day). Validated against `db/schema.sql` before and after DDL.

## Import evidence (per run: pre-existing → inserted → final)
| Run | Table | pre | inserted | final |
|---|---|---|---|---|
| m4-faults (first import) | experiment_runs / latency_samples / venue_events / summaries | 0/0/0/0 | 1/60574/48/36 | 1/60574/48/36 |
| m4-faults (unchanged re-import) | same | 1/60574/48/36 | 0/0/0/0 | 1/60574/48/36 |
| m3-remoteB | experiment_runs / latency_samples_untimed / summaries | 0/0/0 | 1/60000/108 | 1/60000/108 |
| m3-localB | same | 0/0/0 | 1/60000/108 | 1/60000/108 |

Totals: 3 runs, 60 574 timed samples, 120 000 untimed samples, 48 venue events, 252 summary rows.
Content check (read-only): every source row present with identical content; 0 conflicts.

Conflict probe: one non-key value (`value_ns` of `m4-faults:0:schedule_to_completion_ns:p50`,
636 500 → 636 501) changed in memory only. Result: content-conflict error naming that key and column;
transaction rolled back (no committed row changes); stored value still 636 500; counts unchanged;
source artifact checksums unchanged.

## Query validation (`db/queries.sql`, read-only session)
- `route_rtt_by_run_trial` (client→aggregator route lookups; never touches a venue): 7 run/trial groups
  × {p50, p95, p99 send-to-completion, p99 schedule-to-completion} = 28 statistics.
  DB `percentile_disc` vs Python nearest rank on the identical population (non-warm-up samples,
  n = 16 000 per M3 trial, 56 000 for m4): **0 ns difference in all 28**. Vs the summaries recorded at
  run time: |diff| ≤ 48 ns, within the 50 ns bound implied by their rounding to 0.1 µs.
- `feed_rtt_by_placement` (per-sample; only m4-faults has it; loopback, both venues): 0 ns difference
  (n = 275 for A, 299 for B). Venue A's p95 = 263 ms reflects pongs queued while venue A was
  deliberately frozen (SIGSTOP) in the fault scenario.
- `feed_rtt_summaries_by_placement` (summary-only; M3): venue B p50 574 µs cross-VM vs 118 µs loopback
  (n = 329 each; p99 not meaningful). No per-sample remote feed RTT exists, so none is plotted.
- `venue_event_timeline`: 48 events, 20 stale transitions, stale-detection delay 48 µs – 1.08 ms.
- `route_rtt_per_second`: 31 one-second buckets for m4-faults (the only run with UTC-anchored route
  samples; includes warm-up); 60 000 samples total. M3 route samples are untimed and are not bucketed.

## Example query
```sql
SELECT r.placement, s.run_id, s.trial, count(*) AS n,
       percentile_disc(0.50) WITHIN GROUP (ORDER BY s.value_ns) AS p50_ns
FROM quotegate.latency_samples_untimed s JOIN quotegate.experiment_runs r USING (run_id)
WHERE s.kind = 'route' AND NOT s.warmup
GROUP BY 1, 2, 3 ORDER BY 2, 3;
```
