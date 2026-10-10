# Tiger Data (optional post-run analytics)

Status: **Phase 1 imported and query-validated (2026-10-10, revision `b15602e`)** — see
[analytics-example.md](analytics-example.md). Earlier status: a target has been configured
privately (identifier kept out of Git). One approved read-only check has run (2026-10-10):
session verified read-only; PostgreSQL 18.6; TimescaleDB 2.30.2 with `create_hypertable`;
schema `quotegate` absent (no pre-existing objects); CREATE privilege present. No DDL or writes yet. No import has been verified
against a real service. The core system, tests and demo do not need any of this.

Pipeline: local artifacts → `quote_gate.importer` (separate process, after runs) → Tiger Data schema
`quotegate` (`db/schema.sql`) → SQL (`db/queries.sql`) → report. Never routing state, execution
authority, or a recovery mechanism.

## Offline use (no database, no credentials, stdlib only)
```
python3 -m quote_gate.importer plan --root artifacts          # row counts per table per run
python3 -m quote_gate.importer verify-local --root artifacts  # recompute percentiles vs summaries
```
Needs local artifacts (gitignored); produce them with `quote_gate.bench` / `scripts/m4_faults.sh`.

## Credentialed use (requires explicit approval for the specific target)
1. `python3 -m venv .venv && .venv/bin/pip install -r requirements-db.txt` (psycopg 3.3.6).
2. Create `docs/private/tiger.env` (gitignored, `chmod 600`) from the placeholder-only
   [`db/tiger.env.example`](../db/tiger.env.example). It is parsed as KEY=VALUE by the importer,
   never sourced by a shell; no inline comments; unknown keys are rejected.
3. Read-only check (each step needs approval for the specific target):
   `.venv/bin/python -m quote_gate.importer check --confirm` — session forced read-only
   (`default_transaction_read_only=on`, 10 s statement timeout); runs only catalog SELECTs: server
   version, TimescaleDB extension version, `create_hypertable` availability, whether schema
   `quotegate` exists, CREATE privilege, and the pre-existing-object validation below. Prints the
   sanitized target and results; never prints credentials.
4. `init-schema --confirm`, then `apply --confirm` (writes; separate approval).

## Write-safety safeguards (implemented; tested without a database)
1. **Conflict detection** (`find_conflicts`, used by `apply_plan`): before inserting, existing rows
   for the run's keys are fetched and compared column by column (JSON by value, timestamps exactly,
   `imported_at` ignored). Any reused key with different content aborts that run's transaction
   before any INSERT. Identical re-imports are not conflicts.
2. **Pre-existing object validation** (`validate_existing_schema`, used by `init-schema` and
   `check`): every existing `quotegate.*` table must have exactly the expected columns, data types,
   nullability and primary key, and no other relations may exist in the schema; otherwise no DDL runs.
   `EXPECTED_SCHEMA` is checked against `db/schema.sql` by a test.
Tests: `tests/test_importer_safeguards.py` (8 tests; disabling either safeguard fails them).
Not yet verified against a real database.

## Tables
| table | kind | content |
|---|---|---|
| experiment_runs | relational | one row per benchmark run; placement from `db/placements.json` |
| latency_samples | hypertable (observed_at) | per-observation route RTT and feed RTT with derived UTC |
| latency_samples_untimed | relational | v1 artifacts with no wall-clock anchor (no time invented) |
| venue_events | hypertable (observed_at) | connect / disconnect / status transitions |
| run_metric_summaries | relational | recorded summary stats (only source for M3 cross-VM feed RTT) |

Time: `observed_at = anchor_wall_ns + (mono − anchor_mono_ns)` from one paired reading at observer
start — derived, not independently sampled. Keys are deterministic per run/row; one transaction per
run; per-table counts must match the source or the run rolls back and is reported FAILED.
Remote feed RTT is summary-only: no per-sample remote timeline may be built from it.
`verify-local` on current artifacts: 88 comparisons, 0 mismatches (50 ns tolerance = rounding of
summaries to 0.1 µs; same nearest-rank method as SQL `percentile_disc`).

## Proposed, not approved: real-data import (R1)
Separate from Phase 1; requires its own target-specific approval. Nothing below has been executed.
- **Schema change needed**: new table `quotegate.realdata_top` (hypertable on `observed_at`, chunk
  1 h): `observed_at timestamptz` (local receipt, from the capture's wall/mono pair), `event_key text`
  (`<capture_sha>:<session>:<local index>`), `capture_sha256`, `session int`, `local_index bigint`,
  `exchange_time text` (as received, not used for latency), `bid_ticks bigint`, `bid_units bigint`,
  `ask_ticks bigint`, `ask_units bigint`, `source text` (snapshot | l2update); PK (`event_key`,
  `observed_at`). Plus `quotegate.realdata_runs` (one row per capture + replay scenario summaries).
  This is a schema expansion and is **not** covered by the standing authorization.
- **Scope**: the one 600 s capture (`835e07e9…`): 9 356 top-of-book rows, 1 run row, 3 scenario rows.
- **Safeguards**: same as Phase 1 (target guard, schema validation before DDL, conflict detection,
  per-run pre/inserted/final counts, unchanged re-import, conflict probe), plus a check that the
  normalized sha256 recomputed at import time equals `ingestion.json`.
- **Queries**: spread and top-of-book size distributions by minute; imbalance vs next mid change.
