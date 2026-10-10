# Environment summary — execution round-trip measurement (sanitized)

Sanitized from the local `environment.txt` recorded at measurement time (kept private: it contains a
session-specific cgroup path). Five runs, **agent-run**.

| Item | Value |
|---|---|
| Measurement date | 2026-10-10, runs 21:24:21Z–21:24:47Z |
| Revision | `a8042caa4c9829f60b0796ddc289e7167a5d6965` (`main`, clean working tree) |
| OS / kernel | Ubuntu 24.04.3 LTS; Linux 6.8.0-142-generic x86_64 |
| Python | 3.12.3 (standard library only) |
| Virtualization | KVM, full virtualization (guest view); host CPU model not exposed ("QEMU Virtual CPU version 2.5+") |
| Observed vCPUs | 16 (1 socket × 16 cores, 1 thread per core) |
| Observed RAM | 33,647,370,240 bytes (≈ 31.3 GiB) total |
| Resource limits | cgroup `cpu.max` = max, `memory.max` = max, `memory.high` = max (no container CPU/memory limit); no CPU pinning; CPU frequency governor not exposed |
| Load | 1-minute load average 0.00 before, during and after the runs |
| Dedicated resources | **Not guaranteed.** The vCPU and RAM figures are the VM's configured allocations as seen by the guest; the hypervisor host may be shared, and host contention was not measured. |

## Workload
`python3 -m quote_gate.live --steps 2000` (defaults `--seed 101 --rate 500`), run five times
sequentially. One venue simulator and the aggregator/strategy in one asyncio event loop over
loopback TCP; 2,000 synthetic market steps at 500 quotes/s (≈ 4 s) plus a 1 s settle; frozen policy;
at most one outstanding order (sequential round trips). Each run sent and filled 1,217 orders.

## Files
`run1.json` … `run5.json`: the complete, unmodified JSON report printed by each run (order counts,
timeouts, unresolved orders, position, ledger/venue reconciliation, `exec_rtt_ns`, `reconcile_ns`,
feed RTT, venue stats, backtest comparison). Each run exited 0 with empty stderr.
