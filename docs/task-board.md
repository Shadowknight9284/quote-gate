# Task board

Historical event targets: Sunday 11:00 deadline; Sunday 09:30 code freeze.
Current instruction: consolidate and stop feature expansion.
Statuses use the levels in [state.md](state.md); "done" is not used.

## File ownership
| Owner | Files |
|---|---|
| Teammate | **not yet recorded** — no assignment has been confirmed. Before editing, agree on files here. |
| Codex (sole implementation agent) | `quote_gate/`, `tests/`, `scripts/`, `db/`, `docs/`, `paper/`; ownership transferred by the human handoff; Claude has stopped editing |
| Hermes (delegated, reviewed) | none owned; delegated test files are listed below after acceptance |

To avoid overlapping editors: claim a file in this table (via a commit) before changing it.

## Product tasks (owners below record historical implementation attribution)
| ID | Task | Owner | Status |
|----|------|-------|--------|
| M1 | Quote validation, epochs, freshness, router, protocol, venue, aggregator | Claude | tested locally; tested across VMs |
| M2 | Scheduled-load benchmark client, report | Claude | benchmarked |
| M3 | Venue B on second VM (market-data path) | Claude | tested across VMs; benchmarked |
| M4 | Event log, freshness monitor, faults under load, saturation sweep | Claude | benchmarked (loopback) |
| Q1 | Quant slice + deterministic backtest + 5-cell matrix | Claude | tested locally |
| C1 | Networked order path, loopback, one execution venue | Claude | tested locally (loopback) |
| C2 | Second execution venue in live runner | — | planned (deferred) |
| R1 | Coinbase BTC-USD capture/replay (hypothetical execution) | Claude | merged after audit (PR #2) |
| C3 | Order path with venue on second VM (normal + dropped-ack) | Claude | tested across VMs (one run each) |
| P1 | Two-page paper | Claude | implemented (compiles); citations need human check |
| E1 | Tiger Data schema/queries/importer | Claude | Phase 1 schema + three runs imported and query-validated historically; new imports/schema changes blocked; see [analytics-example.md](analytics-example.md) |

## Hermes tasks (delegated; accepted artifacts listed separately)
| ID | Task | Outcome | Accepted artifact |
|----|------|---------|-------------------|
| T1 | Synthetic generator | accepted after 1 repair | `quote_gate/synthetic.py`, `tests/test_synthetic.py` |
| T2 | Report formatter | rejected after 2 repairs | none (Claude wrote `report.py`) |
| T3 | Artifact normalizer | rejected after 2 repairs | none (Claude wrote `normalize.py`) |
| A1 | Audit of validation/freshness/reconnect/ping | no confirmed defects; patch rejected | none |
| H1 | Ledger regression tests | partially accepted (13 kept, 1 rejected, 2 out-of-scope files discarded) | `tests/test_ledger_hermes.py` |
| H4 | L2 book edge-case tests | merged in PR #4; accepted (10/10; historical 6/6 mutants killed); 0 repairs | `tests/test_l2book_hermes.py` |
| H2 | Order idempotency regression tests | rejected (7/8 tests asserted non-contract frames); no defects confirmed | none |

Details: [delegation-log.md](delegation-log.md).

## Consolidated queue
PRs #1–#6 are merged; [handoff.md](handoff.md) records their merge commits and CI.
No feature implementation or delegated task is in flight. Codex remains sole implementation
owner; no Hermes task was launched during consolidation. Verification is agent-run only.

| ID | Task | State | Contract / files |
|---|---|---|---|
| N1 | Markdown real-data report renderer | ready, not started; feature expansion stopped | [handoff.md](handoff.md); `quote_gate/realdata_report.py`, `tests/test_realdata_report.py` |
| N2 | WebSocket handshake header regression tests | ready, not started; feature expansion stopped | [handoff.md](handoff.md); `tests/test_ws_handshake_extra.py` |
| N3 | Quick demo option | merged in PR #6; tested locally | `scripts/demo.sh`; [demo.md](demo.md) |

Real-data replay is merged with hypothetical execution; its capture is not in Tiger Data.
Database writes, new imports/schema changes, remote deployments and public submission remain
outside consolidation authorization. Remote process state has not been rechecked; the last
reported delegation-VM check is retained in [handoff.md](handoff.md).
