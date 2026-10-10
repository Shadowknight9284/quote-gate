# QuoteGate

A HackRU prototype for synthetic market-data aggregation, freshness-aware
routing, and measured behavior under load and venue failure.

## Initial milestone

- One synthetic instrument.
- Two deterministic venue simulators.
- Correct best-eligible-quote selection.
- Staleness, sequence, and reconnect handling.
- Reproducible latency and throughput measurements.

## Possible extension

Simulated full-fill-or-reject order execution with explicit timeout and
duplicate-request behavior.

## Non-goals

No real money, brokerage integration, trading strategy, production-readiness
claim, or AI dependency.

## Collaboration

Use short-lived feature branches and pull requests.
Keep secrets and private deployment details out of tracked files.
Implementation instructions and benchmark documentation are being added.
