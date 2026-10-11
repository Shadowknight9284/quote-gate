# QuoteGate

A simulated trading system that never guesses: it refuses stale prices, halts and asks when an
order's outcome is unknown, and reconciles every fill against the venue's own records.

HackRU Fall 2026 prototype (Lucera Trading Systems Challenge). Everything is simulated or
hypothetical: no real orders, money, or exchange account. No claim of real-market profit,
production readiness, or exchange-grade latency.

## Run it (Python 3.12, standard library only, nothing to install)
```
python3 -m unittest discover -s tests -t .   # 224 tests, expect "OK" (also run in CI on every push)
scripts/demo.sh                              # full demo + transcript: stale feeds, orders, reconciliation, experiment
python3 -m quote_gate.console                # browser console at http://127.0.0.1:8050
```
In the console, **Run guided demo** walks through three injected failures in about a minute and ends
with a scorecard. **Check fills** reconciles your own broker fill export against your bot's trade log (CSV);
sample files with planted problems are in [`examples/fills/`](examples/fills/README.md).

## What it does
```
venue simulators ──persistent TCP, NDJSON──► aggregator ──► strategy ──► router ──► venue (IOC order)
   quotes, execution reports, status            freshness,     imbalance   best fresh      │
                                                sequence,      signal,     venue           ▼
                                                epochs         |pos| ≤ 5               ledger ◄─ reconciliation
```
- **Freshness gating.** A venue is stale once its last accepted quote is ≥ 50 ms old (100 ms in the
  market-data demo); stale, crossed, out-of-order, or old-session quotes are never routed.
- **Explicit uncertainty.** A lost acknowledgement or disconnect makes an order UNKNOWN: trading halts and
  the venue is queried by client order ID. Orders are never resent. If the venue restarted and lost the
  record, the order stays unresolved instead of being guessed.
- **Exact accounting.** Integer ticks, FIFO P&L, duplicate fills are no-ops, the position limit is enforced
  in the ledger, and venue fills are reconciled against the ledger after every run.
- **Experiment.** A transparent order-book-imbalance strategy in a seeded synthetic market (5 scenarios ×
  30 seeds), plus a read-only replay of a recorded Coinbase BTC-USD order book with hypothetical fills.

## Results (provenance and files in [docs/evidence/](docs/evidence/README.md))
| Claim | Result | Provenance |
|---|---|---|
| Orders reconcile with the venue | 1,217 sent, 1,217 filled; 0 mismatched, 0 unbooked, 0 extra | human-run demo ([transcript](docs/demo-transcript-human-e9a71db.txt)) |
| Lost acks never resent | cross-VM, every 50th ack dropped: 21 UNKNOWN, all reconciled, none resent | agent-run, one run ([benchmarks](docs/benchmarks.md)) |
| Order → execution round trip | p50 ≈ 53 µs, p99 134–143 µs (one process, loopback, 5 runs) | agent-run |
| Signal is real but unprofitable | hit rate .633 vs .502 (null); +1,592 ± 107 ticks vs null on 30/30 seeds; every trading cell loses after spread and fees | agent-run; reproduced exactly by a team member |
| Real-data replay (one 10-min sample) | 227 of 1,798 hypothetical orders filled; −$0.02 pre-fee, −$94.67 with a 10 bp fee | agent-run ([realdata](docs/realdata.md)) |

The negative result is the finding: the signal's edge (~0.13 ticks per unit per step) cannot pay a
~3-tick round trip. See [docs/quant.md](docs/quant.md) and [paper/quotegate.tex](paper/quotegate.tex).

## Invariants and where they are tested
1. **A stale, invalid, or disconnected venue is never routable, and a stale reference never triggers an
   order** — `tests/test_book_router.py`, `tests/test_integration_local.py`, `tests/test_quant.py`.
2. **Every venue fill is booked exactly once, |position| ≤ 5, and an unknown outcome is never resent or
   guessed** — `tests/test_live_local.py`, `tests/test_quant.py`, `tests/test_ledger_hermes.py`.

The full mapping of claims to tests and evidence is in [docs/submission-lucera.md](docs/submission-lucera.md).

## Limits
Synthetic market with a planted signal; one short real-data sample. No partial fills, queue position, or
market impact. Single coordinator process; the venue's order store is in memory, so deduplication does
not survive a venue restart. Freshness is local receipt age, not source age. Cross-VM runs were on one
LAN, possibly one physical host.

## Where to look
| For | Read |
|---|---|
| Judge-facing summary and requirement mapping | [docs/submission-lucera.md](docs/submission-lucera.md) |
| Architecture and wire protocol | [docs/architecture.md](docs/architecture.md), [docs/protocol.md](docs/protocol.md) |
| Strategy, accounting, results | [docs/quant.md](docs/quant.md), [paper/quotegate.tex](paper/quotegate.tex), [paper/quotegate-formal.tex](paper/quotegate-formal.tex) |
| Latency methodology | [docs/benchmarks.md](docs/benchmarks.md) |
| Browser console and Check fills | [docs/console.md](docs/console.md) |
| Status by verification level | [docs/state.md](docs/state.md) |
| Contributors and coding agents | [AGENTS.md](AGENTS.md), [docs/handoff.md](docs/handoff.md) |

Built with Python's standard library (asyncio, unittest), plain HTML/CSS/JS for the console, GitHub
Actions, and LaTeX. Optional analytics use Tiger Cloud (TimescaleDB). AI coding tools (Claude Code,
Codex, Hermes) were used during development only and reviewed through tests; nothing AI runs in the app.
Private host details and credentials are kept out of the repository; `artifacts/` is regenerated by the
commands above.
