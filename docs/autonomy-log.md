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
