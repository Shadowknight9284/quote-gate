# Handoff: current consolidation and historical snapshot

Read `AGENTS.md` first (rules, code map, invariants). Current status below supersedes the historical snapshot. Commands and artifact checksums
remain available for reproduction; the blocked-action list remains in force.
All results are agent-verified only; nothing is human verified.

## Project closed (2026-10-14)
This section supersedes the checkpoints below; they are kept as history.
Final `main` is `b24d506` (PR #13 merge); its CI run [succeeded](https://github.com/Shadowknight9284/quote-gate/actions/runs/38150956855).
The standard suite ran 224 tests, OK, on that commit (agent-run, 2026-10-14).

| PR | Disposition | Merged |
|---|---|---|
| [#7](https://github.com/Shadowknight9284/quote-gate/pull/7) | documentation consolidation | 2026-10-10 |
| [#8](https://github.com/Shadowknight9284/quote-gate/pull/8) | paper wording fix | 2026-10-10 |
| [#9](https://github.com/Shadowknight9284/quote-gate/pull/9) | Lucera submission evidence package | 2026-10-10 |
| [#10](https://github.com/Shadowknight9284/quote-gate/pull/10) | local operations console (browser) | 2026-10-11 |
| [#11](https://github.com/Shadowknight9284/quote-gate/pull/11) | Check fills, guided demo, console redesign | 2026-10-11 |
| [#12](https://github.com/Shadowknight9284/quote-gate/pull/12) | formal paper (LaTeX) | 2026-10-11 |
| [#13](https://github.com/Shadowknight9284/quote-gate/pull/13) | README rewrite for reviewers | 2026-10-11 |

PRs #1–#6 are listed below. All 13 PRs are merged and none are open. No work is in flight, and no feature work is planned.

Human-run observation: on `5a5ab26` the user ran the console's Order faults card in a browser (seed 101).
**Restart venue mid-order** passed in 310.3 ms: the order was unresolvable and trading halted.
**Drop next ack** and **Disconnect after next order** reported *Not observed*, because the lost record had already halted trading, so no later order existed to fault.
No other human verification was recorded. Everything else in this file is agent-run.

Known inconsistencies, documented and not fixed at close:
- The paper says "at most one status query in flight". `live.py` actually sends one every 100 ms without waiting for a reply.
- The paper says exec/status frames are never dropped. The labelled fault hooks drop them on purpose.
- `docs/console.md` says A is the only execution venue. The backtest can route orders to B.
- The console's green end-of-run line says the books match even when the result is an "explained gap", which is identified uncertainty, not completed reconciliation.
- Console P&L marks at A's last quote even when that quote is stale. The backtest marks only when the quote is fresh.

Local disposition: the development checkout was removed. Before removal, the local-only branches (`local/*`, `pr-1`, `research/strategy-comparison`) and the gitignored artifacts and private config were archived outside the repository.
None of that material was pushed.

## Repository checkpoint (2026-10-10, historical)
`main` is the integrated starting point; implementation baseline `50b33b7` (PR #6 merge).
Documentation-only consolidation [PR #7](https://github.com/Shadowknight9284/quote-gate/pull/7)
updates this checkpoint without changing core code; its linked page records disposition and CI.
All results are agent-run, not human-verified.

| PR | Disposition | Merge commit | Merge CI |
|---|---|---|---|
| [#1](https://github.com/Shadowknight9284/quote-gate/pull/1) | merged: baseline | `05b7f4f` | historical success recorded in [autonomy-log.md](autonomy-log.md) |
| [#2](https://github.com/Shadowknight9284/quote-gate/pull/2) | merged: audited Coinbase replay | `9ea2042` | historical success recorded in [autonomy-log.md](autonomy-log.md) |
| [#3](https://github.com/Shadowknight9284/quote-gate/pull/3) | merged: agent docs/submission draft | `bce39da` | [success](https://github.com/Shadowknight9284/quote-gate/actions/runs/38075944458) |
| [#4](https://github.com/Shadowknight9284/quote-gate/pull/4) | merged: H4 regression tests | `3095818` | [success](https://github.com/Shadowknight9284/quote-gate/actions/runs/38077224478) |
| [#5](https://github.com/Shadowknight9284/quote-gate/pull/5) | merged: reconciled handoff | `7a4e60f` | [success](https://github.com/Shadowknight9284/quote-gate/actions/runs/38077295906) |
| [#6](https://github.com/Shadowknight9284/quote-gate/pull/6) | merged: quick demo/ownership | `50b33b7` | [success](https://github.com/Shadowknight9284/quote-gate/actions/runs/38077353760) |

H4 is complete and merged: 10/10 accepted tests; the historical review recorded 6/6 mutants
killed in [delegation-log.md](delegation-log.md). Both autonomy-log histories are preserved.
Current suite: **205 tests across 19 modules/files**, verified with
`python3 -m unittest discover -s tests -t .` and the collection assertion in
[tests.yml](../.github/workflows/tests.yml). See [state.md](state.md) for commands and provenance.

Full and quick demos passed sequentially: each produced 1217 fills, ledger/venue mismatched 0.
Default includes the matrix and reproduces all frozen means; quick skips only that section and
is not full quant verification. Invalid arguments fail with usage. See [demo.md](demo.md).
The paper rebuilt with `latexmk -g -pdf -interaction=nonstopmode -halt-on-error quotegate.tex`;
`pdfinfo quotegate.pdf` reports 2 pages (run from `paper/`).

Current real-data status: PR #2's audited read-only capture/replay is merged; one historical
600-second capture, hypothetical execution only. No new capture was run during consolidation.
Tiger Data: three Phase 1 runs historically imported and query-validated; Phase 2, C3 and real-data
captures remain unimported. No database access or writes occurred during consolidation; live
service state was not rechecked. See [realdata.md](realdata.md) and
[analytics-example.md](analytics-example.md).

Codex is the sole implementation owner. No feature implementation, capture, venue, benchmark
or delegated task is in flight at the completed verification checkpoint. Feature expansion is
stopped; N1/N2 below remain ready but unstarted, and N3 is merged. Local inspection found no
background QuoteGate/Hermes tasks after the sequential demos. The delegation VM's last reported
check is retained below; it has not been rechecked and no remote access was initiated.

For future delegated work, retain the reviewed procedure: allowed-path check, scratch apply,
independent tests, mutation check, review records. Never launch Hermes in this checkout.

## Historical repository snapshot — reported 2026-10-10T18:35Z
The following table records the original handoff, not current branch or PR state.
| Item | Value |
|---|---|
| Remote | `github.com/Shadowknight9284/quote-gate` |
| Default branch | `main` = `bce39da6c808c9b48b3b6506cba96290705b849f` (local = remote; CI push: success) |
| Handoff branch | `handoff/codex` (this file), based on `main`; PR opened for human merge |
| Working tree | clean |
| Open PRs | #4 `hermes/h4-l2book-tests` @ `24b1c56` — Hermes H4 tests, reviewed (10/10, 6/6 mutants killed), CI success on push + pull_request. **Not merged by request; human merge.** It also edits this file's "In flight" section — expect a trivial conflict; keep both facts (H4 accepted, nothing in flight). |
| Merged PRs | #1 baseline (`05b7f4f`), #2 Coinbase real data, audited (`9ea2042`), #3 agent docs + submission draft (`bce39da`) |
| Other remote branches | `publish/m1-m4` @ `883562a`, `feat/coinbase-replay` @ `136f2b9`, `docs/submission` @ `8c1d598` — all merged; safe to delete only with human approval |
| Local-only branches (never push) | `local/unsanitized-main`, `local/full-history-20261010` @ `720b0e0` (contain private deployment details in history; no upstream); `pr-1` @ `b15602e` (sanitized, unused) |

## Historical process report — not a current remote inspection
- At the original handoff, no work was reported in flight; H4 had finished and was in PR #4.
- Original local report: no `quote_gate`, capture, benchmark or Hermes runner running. Local
  inspection during consolidation found no such background tasks before testing.
- Delegation VM: the last reported verification at the original handoff found no `quote_gate`
  Python processes, no Hermes runs, and port 19102 not listening. Codex has not rechecked this
  machine; no remote access was initiated for consolidation.
  Leftover task workspaces `~/qg-tasks/{T1-synthetic,T2-report,T3-normalize,A1-audit,H1-ledger-tests,
  H2-order-tests,H4-l2book-tests}` and venue dirs `~/qg-venue/{m3-…,c3-…}` are inert files (kept).

## Exact commands (repo root, Python 3.12, standard library only)
```
# core tests (CI runs the same plus a collection check; expect "OK", 205 tests after H4)
python3 -m unittest discover -s tests -t .

# synthetic experiment (paper numbers; must reproduce docs/quant.md exactly)
python3 -m quote_gate.experiments --out /tmp/qg

# networked loopback order path (expect 1217/1217 filled, mismatched 0)
python3 -m quote_gate.live --steps 2000

# paper (needs TeX Live with pgfplots); expect 2 pages
cd paper && latexmk -pdf -interaction=nonstopmode quotegate.tex && pdfinfo quotegate.pdf | grep Pages

# full demo with transcript (~20 s; writes artifacts/demo/transcript.txt)
scripts/demo.sh

# real data: replay an existing capture (deterministic, offline)
python3 -m quote_gate.coinbase_replay --capture artifacts/realdata/20261010T175802Z-btcusd --out /tmp/replay
# real data: new capture (public, read-only network access to Coinbase; approved for 600 s / 250 MiB)
python3 -m quote_gate.coinbase_capture --out artifacts/realdata/<run> --seconds 600 --max-mib 250
```
Expected synthetic means (pre-fee / post-fee): null −8973.8 / −25563.2; controlled −7381.8 / −23971.2;
delay −1845.8 / −5311.2; faults −4430.2 / −14475.8; no-trade 0 / 0.

## Historical artifact inventory (gitignored; original development VM)
Original handoff checksums, not revalidated during consolidation. Not in Git.
Regenerate synthetic/loopback outputs with the commands above. Real-data raw capture,
M3/C3 cross-VM outputs and DB inputs cannot be regenerated identically (wall-clock/network dependent).

| Path | sha256 | Bytes | What |
|---|---|---|---|
| `artifacts/realdata/20261010T175802Z-btcusd/raw.jsonl` | `835e07e9029232fac005665f5f71a9fa3a1cd691054f9dfffd96cb589d1a6ebc` | 5796202 | raw Coinbase capture (kept private) |
| `…/20261010T175802Z-btcusd/manifest.json` | `b2c6247e396efed71b97b9ff3d4275fdc96423e71eb42af8a92fb2e821cfb284` | 1149 | capture manifest (sanitized copy in `docs/realdata-results/`) |
| `…/replay-audit/normalized.jsonl` | `285db8b62f4379f1a0f8e97787e556918cb6aef57eddfa88f5d2206af6cfa62c` | 2108241 | normalized top-of-book events |
| `…/replay-audit/report.json` | `ec2cda5f1e553da81f3e262c53d7b01a1a3582716b41da43d402a59772345631` | 5888 | hypothetical execution report (= `docs/realdata-results/report.json`) |
| `…/replay-audit/ingestion.json` | `5b144961a0c5f84cca8a951c7e84df3e2f6a04848a9391fa6ffd2036b898b4e5` | 725 | ingestion stats |
| `artifacts/quant/20261010T165552Z-matrix.json` | `4d6b67d3e8017b9d89ed34c2bd711592468f2800aeb6a18a7c54f795ad80492e` | 165894 | synthetic matrix (paper source) |
| `artifacts/c3/c3-20261010T171636Z/remote-normal/report.json` | `477f55ac9b6e69e8a63b1545c93fe943a9eab8478f408802a25b45e4aa97c29e` | 1108 | C3 cross-VM normal run |
| `artifacts/c3/c3-20261010T171636Z/remote-dropack/report.json` | `f7db543af31cf8a3484353309c293daf4a6865a39390b9af44755eb1273a2b15` | 1247 | C3 cross-VM dropped-ack run |
| `artifacts/m3/c3-20261010T171636Z/fills-normal.jsonl` | `83c449104b120ac9002c64585a164360df3a9dc05a15fb6c1bf3f41ad80e691e` | 462385 | C3 venue fill log (stored under `m3/` by `scripts/remote_venue.sh`) |
| `artifacts/m3/c3-20261010T171636Z/fills-dropack.jsonl` | `9f4a194cb66fa9ced78a4f049e69067823e7139f330942b06f047b5997abe66f` | 164553 | C3 venue fill log |
| `artifacts/tiger/input-manifest.sha256` | `08622f903324d8892e289e8dd7d30743c3b9c4f68e5bb7c4785b49430ed4ff03` | 1572 | checksums of the 3 runs imported to Tiger Data |
| `artifacts/backup/full-history.bundle` | `461f61e411a74a53406e0d5e48909d4bffed131be6ddd5a8e1b87e458489748b` | 59357 | backup of unsanitized history — **never publish** |
| `artifacts/submission/QuoteGate-paper.pdf` | `25688ef93d61824e3d7c3c49026f955a185cbde1a50cd160b4cce38ab8925061` | 211234 | built paper (2 pages) |
| `artifacts/demo/transcript.txt` | `895e153d5e9144b5b7a70e4fe82a4c81113c55bf525e3f941e202ea7b08cda0d` | 1645 | demo transcript (copy in `docs/demo-transcript.txt`) |

Private configuration (credentials, hosts, DB identifier) lives only in the gitignored
`docs/private/` on the development VM and is intentionally not described further here.

## Approved vs blocked
Approved (within existing human approvals):
- Local edits, tests, simulations, paper builds, demo runs; sanitized pushes to feature branches;
  opening/updating PRs with green CI.
- Read-only access to the public Coinbase feed for bounded captures (600 s / 250 MiB) and offline replay.
- Historical Tiger Data approvals covered the original target and Phase 1 runs only. This
  consolidation authorizes no database writes and performs no database access.
- Consolidation only: no feature expansion or new Hermes tasks. Future delegated tasks require
  the procedure in `AGENTS.md` and an isolated workspace.

Blocked (needs explicit human approval):
- Merging any other PR. The explicit consolidation authorization covers #4, #5, #6 (now merged)
  and the documentation-only cleanup after its gates; earlier #4/#5 holds were superseded.
- Importing real-data captures (or any new dataset) into Tiger Data; any schema expansion,
  destructive SQL, privilege change, or new cloud resource.
- New remote deployments, SSH/firewall/Tailscale changes, changes to Hermes or model services.
- Publishing `local/*` branches or `artifacts/backup/*`; history rewrites; force pushes.
- Devpost submission, public posting, visibility changes.
- New strategies, parameter retuning, second execution venue.

## Remaining ready tasks (not started; feature expansion stopped)
| ID | Task | Allowed files | Acceptance |
|---|---|---|---|
| N1 | Markdown renderer for `docs/realdata-results/report.json` (pure `render(report: dict) -> str`, column order as the table in docs/realdata.md) | `quote_gate/realdata_report.py`, `tests/test_realdata_report.py` | renders the committed report into exactly that table; stdlib only |
| N2 | Regression tests for `ws_client` handshake header parsing (case-insensitive headers, missing Accept) | `tests/test_ws_handshake_extra.py` | fail on a mutant comparing header names case-sensitively |

N3 (`scripts/demo.sh --quick`) is merged in PR #6 and verified; it is no longer a ready task.

## Known limitations (keep stating them)
Synthetic planted model; one 10-minute real-data sample with hypothetical fills/fees; single
coordinator; in-memory venue order store (dedup not restart-safe); cross-VM runs on one LAN,
possibly one physical host; results agent-verified only.
