# Handoff: current consolidation and historical snapshot

Read `AGENTS.md` first (rules, code map, invariants). Current status below supersedes the historical snapshot. Commands and artifact checksums
remain available for reproduction; the blocked-action list remains in force.
All results are agent-verified only; nothing is human verified.

## Current consolidation state (2026-10-10)
- Main includes PRs #1–#3 and PR #4 (H4), merged at `3095818` after verifying head `24b1c56`.
  H4 is complete and merged: 10/10 accepted tests; historical review recorded 6/6 mutants killed
  in [delegation-log.md](delegation-log.md). Current suite: 205 tests across 19 test modules/files,
  verified with `python3 -m unittest discover -s tests -t .` and the CI collection assertion.
- PR #5 (this handoff) and PR #6 (quick demo) are being consolidated under explicit human
  authorization; merges require green CI on exact heads and inspection of main's merge CI.
- Codex is the sole implementation agent. No delegated task is active. Current work is this
  local consolidation; no new features are being started.
- For future delegated work, preserve the reviewed procedure: allowed-path check, scratch apply,
  independent tests, mutation check, and review records. Do not launch Hermes in this checkout.

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

## Local artifacts (gitignored; on the original development VM only)
Not in Git. Regenerate synthetic/loopback outputs with the commands above. Real-data raw capture,
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
- Merging any PR outside the explicitly authorized #4, #5, #6 and documentation-only cleanup.
  Earlier human-merge holds on #4/#5 were superseded by the consolidation authorization.
- Importing real-data captures (or any new dataset) into Tiger Data; any schema expansion,
  destructive SQL, privilege change, or new cloud resource.
- New remote deployments, SSH/firewall/Tailscale changes, changes to Hermes or model services.
- Publishing `local/*` branches or `artifacts/backup/*`; history rewrites; force pushes.
- Devpost submission, public posting, visibility changes.
- New strategies, parameter retuning, second execution venue.

## Ready tasks (contract-first; suitable for a delegated agent)
| ID | Task | Allowed files | Acceptance |
|---|---|---|---|
| N1 | Markdown renderer for `docs/realdata-results/report.json` (pure `render(report: dict) -> str`, column order as the table in docs/realdata.md) | `quote_gate/realdata_report.py`, `tests/test_realdata_report.py` | renders the committed report into exactly that table; stdlib only |
| N2 | Regression tests for `ws_client` handshake header parsing (case-insensitive headers, missing Accept) | `tests/test_ws_handshake_extra.py` | fail on a mutant comparing header names case-sensitively |
| N3 | `scripts/demo.sh --quick` skipping the quant matrix | `scripts/demo.sh` | transcript sections 1–3 unchanged |

## Known limitations (keep stating them)
Synthetic planted model; one 10-minute real-data sample with hypothetical fills/fees; single
coordinator; in-memory venue order store (dedup not restart-safe); cross-VM runs on one LAN,
possibly one physical host; results agent-verified only.
