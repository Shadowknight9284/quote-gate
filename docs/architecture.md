# Architecture

Four separate things live in this repository. Only B is a running system.

## A. Deterministic in-process quant experiment (the controlled reference)
`market_sim` → `backtest` (Book, Strategy, router, `SimVenue`, Ledger) → `experiments` → paper.
One process, no sockets, no clocks: time is the step index. Same seed ⇒ identical event stream in
every configuration; faults/execution use a separate RNG. Used for every number in the paper.

## B. Networked runtime (what actually runs over TCP)
```
 venue process(es)                         aggregator process (single coordinator)
 ┌───────────────────────┐   1 TCP conn    ┌──────────────────────────────────────────────┐
 │ quote loop ──quotes──►│────per venue───►│ feed reader (ONE per connection) demux:       │
 │ control reader:       │◄──ping/order/───│   quote → Book.on_quote → quote_listener      │
 │  ping→pong            │   status/release│   pong  → feed RTT                            │
 │  order→match→STORE→exec│───exec/status──►│   exec/status → LiveRunner.on_frame           │
 │  order_status→status  │                 │ pinger (≤1 ping outstanding)                  │
 │ OrderStore (bounded,  │                 │ freshness monitor (observability only)        │
 │  in memory)           │                 │ LiveRunner: Strategy, router, Ledger,         │
 └───────────────────────┘                 │   pending orders, ack timers, status polling  │
                                           │ client port: route / stats (separate)         │
                                           └──────────────────────────────────────────────┘
```
State ownership:
| State | Owner | Notes |
|---|---|---|
| Venue current quote, displayed size, order store, fill log | venue process | store bounded, in memory; fill log is evaluator instrumentation |
| Per-venue epoch/session, sequence, freshness | aggregator `Book` | reset on reconnect |
| Strategy position (confirmed), outstanding/UNKNOWN orders | `LiveRunner` / `Strategy` | only booked fills change it |
| Ledger (cash, position, fees, FIFO lots) | `LiveRunner.ledger` | idempotent by fill ID |

Flows:
- **Quotes**: venue quote loop → feed connection → aggregator feed reader → Book (validated, epoch-
  and sequence-checked) → `quote_listener` → one strategy decision per accepted reference quote.
- **Orders**: decision → `route(allowed=exec venues)` → `order` frame written on that venue's
  existing connection → venue validates, matches, **records outcome**, then writes `exec` → feed
  reader demuxes → runner validates order ID/venue/instance/session → Ledger → `release`.
- **UNKNOWN**: no exec within the ack timeout, or the connection drops while an order is sent →
  UNKNOWN → trading halts → `order_status` polled (≤1 query in flight, ≤50 queries), never resent.
  `found=false` from the same venue instance ⇒ never arrived (resolved, not executed);
  from a different instance (venue restarted) ⇒ stays UNKNOWN (`lost_record`), trading stays halted.

Bounds: 4 KiB frames; ≤16 client connections; 1 outstanding order; 1 outstanding ping; order store
1024 records (unreleased never evicted; full ⇒ `store_full` rejection before execution); event and
sample rings fixed-size with drop counters; venue quotes coalesce under write backpressure, exec and
status frames never do.

Limitations (by design for this prototype):
- **Single coordinator**: one aggregator process holds all strategy and ledger state. If it dies,
  routing and trading stop; restart loses positions, pending orders and counters (nothing persisted).
- **In-memory venue store**: a venue restart loses order records; deduplication is not restart-safe.
- Placement verified so far: everything on one VM (loopback); M3 ran one venue on a second VM on the
  same LAN for the market-data path only. Both VMs may share a physical host. The order path has
  **not** been run across VMs.

## C. Optional post-run analytics (Tiger Data)
Local artifacts → `quote_gate.importer` (separate process, after runs) → Tiger Data → SQL → report.
Never on the runtime path, never routing state, never a recovery mechanism. See
[tiger-data.md](tiger-data.md). Not yet connected.

## D. Development workflow (not runtime)
Claude Code designs, integrates and verifies. Hermes (a local-model coding agent on another VM)
receives small contracts in isolated per-task workspaces and returns patches; every patch is
reviewed and re-tested locally (acceptance tests, mutation checks). See
[delegation-log.md](delegation-log.md). Neither agent is part of the application runtime.
