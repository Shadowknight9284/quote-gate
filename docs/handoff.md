# Handoff: state and next tasks (2026-10-10)

Main: PR #1 (baseline) and PR #2 (Coinbase real-data, audited) merged; CI green on `main`.
PR #3 (this docs set + submission draft) is the docs PR. Read `AGENTS.md` first.

## In flight
Nothing. H4 (Hermes, L2 book edge-case tests) was accepted: 10/10 tests, 6/6 mutants killed;
see `docs/delegation-log.md`. Use the same procedure (path check, scratch apply, mutation check)
for any future delegated task.

## Ready tasks (each small, contract-first; good for a delegated agent)
| ID | Task | Allowed files | Acceptance |
|---|---|---|---|
| N1 | Markdown renderer for `docs/realdata-results/report.json` (pure function `render(report: dict) -> str`, fixed column order as in docs/realdata.md table) | `quote_gate/realdata_report.py`, `tests/test_realdata_report.py` | renders the committed report into exactly the table in docs/realdata.md; stdlib only |
| N2 | Regression tests for `ws_client` handshake header parsing (case-insensitive headers, missing Accept) | `tests/test_ws_handshake_extra.py` | fail on a mutant that compares header names case-sensitively |
| N3 | `scripts/demo.sh` option `--quick` skipping the quant matrix | `scripts/demo.sh` | demo transcript sections 1–3 unchanged |

## Needs human decision (do not start without approval)
- Tiger Data import of the real-data capture (schema expansion; proposal in `docs/tiger-data.md`).
- Any new remote deployment, cloud change, Devpost submission or public posting.
- Second execution venue, new strategies, or parameter changes.

## Known limitations (keep stating them)
Synthetic planted model; one 10-minute real-data sample with hypothetical fills/fees; single
coordinator; in-memory venue store; cross-VM runs on one LAN (possibly one physical host).
