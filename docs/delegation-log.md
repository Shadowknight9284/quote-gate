# Delegation log

## T1 synthetic generator — accepted
- Contract: `docs/contracts/T1-synthetic.md`. Allowed files: `quote_gate/synthetic.py`, `tests/test_synthetic.py`.
- Remote workspace: isolated per-task dir on the Hermes VM (not the Hermes install, not this repo); baseline commit `66d4aaf` containing only contract + empty package files.
- Invocation: `tools/hermes_task.sh run` — `hermes chat -Q --ignore-rules --max-turns 20 --run-budget 120|150`, external `timeout` = budget + 60 s. No `--yolo`.
- Limitation: `--in` is not a sandbox; Hermes runs as the general user account on that VM.
- Attempt 0: both files returned (new files present in patch, only allowed paths). Review: `iter_quotes` was a generator function (lazy validation, failing its own test); `isinstance` accepted bool; duplicate import; `sys.path` hack in tests.
- Repair 1 (of max 2): fixed eager validation, bool rejection, sys.path hack, added prefix/bool tests. Duplicate `typing` import remained; removed locally by Claude at integration along with trailing whitespace.
- Local verification: Claude-authored `tests/acceptance/test_synthetic_acceptance.py` (reference draw-order re-implementation) 6/6 pass; full suite 36/36 pass.

## T2 report formatter — rejected after 2 repairs; implemented locally by Claude
- Contract: `docs/contracts/T2-report.md`. Allowed files: `quote_gate/report.py`, `tests/test_report.py`.
  Baseline `f1e502d`. Budget 180 s, max 25 turns, external timeout 240 s.
- Attempt 0: latency cells lacked " / " spacing; missing metric rendered "n/a/n/a/n/a"; gen-lag cell lacked `*`. Its own 7 tests passed (did not cover these).
- Repair 1: fixed spacing and `*`, but regressed the fixed header; missing-metric not fixed; whitespace not removed despite report claiming so.
- Repair 2: header restored; missing-metric still wrong; 3 of its own tests failing while report said requirements satisfied.
- Outcome: patch not applied. Claude wrote `quote_gate/report.py`; verified by `tests/acceptance/test_report_acceptance.py` (5/5).
- Lesson: Hermes reports are not evidence; exact-format tasks need the acceptance test shipped with the task.

## A1 audit (Hermes second pass) — No confirmed defects from this audit; audit patch rejected.
This is not a clean bill of health and not a security audit result.
- Baseline `720b0e0`; scope: validation/sequence, freshness boundary, reconnect invalidation,
  ping bookkeeping. Allowed output: `tests/audit/` only (respected). Budget 300 s, 30 turns.
- Returned 4 findings + 12 tests (all passing at baseline; none demonstrates a defect).
  1. `_check` ordering — rejected: validation precedes dup/order checks by design; no reproduction.
  2. Freshness equality boundary — rejected: Hermes's own test asserts the documented contract and passes.
  3. Concurrent reconnects — unverified by A1. Claude follow-up: `tests/test_reconnect_ping.py`
     (8 rapid reconnects: epochs 1..9, no leaked tasks, second subscriber refused) passes.
  4. Ping cleanup — unverified by A1. Claude follow-up: single outstanding ping, timeouts counted,
     mismatched pong not recorded as RTT; passes. Remaining unverified: behaviour under write errors.
- Reported "Ran 35 tests … OK" did not match any reproducible run (baseline + audit = 71).

## T3 normalizer — rejected after 2 repairs; implemented locally by Claude
- Contract `docs/contracts/T3-normalize.md`; baseline `00973ad`; budget 240 s, 30 turns.
- Attempt 0: µs→ns multiplied by 1e6; bool accepted; float in ns dicts accepted; invalid trial
  accepted; tests read files and patched sys.path (5 of its own tests errored).
- Repair 1: Claude acceptance file supplied (unchanged by Hermes, verified byte-for-byte). Still
  2/11 acceptance failures; tests still read files although the report said they did not.
- Repair 2: 1/11 acceptance failure remained (whole-number µs values not converted); report
  attributed it to "test expectations", but the test asserts the contract formula.
- Outcome: patch not applied. Claude wrote `quote_gate/normalize.py`; 11/11 acceptance tests pass.

## H1 ledger regression tests (Hermes) — partially accepted: 13 tests retained, one rejected, two out-of-scope files discarded; 0 repairs
- Contract: Accounting section of `docs/contracts/Q1-quant.md`; allowed file `tests/test_ledger_hermes.py`.
  Baseline `1267762`; budget 240 s, 25 turns.
- Scope violation: also created `debug_ledger.py`, `debug_specific.py` (not applied).
- 1 test failed: expected `Fill(...)` construction to raise; contract validates in `Ledger.apply`
  (covered by `tests/test_quant.py`). Test asserts non-contract behaviour → dropped. Report did not
  mention the failure.
- Remaining 13 tests killed 5/5 injected ledger mutants (fee sign, duplicate check, short realized
  sign, FIFO→LIFO, limit off-by-one). Accepted with a provenance docstring.
- Note: writing files outside the allowed path shows the task workspace does not enforce access
  isolation; the test patch itself does not establish isolation. Independent review and mutation
  checks remain required.
