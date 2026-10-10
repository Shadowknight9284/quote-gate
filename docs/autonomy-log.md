# Autonomy log

Entries are agent-recorded under the standing autonomy contract; nothing here is human verified.

## 2026-10-10T17:32Z — CI added (`bdb32e2`)
- Added `.github/workflows/tests.yml` (stdlib only, pinned actions, collection check, no secrets).
- CI on `bdb32e2`: push run **success** (10 s); pull_request run on the same SHA **failed**
  (`test_slow_consumer_does_not_stall_feed`) and then **hung** until the 10-min job timeout.

## 2026-10-10T18:00Z — two confirmed defects fixed
- D1 feed starvation: `Aggregator._client` never yielded while a client had buffered requests
  (`read_frame`/`drain` return without yielding), so a flooding client stalled the event loop
  (33 ms locally, 412 ms on the CI runner). Fix: `await asyncio.sleep(0)` per request.
- D2 shutdown hang: `Aggregator.stop()` awaited `Server.wait_closed()`, which on Python 3.12 waits for
  all open client connections, which `stop()` never closed. Fix: track and abort client connections;
  bound the wait (2 s).
- Repro: `scratchpad repro.py` (flood 100k requests; stop with idle client). After fix: max stall
  1 ms; stop returns. Regression tests `tests/test_reconnect_ping.py::StopAndFairness` fail on the
  pre-fix aggregator (1 failure, 1 error) and pass after.
- Also closed two unclosed files in `tests/test_integration_local.py` (ResourceWarning).

## 2026-10-10T18:10Z — CI-exposed test race fixed (`9ffa86e` → next)
- CI on `9ffa86e`: pull_request run success; push run failed
  `test_disconnect_after_execution_before_ack` (`unbooked_venue_fills 1 != 0`): after reconciliation
  trading resumed and a new order's exec was still in flight at the snapshot. Product invariant
  (unbooked fills all belong to unresolved orders) held. Test now quiesces (stops decisions, waits
  until nothing is pending) before asserting exact agreement; the `== 0` assertion is kept and added
  to two more live tests.
- Stress: 6 concurrent suites x 3 rounds (18/18 OK); 4 suites pinned to one CPU x 2 rounds (8/8 OK).

## 2026-10-10T17:47Z — references, in-text citations, fallback demo
- Citations checked: Cartea et al. (Cambridge copyright page, ISBN), Cont et al. (HAL + RePEc,
  JFEC 12(1):47–88), Gould & Bonart (arXiv:1512.03492 page; journal details only secondary, so the
  arXiv version is cited). Record: `paper/REFERENCES.md`. In-text citations added; paper 2 pages,
  0 overfull boxes, 0 undefined citations.
- `scripts/demo.sh`: tests, market-data path with freeze/kill, networked loopback orders, quant
  matrix; transcript committed at `docs/demo-transcript.txt` (scanned: no private details).

## 2026-10-10T18:05Z — merge-gate check on `baaf363`
- CI: push run 3/3 attempts success, pull_request run 2/2 success (same SHA).
- Clean clone at `baaf363`, empty env: 154 tests OK; 15/15 files collected, 0 import errors;
  experiments 5 cells; live loopback 1217/1217, 0 mismatches; paper 2 pages.
- Tree scan: no private hosts, paths, DB identifiers or secrets (only placeholders in
  `db/tiger.env.example` and a fake password in a unit test). No artifacts/private files tracked.
- origin/main unchanged at `396f2d8` (no teammate changes to preserve).
- `docs/state.md` current-head line was stale (152 tests, "no CI"); corrected in the next commit,
  which must pass CI again before merge.

## 2026-10-10T18:12Z — merge, post-merge state, Coinbase adapter (feature branch)
- PR #1 merged (normal merge) at head `883562a` → merge commit `05b7f4f`; CI on main passed.
  Local: unsanitized history kept as `local/unsanitized-main` (no upstream) + backup branch + bundle;
  new `main` tracks `origin/main`. A local branch `pr-1` (sanitized `b15602e`) exists; left untouched.
- Branch `feat/coinbase-replay`: stdlib read-only WebSocket client (no dependency installed),
  pure L2 book (`l2book.py`), bounded capture, deterministic replay with hypothetical execution.
  `book.MAX_QUANTITY` raised to 1e15 for 1e-8 BTC units; synthetic matrix re-run: identical.
- Capture (authorized, public, read-only): 600 s, 9 956 messages, 0 reconnects. Replay twice:
  identical hashes. Results in `docs/realdata.md`; metadata/report in `docs/realdata-results/`.
- Tests: 179 (17 book fixtures, 8 replay fixtures with hand-traced exact values).
- Not done: Tiger import of this dataset (needs separate approval); Hermes not used (bounded value).

## 2026-10-10T18:20Z — submission materials (branch `docs/submission`)
- `docs/submission.md`: draft text + system-design summary (not submitted anywhere).
- Local only (gitignored): `artifacts/submission/` with QuoteGate-paper.pdf (2 pages), page PNGs,
  demo transcript.
- PR #2 (Coinbase real-data) open with CI green; not merged (merge authority covered PR #1 only).

## 2026-10-10T18:40Z — PR #2 audit (branch feat/coinbase-replay)
- Findings F1–F5 and WebSocket W1–W4 (see docs/realdata.md "Audit"); F1 and W1–W4 confirmed by
  tests that fail on the pre-audit code. Fixed in scope; strategy and lot size unchanged.
- Re-replay of capture 835e07e9… with audited code: twice, identical hashes (report ec2cda5f…,
  normalized 285db8b6… unchanged). Units/notional/fee identities reconcile exactly in all scenarios.
