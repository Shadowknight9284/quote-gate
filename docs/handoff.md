# Handoff: state and next tasks (2026-10-10)

Main: PR #1 (baseline) and PR #2 (Coinbase real-data, audited) merged; CI green on `main`.
PR #3 (this docs set + submission draft) is the docs PR. Read `AGENTS.md` first.

## In flight
### H4 — Hermes: L2 book edge-case tests (launched 18:27Z)
- Workspace: `qg-tasks/H4-l2book-tests` on the delegation VM, baseline `1b96b03`; allowed file
  `tests/test_l2book_hermes.py`; task text in that workspace's `TASK.md` (10 listed cases).
- Review procedure (any agent can do this):
  1. `tools/hermes_task.sh fetch H4-l2book-tests artifacts/delegation` (needs the gitignored
     `docs/private/remote.env`; otherwise skip and mark H4 abandoned).
  2. Reject if the patch touches any path other than `tests/test_l2book_hermes.py`.
  3. Apply in a scratch copy; run `python3 -m unittest tests.test_l2book_hermes -v`.
     A failing test is either a real defect (reproduce it by hand) or a wrong test (drop it).
  4. Mutation check: in a scratch copy, break `l2book.py` (e.g. make zero size not delete; make
     `apply_batch` apply changes before validating all; drop the crossed-book check) and require the
     accepted tests to fail. Keep only tests that assert the contract.
  5. Record the outcome in `docs/delegation-log.md` and `docs/task-board.md`.

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
