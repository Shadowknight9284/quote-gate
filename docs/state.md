# State

Verification levels: **planned** · **implemented** · **tested locally** · **tested across VMs** ·
**benchmarked** · **human verified**. Each row lists the highest level reached. "Human verified" means
a person independently re-ran it; nothing has reached that level yet.

## Features
| Feature | Level | Where |
|---|---|---|
| Quote validation, per-venue epochs, sequence ordering, local-receipt freshness | tested locally; tested across VMs | `book.py`, `tests/test_book_router.py` |
| Best-price routing with tie-break and no-route reasons | tested locally | `router.py` |
| NDJSON protocol, venue simulator, aggregator reconnect/backoff, client port | tested locally; tested across VMs; benchmarked | `protocol.py`, `venue.py`, `aggregator.py` |
| Feed ping/pong RTT, event log, freshness monitor | tested locally; benchmarked | `aggregator.py` |
| Route-lookup benchmark harness + report | benchmarked (loopback and M3) | `bench.py`, `report.py` |
| Quant signal, target-position policy, FIFO ledger with idempotent fills | tested locally | `quant.py`, `ledger.py` |
| Deterministic backtest + fixed 5-cell matrix | tested locally (results in paper) | `backtest.py`, `experiments.py` |
| Strategy-known vs evaluator-authoritative accounting | tested locally | `backtest.py` |
| Networked order path (order/exec/status/release), bounded idempotent store | tested locally (loopback) | `venue.py`, `execution.py`, `live.py` |
| UNKNOWN on timeout/disconnect, status reconciliation, lost-record handling | tested locally (loopback) | `live.py`, `tests/test_live_local.py` |
| Order path across VMs | planned (not approved) | — |
| Second execution venue in live runner | planned | `live.py` (`exec_venues`) |
| Tiger Data importer (plan / verify-local) | tested locally without a database | `importer.py`, `normalize.py` |
| Tiger Data schema init / import | implemented, never run (no credentials; 2 safeguards missing) | `db/`, `importer.py` |
| Two-page paper | implemented (compiles, 2 pages) | `paper/quotegate.tex` |

## Claim → evidence
Published revisions are on `publish/m1-m4`. Raw artifacts are gitignored and regenerate from the
commands shown; M3 artifacts exist only on the developer machine.

| Claim | Revision / run | Command / artifact | Level | Limitation |
|---|---|---|---|---|
| Rule tests: fixture, staleness boundary, reconnect, dup/old sequence | `4b0ec45` | `python3 -m unittest tests.test_book_router` | tested locally | injected clock, not wall time |
| Venue B on a second VM: wire == generator, freeze → stale, restart → new epoch (12/12 checks) | runtime of published `475d900`; run m3-20261010T154645 | `scripts/m3_check.py` (needs private env) | tested across VMs | same LAN, possibly same physical host; market-data path only |
| Feed RTT loopback ≈ 0.12 ms p50, cross-VM ≈ 0.57 ms p50 (n=330 each) | M3 runs | `docs/benchmarks.md` | benchmarked | summary only; p99 not meaningful; app-level RTT, not quote age |
| Route lookups unaffected by venue placement (~150 µs p50) | M3 runs | `quote_gate.bench` | benchmarked | single-host client→aggregator RTT; never touches a venue |
| 0 freshness violations over 60 000 routed replies under faults | M4 fault run | `scripts/m4_faults.sh` | benchmarked (loopback) | internal-consistency observation, not independent proof |
| Throughput plateau ≈ 45k route lookups/s | M4 sweep | `quote_gate.bench` | benchmarked (1 trial/rate) | client and aggregator on one VM; bottleneck not isolated |
| Planted signal detectable: hit rate .633 vs .502 null | `d4af524`; matrix 20261010T165552Z | `python3 -m quote_gate.experiments` | tested locally | synthetic model with planted mechanism |
| Strategy loses in every cell; paired controlled−null +1592±107 on 30/30 seeds | same | same; `paper/fig_paired.dat` | tested locally | frozen parameters, 30 seeds, this model only |
| Live loopback: 1217/1217 orders resolved by exec; ledger == venue fill log; exec RTT p50 ≈ 53 µs | `4b0ec45`, seed 101, 2000 steps | `python3 -m quote_gate.live --steps 2000` | tested locally | venue and aggregator in one event loop; RTT not comparable to cross-host |
| Dropped ack / disconnect after exec / venue restart behave per contract O1 | `4b0ec45` | `python3 -m unittest tests.test_live_local` | tested locally | loopback; fault hooks are injected |

## Known gaps
- Order path not run across processes on different VMs (not approved).
- Coordinator restart loses all strategy/ledger/pending-order state (in memory).
- Venue order store is in memory; dedup is not restart-safe.
- Tiger Data: no credentials; conflict detection and pre-existing-table validation not implemented.
- Pinger behaviour under socket write errors unverified.
