# QuoteGate

QuoteGate turns observable synthetic market information into bounded trading decisions and
measures how execution costs, latency, and venue failures affect those decisions and their outcomes.

HackRU 2026 prototype. Synthetic data only: no real money, orders, brokerage, or market data.
Nothing here claims real-market alpha, production readiness, or exchange-grade latency.

## Branches
- **`publish/m1-m4`** — current published development branch (sanitized history).
  PR: https://github.com/Shadowknight9284/quote-gate/pull/1 (open, not merged).
- **`main`** — still the starter commit until PR #1 is merged. Do not develop on it.

```
git clone https://github.com/Shadowknight9284/quote-gate.git
cd quote-gate && git switch publish/m1-m4
```

## Status (details and evidence: [docs/state.md](docs/state.md))
| Area | Status |
|---|---|
| Market-data path: venues → persistent TCP feeds → aggregator (sessions, sequence, freshness) → route lookup | tested locally; tested across VMs (M3); benchmarked |
| Quant: imbalance signal, target-position policy, FIFO ledger, deterministic backtest, fixed 5-cell matrix | tested locally; results in the paper |
| Networked order path (order/exec/status on the feed connection, UNKNOWN + reconciliation) | tested locally (loopback only) |
| Two-page paper | [paper/quotegate.tex](paper/quotegate.tex) (results from the deterministic backtest) |
| Networked order path across VMs (C3: normal + dropped-ack) | tested across VMs (one run each) |
| Tiger Data post-run analytics | optional; Phase 1 (3 runs) imported and query-validated — see [docs/analytics-example.md](docs/analytics-example.md) |
| Human verification | none yet — results are agent-reported |

## Quick start (Python 3.12, standard library only)
Run from the repository root. No virtualenv, database, or private configuration is needed.
```
python3 -m unittest discover -s tests -t .          # all tests (~3 s); expect "OK"
python3 -m quote_gate.experiments --out /tmp/qg     # fixed quant matrix, 30 seeds (~6 s)
python3 -m quote_gate.live --steps 2000             # networked loopback slice (~5 s), JSON report
```
Market-data demo (three processes on 127.0.0.1): see [docs/demo.md](docs/demo.md).
Paper: `cd paper && latexmk -pdf quotegate.tex` (needs a TeX Live install with pgfplots).

## Optional: real market data (Coinbase BTC-USD, read-only)
Not needed for the quick start. Public `level2_batch` feed, no account or key; execution is
hypothetical. See [docs/realdata.md](docs/realdata.md).
```
python3 -m quote_gate.coinbase_capture --out artifacts/realdata/run1 --seconds 600 --max-mib 250
python3 -m quote_gate.coinbase_replay --capture artifacts/realdata/run1
```

## For coding agents
Start with [AGENTS.md](AGENTS.md) (rules, commands, code map, invariants) and
[docs/handoff.md](docs/handoff.md) (in-flight and ready tasks with contracts).

## Documentation
- [docs/architecture.md](docs/architecture.md) — runtime vs experiment vs analytics vs dev workflow; flows; limits
- [docs/state.md](docs/state.md) — feature status by verification level; claim → evidence table
- [docs/protocol.md](docs/protocol.md) — wire protocol, bounds, order/exec/status contract (O1)
- [docs/quant.md](docs/quant.md) — information set, model, policy, execution, accounting, results
- [docs/task-board.md](docs/task-board.md) — tasks, file ownership, Hermes tasks
- [docs/benchmarks.md](docs/benchmarks.md) — measured latencies and exact field definitions
- [docs/tiger-data.md](docs/tiger-data.md) — optional analytics: offline vs credentialed setup
- [docs/analytics-example.md](docs/analytics-example.md) — sanitized Phase 1 import and query results
- [docs/submission.md](docs/submission.md) — submission draft and system-design summary (not submitted)
- [docs/security.md](docs/security.md), [docs/test-plan.md](docs/test-plan.md),
  [docs/delegation-log.md](docs/delegation-log.md), [docs/contracts/](docs/contracts/)

## Repository hygiene
Private host details and credentials live only in the gitignored `docs/private/`.
`artifacts/` (raw benchmark/experiment outputs) is gitignored; regenerate it with the commands above.
