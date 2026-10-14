# State

Verification levels: **planned** · **implemented** · **tested locally** · **tested across VMs** ·
**benchmarked** · **human verified**. Each row lists the highest level reached. "Human verified" means
a person independently re-ran it; nothing has reached that level yet.

## Features
| Feature | Level | Where |
|---|---|---|
| Quote validation, per-venue epochs, sequence ordering, local-receipt freshness | tested locally; tested across VMs | `book.py`, `tests/test_book_router.py` |
| Best-price routing with tie-break and no-route reasons | tested locally | `router.py` |
| NDJSON protocol, venue simulator, aggregator reconnect/backoff, client port | tested locally; tested across VMs; benchmarked | `protocol.py`, `venue.py`, `aggregator.py` |
| Feed ping/pong RTT, event log, freshness monitor | tested locally; benchmarked | `aggregator.py` |
| Route-lookup benchmark harness + report | benchmarked (loopback and M3) | `bench.py`, `report.py` |
| Quant signal, target-position policy, FIFO ledger with idempotent fills | tested locally | `quant.py`, `ledger.py` |
| Deterministic backtest + fixed 5-cell matrix | tested locally (results in paper) | `backtest.py`, `experiments.py` |
| Strategy-known vs evaluator-authoritative accounting | tested locally | `backtest.py` |
| Networked order path (order/exec/status/release), bounded idempotent store | tested locally (loopback) | `venue.py`, `execution.py`, `live.py` |
| UNKNOWN on timeout/disconnect, status reconciliation, lost-record handling | tested locally (loopback) | `live.py`, `tests/test_live_local.py` |
| Order path across VMs (venue on second VM, normal + dropped-ack) | tested across VMs (one run each, C3) | `docs/benchmarks.md` C3 |
| Second execution venue in live runner | planned | `live.py` (`exec_venues`) |
| Tiger Data importer (plan / verify-local) | tested locally without a database | `importer.py`, `normalize.py` |
| Tiger Data write safeguards (conflict detection, pre-existing schema validation) | tested locally without a database | `importer.py`, `tests/test_importer_safeguards.py` |
| Tiger Data initial read-only check (historical, before schema creation) | run once; read-only session verified; schema absent at that time; superseded by Phase 1 schema/import record below | `importer.py`, [analytics-example.md](analytics-example.md) |
| Tiger Data schema init + Phase 1 import (3 runs), re-import, conflict probe | tested against the real service once (`b15602e`) | `docs/analytics-example.md` |
| Tiger Data query validation (DB percentiles vs local) | tested once: 0 ns exact diff on 28 route stats + per-sample feed RTT | `docs/analytics-example.md` |
| Order path with venue as a separate process (loopback) | tested locally | `cli venue --market-seed`, `live --venue` |
| Two-page paper | implemented (compiles, 2 pages); references agent-checked (`paper/REFERENCES.md`) | `paper/quotegate.tex` |
| Full and quick demos (PR #6 merged) | tested locally; full matrix means reproduced; quick skips only matrix | `scripts/demo.sh`, [demo.md](demo.md) |
| Aggregator fairness (flooding client) and shutdown with open clients | fixed after CI exposed them; regression tested | `tests/test_reconnect_ping.py` |

## Final state at close (2026-10-14)
`main` = `b24d506`, with PRs #1–#13 merged. Suite: **224 tests, OK** (agent-run). CI succeeded on that commit.
The browser console (PRs #10–#11) and the formal paper (PR #12) were added after the checkpoint below.
The project is closed; see [handoff.md](handoff.md#project-closed-2026-10-14) for merge links, the one human-run console observation, and the known inconsistencies.

## Verification checkpoint (2026-10-10, historical)
Main includes merged PRs #1–#6; integration baseline `50b33b7` (PR #6 merge).
Standard suite `python3 -m unittest discover -s tests -t .`: **205 tests, OK**.
The collection assertion in [.github/workflows/tests.yml](../.github/workflows/tests.yml)
collected **205 tests from 19 modules; all 19 test files**, with no import failures.
CI runs the suite and collection check on every push and pull request.
Merge records and CI links: [handoff.md](handoff.md).

Agent-run verification on the integrated code:

- Full `scripts/demo.sh`: includes all five frozen matrix cells; every documented mean matches
  [quant.md](quant.md). Quick mode runs the same first three sections and skips the matrix;
  it is not full quant verification. Invalid arguments fail with usage.
- Both demos: 1217/1217 fills, ledger/venue mismatched 0; run sequentially, no shared-port overlap.
- Paper: `cd paper && latexmk -g -pdf -interaction=nonstopmode -halt-on-error quotegate.tex`,
  then `pdfinfo quotegate.pdf`: **2 pages**.

These results and historical records are agent-run, not human-verified. No database or remote
machine was accessed during consolidation. The delegation VM's last reported process check is
in [handoff.md](handoff.md); it has not been rechecked.

## Real-data extension (merged into main, PR #2; H4 tests in PR #4)
| Feature | Level | Where |
|---|---|---|
| Coinbase BTC-USD level2_batch book reconstruction (exact units, batch-atomic, invalidation) | tested locally; H4 accepted regression tests merged | `l2book.py`, `tests/test_l2book.py`, `tests/test_l2book_hermes.py` |
| Read-only stdlib WebSocket capture, bounded | run once against the public feed (600 s, 0 reconnects) | `ws_client.py`, `coinbase_capture.py` |
| Deterministic replay with hypothetical execution | tested locally (8 fixtures, hand-traced values); replay of the capture byte-identical twice | `coinbase_replay.py`, `docs/realdata.md` |

## Claim → evidence
Current integrated code is on `main`. Rows below retain historical revisions and run evidence;
they do not imply fresh remote verification. Raw artifacts are gitignored; synthetic/loopback
outputs regenerate with the commands shown. Historical cross-VM and real-data captures cannot
be reproduced identically.

| Claim | Revision / run | Command / artifact | Level | Limitation |
|---|---|---|---|---|
| Rule tests: fixture, staleness boundary, reconnect, dup/old sequence | `4b0ec45` | `python3 -m unittest tests.test_book_router` | tested locally | injected clock, not wall time |
| Venue B on a second VM: wire == generator, freeze → stale, restart → new epoch (12/12 checks) | runtime of published `475d900`; run m3-20261010T154645 | `scripts/m3_check.py` (needs private env) | tested across VMs | same LAN, possibly same physical host; market-data path only |
| Feed RTT loopback ≈ 0.12 ms p50, cross-VM ≈ 0.57 ms p50 (n=330 each) | M3 runs | `docs/benchmarks.md` | benchmarked | summary only; p99 not meaningful; app-level RTT, not quote age |
| Route lookups unaffected by venue placement (~150 µs p50) | M3 runs | `quote_gate.bench` | benchmarked | single-host client→aggregator RTT; never touches a venue |
| 0 freshness violations over 60 000 routed replies under faults | M4 fault run | `scripts/m4_faults.sh` | benchmarked (loopback) | internal-consistency observation, not independent proof |
| Throughput plateau ≈ 45k route lookups/s | M4 sweep | `quote_gate.bench` | benchmarked (1 trial/rate) | client and aggregator on one VM; bottleneck not isolated |
| Planted signal detectable: hit rate .633 vs .502 null | `d4af524`; matrix 20261010T165552Z | `python3 -m quote_gate.experiments` | tested locally | synthetic model with planted mechanism |
| Strategy loses in every cell; paired controlled−null +1592±107 on 30/30 seeds | same | same; `paper/fig_paired.dat` | tested locally | frozen parameters, 30 seeds, this model only |
| Live loopback: 1217/1217 orders resolved by exec; ledger == venue fill log; exec RTT p50 ≈ 53 µs | `4b0ec45`, seed 101, 2000 steps | `python3 -m quote_gate.live --steps 2000` | tested locally | venue and aggregator in one event loop; RTT not comparable to cross-host |
| Dropped ack / disconnect after exec / venue restart behave per contract O1 | `4b0ec45` | `python3 -m unittest tests.test_live_local` | tested locally | loopback; fault hooks are injected |
| Separate-process loopback: normal 3012/3012 filled, exec RTT p50 ≈ 130 µs; drop-ack-every-50: 21 timeouts → 21 resolved by status, ≈301 ms to reconciled outcome; ledger == venue fill log in both | pre-C3 rehearsal | `cli venue --market-seed 101 ...` + `live --venue 127.0.0.1:19105` | tested locally | same VM; not comparable to the single-process 53 µs figure (process boundary added) |
| C3 cross-VM order path: 3012/3012 filled (normal), 21/21 dropped acks recovered by status (≈302 ms), ledger == venue fill log, no duplicate executions, max abs position 5; exec RTT p50 649 µs remote vs 132 µs separate-process local | `982cc03`, task c3-20261010T171636Z | `scripts/remote_venue.sh` + `quote_gate.live --venue` + `--reconcile` | tested across VMs | one seed, one run each; same LAN, possibly same physical host; application RTT |
| Phase 1 import: 3 runs, 60 574 timed, 120 000 untimed, 48 events, 252 summaries; re-import inserts 0; conflict probe aborts with content unchanged | `b15602e` | `quote_gate.importer init-schema/apply/probe`; artifacts/tiger (local) | tested against real service once | one service, one import; Phase 2 and C3 not imported |
| DB nearest-rank percentiles equal local recomputation (0 ns, 28 route stats; feed RTT per-sample) | `b15602e` | `db/queries.sql` via read-only session | tested once | same populations only; remote feed RTT summary-only |
| Dedup lifetime: until released **and** evicted; evicted ID re-executes | current | `tests/test_live_local.py` | tested locally | in-memory; not restart-safe |

## Known gaps
- Coordinator restart loses all strategy/ledger/pending-order state (in memory).
- Venue order store is in memory; dedup is not restart-safe.
- Tiger Data: only the historical three Phase 1 runs were imported and query-validated; Phase 2,
  C3, and real-data artifacts remain unimported. New imports/schema changes need human approval;
  no CI runs database tests (by design), and live service state was not rechecked.
- Pinger behaviour under socket write errors unverified.
