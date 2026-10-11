# Operations console

A local browser console for QuoteGate. It runs the real code paths — no mock data — and shows what
they do. Standard library only (`http.server`, `asyncio`); no build step, no CDN, no network beyond
127.0.0.1.

```
python3 -m quote_gate.console [--port 8050]
# open http://127.0.0.1:8050
```

## What a run is
**Start run** launches, on one background event loop, the same loopback stack as
`python3 -m quote_gate.live`:

- **Venue A** — the strategy's reference venue and its only execution venue (as in every published run).
- **Venue B** — streams the second synthetic venue's quotes; market data and route previews only.
- **Aggregator + strategy runner** — 500 quotes/s, stale threshold 50 ms, frozen policy (θ = 0.20,
  |position| ≤ 5), fee 1 tick per unit, seed and duration chosen in the top bar.

When the duration ends (or **Stop run** is pressed) the runner stops deciding, waits 1 s for
in-flight outcomes, takes a final snapshot with reconciliation, and shuts the stack down.

## Views
| View | Shows | Source |
|---|---|---|
| **Live** | orders sent / filled / rejected, UNKNOWN or lost orders; per-venue status, quote age vs 50 ms, top of book, sequence, epoch, connects, gaps; 30 s status timeline; imbalance signal and target; position gauge; decision counts; a route preview (router run now for 1 unit, all venues, with exclusion reasons); event stream | `Book`, `route`, `reference_signal`, `LiveRunner` counters, aggregator event ring |
| **Orders** | lifecycle counts (SENT → RESOLVED / UNKNOWN → RESOLVED or LOST RECORD); venue fill log vs strategy ledger; this run's order→execution RTT histogram; ledger; feed ping RTT; last 40 resolved orders | `venue_reconciliation`, `exec_rtt_ns`, `Ledger` |
| **Quant** | strategy rules; the published 5-cell × 30-seed matrix; paired controlled − null per seed; Coinbase replay results; **Re-run matrix & compare** (runs `quote_gate.experiments`, compares all keys except `run_id`) | `docs/evidence/synthetic-matrix/source-*.json`, `docs/realdata-results/` (the historical agent-run capture; P&L is hypothetical terminal liquidation value, post-fee including a hypothetical closing fee) |
| **Evidence** | **Run tests** (the full `unittest` suite); the five published latency runs; **Verify SHA-256** of every file listed in `docs/evidence/README.md`; scope statements | `docs/evidence/` |

## Fault controls
Each control calls an existing, labelled `Venue` fault hook; nothing is simulated in the browser.

| Control | Hook | Expected behaviour (enforced by tests) |
|---|---|---|
| Pause / resume feed (A or B) | `Venue.pause()` / `resume()` | venue goes STALE after 50 ms and is excluded from routing. Pausing A gives "No signal" (A is the strategy's reference and only execution venue) while B stays fresh and routable in the route preview |
| Drop connection (A or B) | `Venue.drop_connection()` | DISCONNECTED, reconnect with a new epoch; in-flight orders become UNKNOWN |
| Drop next ack (A) | `drop_next_acks += 1` | ack timeout → UNKNOWN → halt → status query → resolved |
| Disconnect after next order (A) | `disconnect_after_exec += 1` | UNKNOWN on disconnect → reconnect → resolved via status |
| Restart venue mid-order (A) | `drop_next_acks`, then `restart_lose_store()` after the timeout (same sequence as `test_venue_restart_with_lost_record_stays_unresolved`) | LOST RECORD: trading stays halted; reconciliation can report the venue fill as unbooked and attributable to the unresolved order ("Explained gap": identified uncertainty, not resolved accounting). Zero unbooked fills is not expected after this fault |

## Measurement caveat
Snapshots are taken on the event loop that also runs the venues and the runner, and the console
runs two venues instead of one: a different workload from the published runs, so its round-trip times
are not comparable with their 53 µs p50 (the console read about 190 µs p50 in local trials). Use
`python3 -m quote_gate.live` for benchmark numbers.

## Safety and bounds
- Binds 127.0.0.1 only; serves exactly three static files; JSON bodies ≤ 4 KiB; seed and duration
  validated (seed 1–1,000,000; 5–300 s); one run and one job of each kind at a time.
- No per-request logging. Recent orders (40) and console notes (200) are bounded deques; every
  other figure comes from the existing bounded rings and counters.
- Everything is simulated. No exchange account, funds, or real orders.

Tests: `tests/test_console.py` (real HTTP against a real loopback run).
