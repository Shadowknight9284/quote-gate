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
- Hypothetical execution (real market observations with hypothetical execution), audited replay:

| Scenario | Orders | Filled | Rejected | Turnover | Notional | Fees paid | Pre-fee P&L | Post-fee P&L |
|---|---|---|---|---|---|---|---|---|
| fee 0, delay 0 | 1798 | 227 | 1571 size | 1.135 BTC | $94,229.22 | $0 | −$0.02025 | −$0.02025 |
| fee 10 bp, delay 0 | 1798 | 227 | 1571 size | 1.135 BTC | $94,229.22 | $94.23018 | −$0.02025 | −$94.66557 |
| fee 10 bp, delay 1 event | 1955 | 267 | 1641 size, 47 price moved | 1.335 BTC | $110,833.61 | $110.83479 | −$0.07045 | −$111.32038 |

  Reconciliation (exact, every scenario): pre-fee = realized + unrealized at the mark;
  pre-fee − post-fee = fees paid + hypothetical liquidation fee ($0.41514 at 10 bp). Fees are rounded
  up per fill in 1e-5 USD (excess over bp × notional: $0.00096 for 227 fills). Terminal inventory:
  −0.005 BTC, valued at the ask $83,027.32 of local event 9955, session 1 (hypothetical; no trade).
  First size rejection: sell 0.005 BTC (500 000 units) vs displayed bid 0.0046 BTC (460 000 units)
  at $83,042.45. BTC-USD's 1-cent spread is ~0.0012 bp, so spread cost is tiny; hypothetical fees
  dominate. Evaluator-only hit rate (imbalance > θ at the event just before a mid change vs that
  change): 0.685, n = 200 — one 10-minute sample; not a finding; directional accuracy is not P&L.

## Audit (2026-10-10) — findings fixed before merge
- F1 (defect): an order delayed past a session end could fill in the next session. Now cancelled at
  session end (`session_ended`); test fails on the old code.
- F2 (assumption, now explicit): displayed size is consumed only within the event of our fill and is
  restored from the next event — our fills never deplete the real book (optimistic).
- F3 (evaluator definition): hit rate used the imbalance at the previous mid change; now the latest
  imbalance before the change (0.598 → 0.685 on this capture; P&L unaffected).
- F4: reports now include turnover, notional, per-fill fee range, unrealized P&L, a dated terminal
  mark, and the hypothetical liquidation fee; the identities above are tested.
- F5: zero-delay and delayed fills share one execution path.
- WebSocket client now rejects RSV/extension bits, data frames inside a fragmented message,
  fragmented or >125-byte control frames, unknown opcodes and invalid UTF-8 (5 cases the old client
  mishandled); 12 tests with in-memory streams, including partial reads and TLS-verification checks.
- Freshness: decisions occur after a whole batch is applied; freshness is the local receipt age of
  the last applied book message (all batches, not only top changes); heartbeats never count.

## Limitations
- One product, one short capture; not a general result and not evidence of profitability.
- Displayed liquidity does not guarantee a real fill: queue position, latency, other participants,
  hidden liquidity and market impact are ignored. Fees are declared hypothetical rates (0 and 10 bp),
  not Coinbase's fee schedule.
- `level2_batch` has no sequence numbers: message gaps cannot be detected; heartbeats are recorded
  but not used as quote freshness.
- Timestamps are local receipt times; no source-to-receiver latency is claimed.
- Not imported into Tiger Data (outside the Phase 1 authorization).
