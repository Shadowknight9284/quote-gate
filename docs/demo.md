# Demo (all local, 127.0.0.1)

## 1. Market-data path (three processes)
```
scripts/local_stack.sh                      # venues A, B + aggregator; PIDs in artifacts/stack.pids
python3 -m quote_gate.cli route --port 19200 --side buy --quantity 1
python3 -m quote_gate.cli stats --port 19200
kill -STOP <venue A pid>; route again       # A excluded as "stale" after 100 ms
kill -CONT <pid>; kill <venue B pid>        # B "disconnected", reconnect attempts counted
python3 -m quote_gate.bench --port 19200 --rate 2000 --duration 5 --trials 2 --label demo
kill $(cat artifacts/stack.pids)
```
A route request is a lookup in the aggregator's book; it never contacts a venue or trades.

## 2. Networked order path (one process, real TCP loopback)
```
python3 -m quote_gate.live --seed 101 --steps 2000 --rate 500
```
Prints decisions by reason, orders sent/filled/rejected, timeouts/UNKNOWN/reconciliation counts,
position and cash, execution RTT (order written → exec read), ledger-vs-venue reconciliation, and
a decision comparison with the deterministic backtest for the same market seed.

## 3. Deterministic quant experiment (paper numbers)
```
python3 -m quote_gate.experiments --out /tmp/qg
```
