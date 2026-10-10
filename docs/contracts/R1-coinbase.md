# Contract R1: Coinbase public L2 capture and replay (real observations, hypothetical execution)

Source: Coinbase Exchange public WebSocket feed `wss://ws-feed.exchange.coinbase.com`, product
`BTC-USD`, channels `level2_batch` (public; level2 updates batched every 50 ms) and `heartbeat`.
Read-only. No account, API key, authenticated channel, or order entry of any kind.

## Feed semantics (from Coinbase docs, checked 2026-10-10)
- `snapshot`: `bids`/`asks` arrays of `[price, size]` strings = the entire book.
- `l2update`: `changes` array of `[side, price, size]`; `size` is the **new absolute size** at that
  level (not a delta); `"0"` removes the level; `time` is the matching-engine event time.
- `heartbeat`: `sequence`, `last_trade_id`, `product_id`, `time`, once per second. It is **not** a
  quote and does not refresh quote freshness.
- level2_batch messages carry no sequence numbers: no gap detection is claimed.

## Units (product metadata `GET /products/BTC-USD`, recorded in each capture's metadata)
- Price tick = `quote_increment` = 0.01 USD. `price_ticks = price / 0.01`, exact (Decimal);
  a price that is not a multiple is rejected (counted, session invalidated).
- Size unit = `base_increment` = 0.00000001 BTC. `size_units = size / 1e-8`, exact; non-multiples
  rejected likewise.
- Hypothetical strategy lot = 0.001 BTC = 100 000 size units. The frozen policy's q_max = 5 and max
  order 5 mean **5 lots = 0.005 BTC**, not 5 BTC.
- Ledger units: quantity in lots; price per lot in units of 1e-5 USD (= price_ticks exactly, since
  0.01 USD x 0.001 BTC); cash and P&L in 1e-5 USD; reports convert to USD with Decimal.

## Book reconstruction (`quote_gate/l2book.py`, pure)
- States: `EMPTY` (no snapshot) → `VALID` after a snapshot → `INVALID` on disconnect, malformed
  input, unknown side, non-increment value, crossed/locked top, or level-count bound exceeded.
  `INVALID` persists until the next snapshot of a **new session**; updates are never applied to an
  invalid or empty book (counted as `updates_ignored_invalid`).
- A batch (`l2update` message) is applied atomically: validate all changes first; any invalid change
  invalidates the session and applies none of the batch. Top of book is emitted only after a whole
  batch (or snapshot) is applied.
- Zero size deletes a level; deleting an absent level is counted (`delete_absent`), not an error.
- Bounds: at most 200 000 levels per side; exceeding it invalidates (never silently truncates).

## Capture (`quote_gate/coinbase_capture.py`)
One connection at a time; default 600 s and 250 MiB total output; frame/message bound 32 MiB;
at most 3 reconnects with backoff; each connection is a new session that must start with a snapshot.
Raw messages are stored verbatim with local receipt `wall_ns`, `mono_ns`, session and local event
index; a manifest stores sha256, counts, limits, product metadata and stop reason. Local timestamps
only: no source-to-receiver latency is claimed (clocks are not synchronized with Coinbase).

## Replay (`quote_gate/coinbase_replay.py`)
Deterministic: same raw file ⇒ identical normalized output and report. Event time = local receipt
monotonic ns (real inter-arrival preserved, no scaling). The top of book is (re-)emitted after every
applied snapshot/batch, even if unchanged; freshness (50 ms, as in the frozen policy) is the local-
receipt age of the last applied book message (heartbeats do not count). One decision per emission. Session boundaries end the epoch: the strategy
sees DISCONNECTED until the next snapshot; invalid intervals are reported.

Session end cancels pending delayed orders (`session_ended`); a last valid top is used only for the
dated terminal valuation and never for decisions after disconnection.

## Hypothetical execution
Frozen policy (θ = 2000 bp, q_max = 5 lots, max order 5 lots, cooldown 0), decision once per
emitted top-of-book event. IOC limit at the observed best price; it fills only if, at the decision's
arrival event (delay configurable, default 0 events), the best price is no worse and the displayed
size covers the order; displayed size is consumed until the next top change. **Displayed liquidity
does not guarantee a real fill; queue position, latency, other participants and impact are ignored.**
Displayed size is consumed only within the event of our fill and restored from the next event.
Fees are a declared hypothetical rate in basis points of notional (reported at 0 and 10 bp), not
Coinbase's fee schedule. Label on every output: "real market observations with hypothetical
execution". No real orders are ever sent.
