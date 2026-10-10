# QuoteGate — Lucera Trading Systems Challenge submission evidence

**Core claim.** An end-to-end simulated trading workflow that excludes stale quotes, bounds
positions, and reconciles venue fills with accounting.

Everything is simulated: no real orders, no funds, no exchange account or API key. External data is
used only in the optional, separately labelled Coinbase replay ("real market observations with
hypothetical execution"). Provenance labels used below: **human-run** = executed by a team member
who reported the result; **agent-run** = executed by the AI coding agent; **recorded** = saved output,
not a live run.

## 1. Official minimum-requirement mapping
| # | Requirement (track document) | Evidence |
|---|---|---|
| 1 | Trading / financial-market infrastructure use case | Market-data aggregation with freshness checks, smart-order routing, simulated execution with reconciliation (§2) |
| 2 | Working end-to-end path; ≥ 2 communicating components | Venue simulator ↔ aggregator over persistent TCP (quotes, pings, orders, execution reports, status queries); `scripts/demo.sh`; human-run demo transcript `docs/demo-transcript-human-e9a71db.txt` |
| 3 | At least one correctness invariant with automated tests | I1 and I2 (§3) with named tests; suite: `python3 -m unittest discover -s tests -t .` (205 tests; human-run pass at `e9a71db`; CI on every push/PR) |
| 4 | Reproducible quantitative evidence | Execution round-trip latency, 5 runs with disclosed environment (§4) |
| 5 | Submitted through HackRU Devpost with repository | **Pending — to be done by the team** (not done by the agent) |
| 6 | Setup/run instructions, architecture, test instructions/results, performance methodology/results | §2, §3, §4 of this file; `README.md`; `docs/architecture.md` |
| 7 | Disclosure of external APIs, frameworks, libraries, datasets, prebuilt components; what was built during the hackathon | §6, §7 |

Published evidence files and their SHA-256 hashes: [`docs/evidence/README.md`](evidence/README.md).

## 2. Architecture and runnable demo
```
venue simulator(s) ──one persistent TCP connection each──► aggregator process
  quote loop ──── quotes ───────────────────────────────►  feed reader (one per venue) → Book
  control reader ◄─ ping / order / order_status / release ─  freshness, sequence, session checks
  matching + bounded order store ── exec / status ───────►  strategy runner → router → ledger
```
- **Venue simulator** (`quote_gate/venue.py`): streams quotes; validates and matches immediate-or-cancel
  orders against its own current quote; records each outcome in a bounded, idempotent order store
  before acknowledging.
- **Aggregator** (`quote_gate/aggregator.py`, `book.py`, `router.py`): one reader per venue
  connection; rejects old/duplicate sequences; marks a venue stale when its last accepted quote is
  older than the configured threshold (100 ms in the market-data demo, 50 ms in the order-path
  runner; local receipt time, not source age); routes only to fresh venues.
- **Strategy runner and ledger** (`quote_gate/live.py`, `quant.py`, `ledger.py`): frozen
  target-position policy (|position| ≤ 5); a missing acknowledgement makes the order UNKNOWN,
  halts new orders and triggers status queries — never a resend.
- The distributed boundary is the venue/aggregator split: market data and order outcomes are
  owned by the venue and must survive delay, loss and disconnects on the wire.

Setup: Python 3.12 standard library only (no install). From the repository root:
```
python3 -m unittest discover -s tests -t .     # tests (expect "OK")
scripts/demo.sh                                # full demo + transcript (stale filtering, orders, experiment)
scripts/demo.sh --quick                        # same, without the synthetic experiment
python3 -m quote_gate.live --steps 2000        # networked order path alone (JSON report)
```
Recorded transcripts: `docs/demo-transcript-human-e9a71db.txt` (human-run, revision `e9a71db`,
2026-10-10T21:06Z, 205 tests) and `docs/demo-transcript.txt` (agent-run, revision `1032c4d`,
2026-10-10T17:52Z, 154 tests at that revision; kept unchanged for history).

## 3. Invariants and the tests that try to break them
**I1 — A stale, invalid or disconnected venue is never routable, and a stale reference never
triggers an order.**
- `tests/test_book_router.py`: `test_a_stale_buy_selects_b`, `test_a_fresh_just_before_threshold`
  (exact threshold boundary), `test_both_stale_no_route`, `test_both_disconnected_no_route`,
  `test_reconnect_resets_sequence_and_requires_fresh_quote`, `test_old_epoch_message_ignored`.
- `tests/test_integration_local.py`: `test_paused_feed_goes_stale_and_is_excluded` (real sockets).
- `tests/test_quant.py`: `test_reference_signal_requires_fresh`, `test_stale_reference_blocks_orders`.

**I2 — Every venue fill is booked exactly once, |position| ≤ 5, and an unknown outcome is never
resent or guessed.**
- `tests/test_live_local.py`: `test_same_id_same_payload_executes_once`,
  `test_same_id_different_payload_conflict`, `test_duplicate_exec_does_not_double_book`,
  `test_wrong_session_id_instance_not_applied`, `test_dropped_ack_after_fill_recovered_by_status`,
  `test_disconnect_after_execution_before_ack`, `test_venue_restart_with_lost_record_stays_unresolved`,
  `test_reuse_of_released_and_evicted_id_executes_again` (documents the deduplication limit).
- `tests/test_quant.py`: `test_ledger_matches_venue_record_after_reconciliation`, `test_limit_and_invalid`.
- `tests/test_ledger_hermes.py`: `test_position_limit_breach_raises_error`,
  `test_duplicate_fill_id_different_payload_raises_error`.

Demonstrated in the human-run demo: venue A excluded as `stale` while frozen; A fresh again after
resuming; B `disconnected` after being killed; 1,217 orders sent and filled; venue fills = ledger
fills = 1,217 with 0 unbooked, 0 extra, 0 mismatched; maximum |position| 5, final −5.

## 4. Performance: execution round-trip latency (agent-run, 2026-10-10T21:24Z)
Complete per-run reports and a sanitized environment summary:
[`docs/evidence/perf-exec-rtt-20261010T212410Z/`](evidence/perf-exec-rtt-20261010T212410Z/environment.md).

**Environment.**
| Item | Value |
|---|---|
| Machine | KVM virtual machine (guest view); host CPU model not exposed ("QEMU Virtual CPU version 2.5+") |
| vCPUs / memory | 16 vCPUs, 33,647,370,240 bytes (≈ 31.3 GiB) — **configured allocations seen by the guest, not guaranteed dedicated resources**; host contention was not measured |
| OS / kernel | Ubuntu 24.04.3 LTS, Linux 6.8.0-142-generic x86_64 |
| Runtime | Python 3.12.3 (standard library only) |
| Process limits | cgroup `cpu.max` = max, `memory.max` = max (no container limit); no CPU pinning; frequency governor not exposed |
| Load | 1-min load average 0.00 before and during the runs |
| Revision | `a8042caa4c9829f60b0796ddc289e7167a5d6965`, clean working tree |

**Workload.** `python3 -m quote_gate.live --steps 2000` (defaults seed 101, 500 quotes/s): one venue
and the aggregator/strategy in one asyncio event loop over loopback TCP; 2,000 synthetic market steps
(≈ 4 s) plus 1 s settle; frozen policy; at most one outstanding order, so orders are sequential.
Each run sent and filled 1,217 orders.

**Metric.** Order send → execution report, measured with `time.monotonic_ns()` on one host:
start immediately before the order is encoded and written; end after the matching execution
report is read, validated and applied to the ledger. Counted only for orders resolved directly by
an execution report (all 1,217 here). Percentiles are nearest-rank per run. **No warm-up
exclusion:** every sample is included. This is application-level round-trip time, not network
latency, and not throughput.

**Procedure.**
```
D=artifacts/perf/$(date -u +%Y%m%dT%H%M%SZ); mkdir -p "$D"
for i in 1 2 3 4 5; do python3 -m quote_gate.live --steps 2000 > "$D/run$i.json"; echo "run$i exit=$?"; done
```
Each run's report was checked: exit 0; sent = filled = 1,217; 0 timeouts; 0 unresolved; venue fills =
ledger fills; 0 mismatched; maximum |position| ≤ 5.

**Results (each run separately; percentiles are not pooled).**
| Run | n | p50 (ns) | p99 (ns) | max (ns) |
|---|---|---|---|---|
| 1 | 1217 | 53,441 | 138,662 | 483,086 |
| 2 | 1217 | 52,730 | 135,337 | 619,656 |
| 3 | 1217 | 53,381 | 138,583 | 531,868 |
| 4 | 1217 | 52,760 | 133,854 | 238,211 |
| 5 | 1217 | 53,040 | 142,701 | 607,091 |

Run-to-run variation: p50 52,730–53,441 ns; p99 133,854–142,701 ns; max 238,211–619,656 ns.
Earlier recorded runs of the same command: p50 53,030 / p99 123,214 ns (agent-run, `1032c4d`);
p50 53,200 / p99 120,088 ns (`e9a71db`, 18:59Z, operator not recorded); p50 53,030 / p99 144,003 ns
(human-run, `e9a71db`, 21:06Z).

**Interpretation.** The median is stable at ≈ 53 µs across eight recorded runs; the tail varies
(p99 120–144 µs; max up to ≈ 0.6 ms), consistent with scheduling effects on a shared virtual machine.
With n = 1,217, p99 is about the 13th-largest sample, so tail estimates are noisy. Both endpoints
share one process and one event loop, so this does not include inter-process or network costs.
A separate-process loopback run measured p50 ≈ 132 µs, and a cross-VM run p50 ≈ 649 µs (agent-run,
one run each; `docs/benchmarks.md`) — those configurations change more than placement and are not
directly comparable.

## 5. Evidence and provenance
| Evidence | Where | Provenance |
|---|---|---|
| Tests (205) | `tests/`; CI `.github/workflows/tests.yml` | human-run at `e9a71db`; CI on every push/PR |
| Demo: stale filtering, 1,217-fill reconciliation | `docs/demo-transcript-human-e9a71db.txt` | human-run, recorded |
| Historical demo transcript | `docs/demo-transcript.txt` | agent-run at `1032c4d`, recorded |
| Synthetic experiment matrix reproduced exactly | `docs/evidence/synthetic-matrix/` (source and reproduced matrices + comparison command); `docs/quant.md`; paper Table 1 / Figure 1 (`paper/fig_paired.dat`) | source matrix agent-run; reproduction and original comparison human-run (no saved comparison transcript); comparison re-run on the public copies by the agent: all substantive keys match |
| Performance (§4) | `docs/evidence/perf-exec-rtt-20261010T212410Z/` (5 complete run reports, sanitized environment) | agent-run |
| Cross-VM execution (C3) | `docs/benchmarks.md` | agent-run, one run per scenario; aggregate recovery counters only (per-order lifecycle not retained) |
| Coinbase replay | `docs/realdata.md`, `docs/realdata-results/` | agent-run; raw data not redistributed |
| Paper | `paper/quotegate.tex` | built locally; PDF submitted separately |

## 6. External software, data, services and AI tools
- **Runtime:** Python 3.12.3 standard library only (`asyncio`, `ssl`, `json`, `decimal`, …).
- **Optional analytics:** `psycopg[binary]==3.3.6` (project-local virtualenv); Tiger Cloud service
  running TimescaleDB 2.30.2 on PostgreSQL 18.6. Not needed for the demo or tests.
- **CI:** GitHub Actions on `ubuntu-24.04`, `actions/checkout` (pinned SHA, v4) and
  `actions/setup-python` (pinned SHA, v5), Python 3.12.
- **Paper:** TeX Live 2023 (pdfTeX 1.40.25), PGF 3.1.10, pgfplots 1.18.1.
- **External data/API:** Coinbase Exchange public WebSocket feed `wss://ws-feed.exchange.coinbase.com`
  (`level2_batch`, `heartbeat`, BTC-USD) and public product metadata
  `https://api.exchange.coinbase.com/products/BTC-USD`; read-only, no account. One 600 s capture;
  raw data not redistributed. All other data is synthetic and seeded.
- **Prebuilt trading components:** none.
- **AI coding tools (development only; nothing AI runs in the application):** Claude Code with
  Claude Opus 5.5 (design, implementation, review); Codex (later audit and consolidation; version
  not recorded); Hermes Agent v0.21.3 coding agent backed by a locally hosted model (model name not
  recorded). Delegated Hermes output was accepted only after independent tests
  (`docs/delegation-log.md`).

## 7. What was built during the event
HackRU Fall 2026 runs October 10–11, 2026 (track document).

The team reports an event start of 10:00 EDT on October 10, 2026
(14:00 UTC). The repository’s first commit is timestamped
15:21:46 UTC that day, after that reported start. Commit timestamps
alone do not establish when all underlying work was created;
event eligibility remains subject to the official HackRU rules.

## 8. Known limitations and next bottleneck
- The synthetic experiment uses a planted signal; results are not evidence of real-market
  profitability. The Coinbase replay's execution is hypothetical (one 10-minute sample).
- One aggregator process holds all strategy and ledger state (single coordinator); venue order
  records are in memory, so deduplication does not survive a venue restart.
- Cross-VM runs were on one LAN, possibly on one physical host; the historical C3 per-order
  UNKNOWN/status lifecycle was not retained.
- Measured route-lookup capacity plateaued near 45,000 lookups/s with client and aggregator on one
  VM (one trial per rate; bottleneck not isolated; `docs/benchmarks.md`).
- **Next-bottleneck hypothesis (not measured):** the single aggregator event loop. A CI-found defect
  showed one flooding client could stall feed processing (fixed by yielding per request); with more
  venues, clients or strategies, CPU on that one loop is expected to saturate first. The likely next
  design step is separating feed handling from client and strategy work.
