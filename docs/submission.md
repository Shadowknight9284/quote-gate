# Submission draft (not submitted; for human review)

**QuoteGate** — turns observable market information into bounded trading decisions and measures
how execution costs, latency and venue failures affect those decisions and their outcomes.
Synthetic data for the controlled experiment; no real money or real orders anywhere.

## What it does
- A small distributed market-data system: venue simulators stream quotes over persistent TCP
  connections to an aggregator that tracks per-venue sessions, sequence order and local-receipt
  freshness, and routes to the best eligible venue.
- A simulated execution path on the same connections: IOC orders, idempotent client order IDs, a
  bounded venue order store, and explicit UNKNOWN outcomes that halt trading until reconciliation —
  never a blind resend.
- A transparent strategy (displayed-size imbalance → target position) evaluated with exact integer
  accounting in a fixed, seeded experiment, written up in a two-page paper.
- Optional post-run analytics in Tiger Data (TimescaleDB), validated against local recomputation.

## System design (why it is built this way)
- **One reader per connection, single-threaded event loop**: quotes, pings and execution reports
  share a connection without reader races; ordering per venue is preserved.
- **Correctness before speed**: epochs and sequence numbers make stale or replayed data harmless;
  freshness is local receipt age (honestly not source age); crossed/invalid quotes invalidate a venue.
- **Bounded everything**: frames, connections, outstanding orders and pings, order store, event and
  sample rings — with counters instead of silent drops.
- **Failure semantics are explicit**: timeout or disconnect ⇒ UNKNOWN ⇒ halt ⇒ status query;
  a venue restart that lost its records leaves the order UNKNOWN rather than guessing.
- **Measurement discipline**: route lookup RTT, feed ping/pong RTT and order→ack RTT are separate,
  all on one host's clock; cross-host clocks are never subtracted.

## Results (agent-run, single seeds where stated; see docs/state.md)
- Deterministic experiment (30 seeds): the planted signal is detectable (hit rate .633 vs .502) but
  the policy loses in every cell — the one-step signal cannot pay for a 1-tick spread plus fees.
- Cross-VM execution (one run each): 3012/3012 orders filled; with every 50th ack dropped, 21 UNKNOWN
  orders all reconciled (~302 ms), none resent; ledger matched the venue's fill log exactly.
  Order→ack RTT p50 649 µs cross-VM vs 132 µs on one VM (application level).
- Two defects found by CI and fixed: a flooding client could starve the feed; shutdown could hang
  with an open client.
- Tiger Data: 3 runs imported; database percentiles equal local recomputation to 0 ns.
- Real-data extension (merged, audited): Coinbase BTC-USD public `level2_batch` capture (600 s),
  deterministic replay with hypothetical execution. 227 of 1798 orders filled (the rest exceeded
  displayed size); pre-fee −$0.02, with a hypothetical 10 bp fee −$94.67 — fees dominate.
  One 10-minute sample; not a finding.

## How we built it
Python 3.12 standard library (asyncio), integer ticks throughout, unittest + GitHub Actions CI,
LaTeX for the paper, TimescaleDB for optional analytics. Built with Claude Code as integrator and
reviewer; a local-model agent (Hermes) produced small delegated pieces that were accepted only after
independent tests — several of its patches were rejected (see docs/delegation-log.md).

## Honest limitations
Synthetic model with a planted mechanism; short real-data sample; no queue position, impact or
partial fills; single coordinator; in-memory venue order store; cross-VM runs on one LAN, possibly
one physical host. Nothing here claims real-market alpha or production readiness.

## Links
Paper: `paper/quotegate.tex` · Demo: `scripts/demo.sh`, `docs/demo-transcript.txt` ·
Architecture: `docs/architecture.md` · Evidence: `docs/state.md` · Real data: `docs/realdata.md` ·
Agents: `AGENTS.md`, `docs/handoff.md`. Tests: `python3 -m unittest discover -s tests -t .` (CI).
