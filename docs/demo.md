# Demo (local)

```
scripts/local_stack.sh                      # venues A,B + aggregator on loopback
python3 -m quote_gate.cli route --port 19200 --side buy --quantity 1
python3 -m quote_gate.cli stats --port 19200
kill -STOP <venue A pid>; route again       # A excluded as "stale" after 100 ms
kill -CONT <pid>; kill <venue B pid>        # B "disconnected", reconnect attempts counted
python3 -m quote_gate.bench --port 19200 --rate 2000 --duration 5 --trials 2 --label demo
kill $(cat artifacts/stack.pids)
```
