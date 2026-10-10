# Real-data extension: Coinbase BTC-USD (real observations, hypothetical execution)

Contract: [contracts/R1-coinbase.md](contracts/R1-coinbase.md). Separate from the synthetic
experiment and the paper; results are never mixed with synthetic results.

## What it does
1. **Capture** (read-only, public): one WebSocket connection to Coinbase Exchange's public feed,
   `level2_batch` + `heartbeat` for BTC-USD. Raw messages are stored verbatim with local receipt
   timestamps. No account, API key, authenticated channel, or order entry exists in this code.
2. **Normalize** (offline, deterministic): rebuild the L2 book from the snapshot and absolute-size
   updates (`"0"` deletes), batch-atomically, in exact integer ticks (0.01 USD) and size units
   (1e-8 BTC); emit top of book after every applied message; invalidate on disconnect/malformed/
   crossed input; require a fresh snapshot per session.
3. **Replay**: the frozen policy (θ = 0.2, q_max = 5 **lots of 0.001 BTC** = 0.005 BTC) with
   **hypothetical** IOC execution against the reconstructed top of book. No real order is sent.

## Commands
```
python3 -m quote_gate.coinbase_capture --out artifacts/realdata/<run> --seconds 600 --max-mib 250
python3 -m quote_gate.coinbase_replay --capture artifacts/realdata/<run>     # writes <run>/replay/
python3 -m unittest tests.test_l2book tests.test_coinbase_replay           # fixtures, no network
```
No dependency is installed: the WebSocket client (`quote_gate/ws_client.py`) is a minimal read-only
RFC 6455 client using the standard library (TLS with certificate verification).

## Outputs
- `raw.jsonl`, `manifest.json` (limits, counts by type, sessions, stop reason, product metadata,
  sha256) — capture.
- `replay/normalized.jsonl` (+ sha256 in `ingestion.json`), `replay/ingestion.json` (messages,
  sessions, snapshots, batches, deletes, invalid intervals), `replay/report.json` (per hypothetical
  scenario: orders, fills, rejections, fees, pre/post-fee terminal liquidation-value P&L in USD,
  drawdown, signal hit rate) — every report carries the label
  "real market observations with hypothetical execution".

## First capture and replay (2026-10-10, agent-run; not human verified)
Metadata, ingestion statistics and the hypothetical report are committed in
[realdata-results/](realdata-results/) (raw market data is kept local, not published).

- Capture: 600 s, stop reason `duration`, 1 session, 0 reconnects, 9 956 messages
  (1 subscriptions ack, 1 snapshot, 9 355 `l2update` batches, 599 heartbeats), 5.8 MB,
  raw sha256 `835e07e9…`. The subscription ack names the channel `level2_50`.
- Normalization: 9 355 batches applied, 65 435 level changes, 25 913 deletions, 5 259 deletions of
  absent levels (counted, not errors), 0 invalid intervals; 9 356 top-of-book events. The single
  counted invalidation is the end-of-capture disconnect. Replay run twice: byte-identical
  `normalized.jsonl`, `ingestion.json`, `report.json`.
- Hypothetical execution (real market observations with hypothetical execution):

| Scenario | Orders | Filled | Rejected | Fees (USD) | Pre-fee P&L (USD) | Post-fee P&L (USD) |
|---|---|---|---|---|---|---|
| fee 0, delay 0 | 1798 | 227 | 1571 insufficient size | 0 | −0.02 | −0.02 |
| fee 10 bp, delay 0 | 1798 | 227 | 1571 insufficient size | 94.23 | −0.02 | −94.67 |
| fee 10 bp, delay 1 event | 1955 | 267 | 1641 size, 47 price moved | 110.83 | −0.07 | −111.32 |

  P&L is terminal liquidation value (hypothetical; open inventory −5 lots valued at the last valid top).
  Most orders were rejected because the displayed top-of-book size was below 0.005 BTC.
  Evaluator-only hit rate (sign of imbalance > θ vs next mid change): 0.598 over 204 mid changes —
  one 10-minute sample; not a finding. With any hypothetical fee the result is dominated by fees.

## Limitations
- One product, one short capture; not a general result and not evidence of profitability.
- Displayed liquidity does not guarantee a real fill: queue position, latency, other participants,
  hidden liquidity and market impact are ignored. Fees are declared hypothetical rates (0 and 10 bp),
  not Coinbase's fee schedule.
- `level2_batch` has no sequence numbers: message gaps cannot be detected; heartbeats are recorded
  but not used as quote freshness.
- Timestamps are local receipt times; no source-to-receiver latency is claimed.
- Not imported into Tiger Data (outside the Phase 1 authorization).
