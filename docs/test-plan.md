# Test plan

Run: `python3 -m unittest discover -s tests -t .` (repo root).

| Layer | File | Clock | Covers |
|---|---|---|---|
| Rules | tests/test_book_router.py | injected | fixture (A 10001 / B 10003), stale boundary, sell, ties, quantity, reconnect, epochs, dup/old, invalid input |
| Generator | tests/test_synthetic.py (Hermes), tests/acceptance/test_synthetic_acceptance.py (Claude) | none | determinism, reference draw order, validation |
| Report | tests/acceptance/test_report_acceptance.py (Claude) + Hermes tests | none | T2 contract |
| Bench math | tests/test_bench_summary.py | none | warm-up exclusion, percentiles, small-sample flag |
| Integration | tests/test_integration_local.py | real, loopback | wire==generator, stale exclusion, reconnect epoch, oversize frame, crossed on wire, client limit |

Timing magnitudes are never asserted in tests; they belong in docs/benchmarks.md.
Planned: fault scenarios (dup/old on wire, sequence reset, slow report consumer, overload), cross-host.
