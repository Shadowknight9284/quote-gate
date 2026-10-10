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
