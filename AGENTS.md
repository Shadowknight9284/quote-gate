# AGENTS.md — entry point for coding agents

Read this first. Then `README.md` (humans), `docs/state.md` (what is verified, at what level),
`docs/handoff.md` (open tasks with contracts). Every claim in docs links to a command or artifact.

## What this is
QuoteGate: synthetic market-data system + transparent trading policy + simulated execution with
explicit failure/reconciliation semantics, optional Tiger Data analytics, and a read-only Coinbase
real-data replay. **No real money, no real orders, no exchange keys, ever.**

## Ground rules (non-negotiable)
- Python 3.12 **standard library only** for core code and tests. `.venv` + `requirements-db.txt` is
  only for the optional Tiger Data importer.
- Integer ticks / integer quantities everywhere. Decimal only at the real-data parsing boundary.
- Never commit: `artifacts/`, `docs/private/`, `.venv/`, raw market data, hostnames/IPs, usernames,
  key paths, DB identifiers, credentials. CI and reviewers scan for these.
- Never send real orders, never add exchange credentials, never connect to the database without
  a human's target-specific approval (see `docs/tiger-data.md`).
- Do not change frozen experiment parameters (`quote_gate/quant.py` `PolicyConfig()`,
  `quote_gate/experiments.py`) or tune on results. The paper numbers come from them.
- Branch from `main`, open a PR, keep CI green. Do not push to `main` directly; no force pushes.

## Verify everything (≈10 s, no network)
```
python3 -m unittest discover -s tests -t .        # must print OK; CI runs the same + a collection check
python3 -m quote_gate.experiments --out /tmp/qg   # must reproduce docs/quant.md table exactly
python3 -m quote_gate.live --steps 2000           # loopback orders; ledger == venue (mismatched 0)
scripts/demo.sh                                   # full demo + transcript
```
Expected synthetic results (pre-fee / post-fee mean): null −8973.8 / −25563.2; controlled
−7381.8 / −23971.2; delay −1845.8 / −5311.2; faults −4430.2 / −14475.8. Any change is a regression.

## Code map (`quote_gate/`)
| Module | Role | Contract |
|---|---|---|
| `book.py` | per-venue quote state: epochs, sequence, freshness | docs/state.md (M1) |
| `router.py` | best eligible venue, tie-break, no-route reasons | — |
| `protocol.py` | NDJSON framing, 4 KiB frames | docs/protocol.md |
| `venue.py` | venue simulator: quotes, orders, bounded idempotent store | docs/protocol.md (O1) |
| `aggregator.py` | feed readers (one per venue), client port, monitor, pings | docs/architecture.md |
| `quant.py`, `ledger.py` | signal, target policy, FIFO ledger | docs/contracts/Q1-quant.md |
| `market_sim.py`, `backtest.py`, `experiments.py` | deterministic synthetic experiment | Q1 |
| `execution.py`, `live.py` | shared IOC matching; networked runner, UNKNOWN/reconciliation | O1 |
| `bench.py`, `report.py`, `metrics.py` | load generator, Markdown report, bounded rings | docs/benchmarks.md |
| `importer.py`, `normalize.py` | optional Tiger Data import (safeguarded) | docs/tiger-data.md |
| `l2book.py`, `ws_client.py`, `coinbase_capture.py`, `coinbase_replay.py` | real-data capture/replay | docs/contracts/R1-coinbase.md |

## Invariants tests enforce (do not weaken)
- Old/duplicate sequences never roll state back; reconnect = new epoch, fresh snapshot required.
- Freshness is local receipt age, never source age. Heartbeats are not freshness.
- UNKNOWN order outcome ⇒ trading halts; only `order_status` queries; never resend.
- Ledger == venue fill records (strategy-known vs evaluator-authoritative reported separately).
- Same seed / same capture ⇒ byte-identical outputs.

## Delegated agents (Hermes)
Small tasks only, one at a time, explicit allowed files, isolated workspace
(`tools/hermes_task.sh`), max two repairs. Accept only after independent tests and mutation checks;
completion summaries are not evidence. History: `docs/delegation-log.md`.
