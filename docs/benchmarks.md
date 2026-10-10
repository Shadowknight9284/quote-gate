# Benchmarks

## What a benchmark request does
`quote_gate.bench` sends **route lookups** (`{"type":"route","side","quantity"}`) to the
aggregator's client port. A route lookup is answered from the aggregator's in-memory book;
it does **not** contact any venue and executes nothing. It is not a trade.

## Latency fields (exact start/end events; all client-host `time.monotonic_ns`)
| field | start | end |
|---|---|---|
| schedule_to_completion | scheduled instant of request i (start + i/rate) | reply frame decoded by client |
| send_to_completion | just before client writes the request | reply frame decoded by client (client-observed round trip; **not** server service time) |
| schedule_to_send | scheduled instant | just before client writes the request (generator delay + wait for a free connection) |
| generator_delay | scheduled instant | producer enqueues the request |

Server-internal (aggregator bounded rings, **cumulative since aggregator start**, i.e. warm-up + all trials):
| field | start | end |
|---|---|---|
| route_ns | before `route()` | after `route()` returns (computation only, excludes I/O) |
| process_ns | feed frame returned by reader | book updated (feed path) |
| feed_rtt_ns (per venue) | aggregator, just before writing ping on the feed connection | aggregator, pong frame read. Both stamps on the aggregator host. Pong queues behind buffered quotes, so this is feed-path RTT, not bare network RTT. 1 ping / 100 ms. |

Offered load is scheduled; ≤ connections outstanding; bounded queue overflow counted as
`not_sent_backlog`. p99 is flagged not meaningful when n < 1000.

## Earlier results (loopback, single VM)
- 2026-10-10 smoke, 2000 req/s, 2 trials: see git history of this file; superseded by M3 local runs below.
- Rate sweep 4k / 8k / 16k req/s: **one loopback trial at each rate**, all requests completed,
  no backlog. This is **not** a demonstrated capacity limit and not a cross-machine result.

## M3: local-B vs remote-B (cross-VM, same LAN), 2026-10-10
Matched configuration: venue A local on the coding VM; venue B either local (loopback) or on the
second VM over the LAN; both venues 1000 quotes/s; aggregator and bench client on the coding VM;
stale 100 ms; bench 2000 req/s, 10 s, 2 s warm-up, 8 connections, 3 trials each.
Deployed source: commit `91dc3cf` (runtime files identical to `76a56c3`), checksums verified.
Raw: `artifacts/bench/*-m3-remoteB/`, `artifacts/bench/*-m3-localB/`, `artifacts/m3/m3-20261010T154645/` (local, gitignored).

Route lookups (per trial: 20000 offered / 20000 completed / 0 errors / 0 backlog, 2000.0 rps, both configs):
| config | send_to_completion p50 / p99 µs (3 trials) | schedule_to_completion p99 µs | generator_delay p99 µs |
|---|---|---|---|
| remote-B | 150.0/227.7, 151.2/233.0, 152.1/229.2 | 1238.0, 1229.6, 1229.4 | 1075.1, 1074.7, 1074.7 |
| local-B | 146.9/224.1, 150.6/236.3, 148.4/232.1 | 1229.0, 1234.6, 1230.5 | 1070.8, 1074.4, 1074.7 |

Feed path (aggregator stats after the 3 trials; n = 330 per venue, so p99 is **not meaningful**):
| config | venue | feed_rtt p50 / p95 µs | accepted | gaps | rejected | feed errors |
|---|---|---|---|---|---|---|
| remote-B | A (loopback) | 121.8 / 207.7 | 33196 | 0 | 0 | 0 |
| remote-B | B (cross-VM) | 573.1 / 799.7 | 33196 | 0 | 0 | 0 |
| local-B | A (loopback) | 118.9 / 211.8 | 33190 | 0 | 0 | 0 |
| local-B | B (loopback) | 118.1 / 192.3 | 33189 | 0 | 0 | 0 |

Server-internal (cumulative): route_ns p50 3.6 µs / p99 6.1–6.4 µs; process_ns p50 6.1–6.3 µs / p99 12.8–13.7 µs.
ICMP reference (50 pings, coding VM → second VM): avg 0.705 ms, min 0.207, max 1.108 ms.

Interpretation:
- Route-lookup latency is unchanged by venue placement, as expected: lookups never touch venues.
- Remote placement adds roughly 0.45 ms p50 to the feed-path round trip for venue B; this is
  consistent with the ICMP reference and includes Python asyncio on both ends.
- schedule_to_completion (~1.2 ms p99) is dominated by load-generator delay (~1.07 ms p99),
  i.e. asyncio timer granularity in the Python client, not by the aggregator.
- Both VMs may share a physical host; this is cross-VM/LAN behaviour, not independent
  physical-host failure tolerance. Clocks were not compared across hosts.

## M4: faults under load and saturation sweep (local loopback, one VM), 2026-10-10
Instrumentation: aggregator event ring (10 000, 0 dropped), freshness monitor polling every 1 ms,
per-sample feed RTT, `quote_age_ns` in route replies; bench raw v2 with UTC anchor + observer.

Fault run (`scripts/m4_faults.sh`): 2000 req/s for 30 s, 8 connections, 1 trial; labelled fault
injection: 20 × SIGSTOP venue A for 300 ms, then kill venue B, restart after 1.5 s.
- 60 000 offered / 60 000 completed / 0 errors / 0 backlog.
- Freshness violations (server-reported quote age ≥ 100 ms on a routed reply): 0 of 60 000.
  This is an internal-consistency observation (the aggregator reports on its own decision), not
  independent proof of freshness correctness. Independent evidence is the injected-clock rule tests
  (`tests/test_book_router.py`), kept separate.
- Stale-detection delay (monitor observation − (receipt + 100 ms)), n = 20: min 48 µs,
  p50 0.44–0.48 ms, max 1.08 ms. Bounded by the 1 ms monitor tick plus asyncio timer
  granularity. n = 20 does not support a p99. The routing decision itself evaluates freshness
  exactly at query time and does not depend on the monitor.
- The single "stale at 100.2 ms quote age" observation in M3 is one observation, not a distribution.
- Venue B kill/restart: disconnect event, 4 refused reconnects counted, new epoch, fresh again.
  The monitor can miss states shorter than one tick (e.g. NO_QUOTE between connect and first quote).
- send_to_completion p50/p99 147.6/237.1 µs; generator_delay p99 1070.5 µs.

Saturation sweep (time-boxed; one loopback trial per rate, 4 s, 1 s warm-up, 16 connections):
| offered rps | completed / offered | not_sent_backlog | steady rps | send_to_completion p50/p99 µs | schedule_to_completion p99 µs |
|---|---|---|---|---|---|
| 24 000 | 95 969 / 96 000 | 31 | 23 988 | 141.4 / 307.1 | 575.1 |
| 32 000 | 127 942 / 128 000 | 58 | 31 978 | 177.5 / 352.7 | 533.1 |
| 48 000 | 183 655 / 192 000 | 8 345 | 46 220 | 331.2 / 544.5 | 2285.1 |
| 64 000 | 180 359 / 256 000 | 75 641 | 44 927 | 350.9 / 559.8 | 2350.6 |
Completed throughput plateaus near 45k route lookups/s with the bench client and aggregator
(both single-threaded Python) on the same VM; the bottleneck was not isolated. Freshness
violations 0 at every rate. Not a capacity claim; single trials.
