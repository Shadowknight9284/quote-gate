# Scope

Synthetic two-venue market-data testbed that produces correct, freshness-aware
routing decisions and measures end-to-end performance and behavior under load
and venue failure.

Build order: (M1) pure rules + local single-venue slice → (M2) two local
networked venues → (M3) one venue on a second VM → (M4) fault/overload
benchmarks → (M5, optional) simulated full-fill-or-reject execution.

Directions kept open pending judge feedback: A. QuoteGate (routing) /
B. FeedScope (feed integrity observability). Both reuse book/protocol/bench.

Non-goals: see README. Deadline: Sunday 11:00 (confirmed). Team: 2.
