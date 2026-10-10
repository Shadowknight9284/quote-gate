# Implemented behavior (M1 core)

- Quote validity (synthetic rules): int fields (bool rejected); sequence ≥ 1;
  1 ≤ ticks ≤ 1e12; 1 ≤ quantity ≤ 1e9; ask > bid (locked = crossed).
- Per-venue state: `connection_epoch` increments on each connect; connect clears
  quote and sequence (fresh state required). Messages/disconnects from older
  epochs are ignored. Sequence must increase within an epoch: equal = DUPLICATE,
  lower = OUT_OF_ORDER (state never rolls back). Gaps are accepted and counted.
- Malformed/crossed/bad-quantity/wrong-symbol/wrong-venue invalidates the venue's
  quote until the next valid one (venue state is unknown). Dup/old do not invalidate.
- Freshness: FRESH iff `now - received_monotonic_ns < stale_after_ns`. This is
  local receipt age, **not source age**; a quote may have been delayed before arrival.
- Routing: buy = lowest eligible ask, sell = highest eligible bid; eligible = FRESH
  and displayed qty ≥ order qty; ties → lowest venue_id; else explicit no-route with
  per-venue exclusion reasons. No splitting.

Networked slice (M1c, tested on loopback only): see `docs/protocol.md`.
Receipt time is taken after the frame leaves the asyncio buffer; kernel/buffer
queueing before that is not included in `process_ns`.
Freshness is evaluated lazily at route time, so expiry is detected at the first
query after the threshold (no background timer).

M3 (tested across VMs on the same LAN): venue B ran on the second VM; aggregator +
venue A + bench on the coding VM. Verified: wire == generator, no rejects/gaps,
freeze → STALE at 100.2 ms quote age with A unaffected, stop → DISCONNECTED and
reconnect attempts counted, restart → new epoch, session/sequence reset, wire ==
generator again. Feed ping/pong RTT measured on the aggregator host only.

M4 (tested locally): bounded event log + freshness monitor; faults under load recorded with
0 self-reported freshness violations; saturation plateau ~45k route lookups/s on one VM.
Evidence store (E1): schema/queries/importer built; tested without a database only.

Open: coordinator-restart semantics (aggregator state is
in-memory only: restart loses all venue state and counters; every venue starts at
NO_QUOTE in a new epoch), execution (M5).

## Quant slice integration status (2026-10-10)
- A. Pure quant tests: implemented and tested (`tests/test_quant.py`, `tests/test_ledger_hermes.py`).
- B. In-process event-driven backtest: implemented and tested; frozen 5-cell matrix run on seeds
  101–130 (`python3 -m quote_gate.experiments`), reported in `paper/quotegate.tex`.
- C. Networked strategy-to-venue execution: **not implemented**. Nothing in A or B exercises the TCP
  venue transport for orders.
Execution model (approved): IOC limit at observed price; venue validates session, current executable
price (buy at ask ≤ limit, sell at bid ≥ limit) and displayed size; full fill or rejection.
