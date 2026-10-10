# Protocol (v0)

Transport: TCP, one persistent connection per venue (aggregator dials venue).
Framing: newline-delimited JSON, UTF-8, `MAX_FRAME` = 4096 bytes incl. newline.
Oversize/undecodable/non-object/truncated frame → connection closed (FrameError).
Decodable frame with wrong shape → counted (`bad_shape`), skipped.

Venue → aggregator:
- `{"type":"hello","venue_id","symbol","session"}` first frame, within 1 s, venue_id must match config.
- `{"type":"quote","venue_id","symbol","sequence","bid_ticks","ask_ticks","bid_quantity","ask_quantity"}`
  Sequence restarts at 1 per session (connection).

Aggregator → venue (feed connection, M3): `{"type":"ping","id":int}` every 100 ms, ≤1
outstanding, 1 s timeout (`ping_timeout`). Venue replies `{"type":"pong","id"}` in-stream
(dropped and counted if its write buffer is over limit). Venue ends the session on subscriber EOF.

Client → aggregator (separate port, ≤16 connections, 1 request in flight per connection, 30 s idle timeout):
- `{"type":"route","id"?,"side":"buy|sell","quantity":int}` → `route_result` with
  `venue_id` (null = no eligible route), `price_ticks`, `quote_sequence`, `connection_epoch`, `excluded[[venue, reason]]`.
- `{"type":"stats"}` → counters and bounded latency summaries.

Lifecycle/limits: connect timeout 1 s; reconnect backoff 50 ms doubling to 1 s cap,
reset after a good hello. Venue accepts one subscriber; extras refused (counted).
Venue overload: if socket write buffer > 256 KiB the scheduled quote is skipped
(`coalesced`), visible downstream as a sequence gap. A venue behind schedule
sends late (lag recorded), it does not drop slots; after a stall it bursts to catch up.
Shutdown: SIGINT/SIGTERM stops accepting, cancels feeds, prints final stats.

## Orders on the feed connection (contract O1, frozen 2026-10-10; loopback slice)
Same persistent venue connection; the aggregator's single feed reader demultiplexes `quote`,
`pong`, `exec`, `status`. Writes are single `write()` calls on the event loop (serialized).
`hello` now carries `instance` (random per venue process) in addition to `session`.

Client → venue
- `{"type":"order","client_order_id":str(1..64),"symbol":str,"venue_id":str,"instance":str,
   "session":int,"side":"buy"|"sell","quantity":int>=1,"limit_ticks":int>=1}`
  ints are `type(x) is int` (bool rejected). Validated: fields/types, symbol, venue_id, instance,
  session == the connection's session.
- `{"type":"order_status","client_order_id":str}` — query only; never re-executes.
- `{"type":"release","client_order_id":str}` — client has booked the outcome; record may be evicted.

Venue → client
- `{"type":"exec","client_order_id","venue_id","instance","session","status":"FILLED"|"REJECTED",
   "reason":str|null,"fill_price_ticks":int|null,"quantity":int,"fee_ticks":int,"quote_sequence":int|null}`
- `{"type":"status","client_order_id","venue_id","instance","found":bool, ...exec fields if found}`

Execution (same rules as the backtest venue): IOC limit; buy fills at current ask <= limit, sell at
current bid >= limit, if displayed size >= quantity; fill consumes displayed size until the next
quote; else REJECTED with reason (`price_moved`, `insufficient_quantity`, `session`, `bad_order`,
`conflicting_order_id`, `store_full`). Fee = fee_per_unit x quantity.
Ordering: outcome is recorded in the store **before** the exec frame is written.
Exec/status frames are never coalesced or dropped by the venue (only quotes coalesce; pongs may drop).

Idempotency and store: same `client_order_id` + identical payload → stored outcome re-sent, no
re-execution; different payload → REJECTED `conflicting_order_id` (not stored). Store capacity is
bounded (default 1024 records). Only released records are evicted (oldest first); an unreleased
record is never evicted. If the store is full of unreleased records the order is REJECTED
`store_full` before execution and is not stored. The store is in memory: a venue restart (new
`instance`) loses it. Deduplication is **not** restart-safe.

Client rules (`quote_gate/live.py`): one outstanding order. No exec within the ack timeout, or a
disconnect while sent → UNKNOWN; new orders halt; `order_status` is polled (bounded, one query in
flight per order); the order is never resent. `found=false` from the **same instance** means the
order never arrived (records precede acks and are not evicted unreleased) → resolved as not executed.
`found=false` from a **different instance** (venue restarted) is not proof of non-execution → the
order stays UNKNOWN and is reported as `lost_record`. Responses are applied only if order ID is
pending/known, and venue, instance and session match.
