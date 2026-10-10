# Contract Q1: quant slice (frozen 2026-10-10 before any evaluation run)

Synthetic data only. Integer price ticks and integer quantities. 1 tick = 0.01 currency units
(declared; all P&L below is reported in ticks·units unless stated). Time is injected integer ns
or integer steps; no clocks. No claim about real markets, alpha, or profitability.

## Information set (strategy may observe only)
Accepted bid/ask/quantities of the preselected reference venue A (current; past via its own
memory), A's locally observed freshness/connection state (`Book.status`), and its own confirmed
position and order state. It never receives future quotes, simulator draws, the latent price,
future execution outcomes, or evaluation results.

## Signal (`quant.imbalance_bp`, `quant.reference_signal`)
`imbalance = (bq - aq) / (bq + aq)` for ints bq, aq >= 1, represented exactly as basis points with
truncation toward zero: `imb_bp = trunc(10000 (bq - aq) / (bq + aq))` ∈ [-9999, 9999].
Invalid quantities → `ValueError`. `reference_signal` is `None` unless A's status is FRESH.
Displayed-quantity imbalance of one venue's top of book; not trade-flow imbalance; never summed
across venues or price levels.

## Policy (`quant.PolicyConfig`, `quant.Strategy`)
Frozen parameters: theta = 2000 bp, q_max = 5, max_order_qty = 5, cooldown = 0 steps.
- Target: `imb > theta → +q_max`; `imb < -theta → -q_max`; otherwise `0` (equality ⇒ 0).
- Desired order = target − confirmed position, clipped to max_order_qty; none if zero.
- Cadence: one decision per market step, after that step's quotes are delivered.
- Signal `None` (stale/invalid/disconnected A) ⇒ no new order; existing position is kept and
  reported as held (no implied close). No emergency liquidation policy exists.
- At most one outstanding order. While an order is outstanding, no new order.
- An UNKNOWN outcome halts new orders until reconciliation resolves it; a timeout never clears it.
- Cooldown: no new order within `cooldown` steps after the last order's resolution.
- Every decision records a reason code: `order`, `at_target`, `no_signal`, `outstanding`,
  `halted_unknown`, `cooldown`.

## Execution (`backtest`, simulated venue; no networking yet)
- One instrument, one venue per order chosen by `router.route` at decision time; full fill or
  explicit rejection; no partial fills, splitting, queues, matching engine, or auto-retry.
- Order = IOC limit at the observed executable price (buy limit = observed ask, sell = observed
  bid) with the observed session epoch. It reaches the venue after `exec_delay_steps` steps.
  The venue fills at its then-current ask (buy) / bid (sell) only if the session is unchanged,
  that price is no worse than the limit, and displayed quantity ≥ order quantity; else REJECTED
  with a reason.
- Liquidity consumption: a fill consumes displayed quantity at that venue's current quote;
  the remaining quantity is restored only by the venue's next quote (replenishment per quote).
- Acknowledgement returns `ack_delay_steps` later. If the fault schedule drops the ack, the order
  becomes UNKNOWN at `ack_timeout_steps`; nothing is resent and no fill is booked. Reconciliation
  queries the venue by client order ID `reconcile_after_steps` later and books the venue's actual
  outcome exactly once.
- Client order IDs are unique; resubmitting an ID with the same payload returns the existing state;
  a different payload is rejected.

## Accounting (`ledger.Ledger`)
For signed fill quantity Δq, price p (ticks), total fee f (ticks·units, = fee_per_unit·|Δq|):
`cash ← cash − p·Δq − f`, `position ← position + Δq`. Realized P&L uses FIFO lots (exact ints).
Fill IDs are idempotent: same ID + same payload ⇒ no-op ("duplicate"); same ID + different payload
⇒ `LedgerError`. `|position| ≤ max_position` or `LedgerError` with state unchanged.
Mark: liquidation value at the reference venue's executable price (bid if long, ask if short)
when A is FRESH; otherwise the mark is MISSING and labelled, never guessed.
Identity: `cash + position·mark = realized_gross − fees + unrealized(mark)`.
End of run: report open inventory and unrealized P&L separately; optionally liquidate via the
same execution path (with costs) — the report states which was done.

## Market model (`market_sim`), regimes
Latent reference price p_t (ticks). Venue A quotes `[p_t, p_t + spread]` with sizes
`bq, aq ~ U{1..100}`. Venue B quotes `[p_t − oB, p_t + spread + oB']`, oB, oB' ~ U{0..b_off} —
never better than A, so no accidental cross-venue arbitrage. Next step: with probability m the
price moves one tick, up with probability `(1 + beta · imbalance_A) / 2`.
- Null regime: beta = 0. Controlled regime: beta = 0.5. Illustrative assumption only.
Market randomness comes from `random.Random(market_seed)` only; faults/execution use a separate
`random.Random(fault_seed)`. Strategy actions never feed back into the market stream.

## Evaluation
Evaluation seeds 101–130 (never used during development; development used seeds 1–5).
Matrix: (1) no-trade baseline, (2) null, (3) controlled, (4) controlled + exec delay,
(5) controlled + fault (A feed outages + dropped acks). Reported per cell: trades, turnover,
gross and net P&L (gross = net + fees), fees, spread cost, max |inventory|, max drawdown of
marked equity (missing marks counted), rejections, UNKNOWN outcomes, and the signal-prediction
statistic: hit rate of sign(imb_A,t) vs sign(p_{t+1} − p_t) over steps where |imb| > theta
and the price moved (evaluation-side only; uses future prices the strategy never sees).
