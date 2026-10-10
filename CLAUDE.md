# QuoteGate — working rules

- Synthetic data only. No real orders, brokerage, strategies, or AI at runtime.
- Python 3.12 stdlib only unless a dependency is approved. Tests: `python3 -m unittest discover -s tests -t .`
- Pure logic (`quote_gate/book.py`, `quote_gate/router.py`) takes injected `now_ns`; runtime uses `time.monotonic_ns()`.
- Hot path: no synchronous per-message logging; instrumentation is bounded.
- Delegation to Hermes: `tools/hermes_task.sh`; contracts in `docs/contracts/`; patches reviewed before apply, max two repairs.
- Do not claim sponsor endorsement, production readiness, exchange-grade latency, or physical-host fault tolerance.
- Keep private host details in `docs/private/` (gitignored), not in public docs.
- Teammate-owned files: TBD — do not delegate or edit them without coordination.
