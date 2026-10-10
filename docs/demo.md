# Demo (all local, 127.0.0.1)

The full demo runs four script sections: tests, market-data freeze/disconnect handling,
networked loopback orders, and the deterministic quant matrix. It records a transcript:

```
scripts/demo.sh [OUT_FILE]          # default output: artifacts/demo/transcript.txt
scripts/demo.sh --quick [OUT_FILE]  # same first three script sections; skips only the matrix
```
`--quick` is not full quant verification. Use the default full demo or
`python3 -m quote_gate.experiments --out /tmp/qg` to reproduce the frozen matrix.
Invalid options or extra arguments exit with status 2 and usage. Run demos sequentially because
they share fixed loopback ports and log paths. Optional analytics below are documentation only;
the script does not access a database.

At the 2026-10-10 consolidation checkpoint, both modes passed 205 tests and each produced
1217 fills with ledger/venue mismatched 0. The full mode reproduced every frozen matrix mean;
quick mode omitted that section. Verification was agent-run, not human-verified. A historical
full transcript is committed at [demo-transcript.txt](demo-transcript.txt); it predates H4 and
is not a current test-count record. See [state.md](state.md) for verification commands.

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

## 3. Optional analytics
Results of the Phase 1 Tiger Data import and query validation, readable without credentials:
[analytics-example.md](analytics-example.md). Offline importer checks: [tiger-data.md](tiger-data.md).

## 4. Deterministic quant experiment (paper numbers)
```
python3 -m quote_gate.experiments --out /tmp/qg
```
