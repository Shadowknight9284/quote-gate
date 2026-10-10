# Task board

Deadline Sun 11:00. Cut points: M3 must pass by Sun 03:00 or skip remote placement;
M5 execution only if M4 benchmarks exist by Sun 06:00. Freeze code Sun 09:30.

| ID | Task | Owner | Status |
|----|------|-------|--------|
| M1a | Quote validation, venue state, epochs, freshness (`book.py`) | Claude | done, tested |
| M1b | Router + fixture tests (`router.py`) | Claude | done, tested |
| T2 | Report formatter (`report.py`) | Hermes→Claude | done locally (Hermes patch rejected after 2 repairs) |
| T1 | Synthetic generator (`synthetic.py`) | Hermes | done, accepted after 1 repair |
| M1c | NDJSON protocol + venue simulator + aggregator (local, 1 venue) | Claude | done, tested locally (loopback) |
| M2 | Two local venues + benchmark client (scheduled load) | Claude | done, smoke-benchmarked locally |
| M3 | Venue B on second VM, RTT-only cross-host timing | Claude | done: 12/12 remote checks, matched bench, cleaned up |
| M4 | Fault + overload scenarios, report | — | todo |
| M5 | Simulated execution (UNKNOWN on timeout, idempotent IDs) | — | optional |
