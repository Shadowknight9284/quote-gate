# Task board

Deadline: Sunday 11:00 (confirmed). Code freeze target: Sunday 09:30.
Statuses use the levels in [state.md](state.md); "done" is not used.

## File ownership
| Owner | Files |
|---|---|
| Teammate | **not yet recorded** — no assignment has been confirmed. Before editing, agree on files here. |
| Claude (integration/review) | everything currently under `quote_gate/`, `tests/`, `scripts/`, `db/`, `docs/`, `paper/` |
| Hermes (delegated, reviewed) | none owned; delegated test files are listed below after acceptance |

To avoid overlapping editors: claim a file in this table (via a commit) before changing it.

## Product tasks
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
| E1 | Tiger Data schema/queries/importer | Claude | safeguards tested without DB; read-only check run; schema/import awaiting approval |

## Hermes tasks (delegated; accepted artifacts listed separately)
| ID | Task | Outcome | Accepted artifact |
|----|------|---------|-------------------|
| T1 | Synthetic generator | accepted after 1 repair | `quote_gate/synthetic.py`, `tests/test_synthetic.py` |
| T2 | Report formatter | rejected after 2 repairs | none (Claude wrote `report.py`) |
| T3 | Artifact normalizer | rejected after 2 repairs | none (Claude wrote `normalize.py`) |
| A1 | Audit of validation/freshness/reconnect/ping | no confirmed defects; patch rejected | none |
| H1 | Ledger regression tests | partially accepted (13 kept, 1 rejected, 2 out-of-scope files discarded) | `tests/test_ledger_hermes.py` |
| H4 | L2 book edge-case tests | accepted (10/10; 6/6 mutants killed); 0 repairs | `tests/test_l2book_hermes.py` |
| H2 | Order idempotency regression tests | rejected (7/8 tests asserted non-contract frames); no defects confirmed | none |

Details: [delegation-log.md](delegation-log.md).
