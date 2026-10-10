# Architecture (current)

```
venue A sim ──TCP/NDJSON──┐
                           ├─> aggregator: Book (per-venue epoch/seq/freshness) ─> router
venue B sim ──TCP/NDJSON──┘          │
                                client port (route, stats) <── bench client / CLI
```
- Single asyncio process for the aggregator; one feed task per venue; one task per client.
- Pure core (`book.py`, `router.py`) has no I/O and takes injected time.
- Instrumentation: bounded rings (`metrics.Samples`), periodic stats to stdout, raw
  benchmark observations written only after a trial.
- Placement so far: all processes on the coding VM (loopback). Cross-VM placement is
  planned for venue B; both VMs may share a physical host, so this is not physical-host
  failure independence.
