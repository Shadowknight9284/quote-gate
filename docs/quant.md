# Quant design and results

Contract: [contracts/Q1-quant.md](contracts/Q1-quant.md). Code: `quant.py`, `ledger.py`,
`market_sim.py`, `backtest.py`, `experiments.py`, `execution.py`, `live.py`.

## Information available to the strategy
Reference venue A's accepted bid/ask prices and displayed sizes, A's locally observed freshness and
connection state, and the strategy's own confirmed position and order state. Never: future quotes,
simulator draws, the latent price, future outcomes, venue-side records, or evaluation results.

## Synthetic model (illustrative assumption, not a market finding)
Latent price p_t (integer ticks). A quotes [p_t, p_t+1]; B quotes at or outside A (no cross-venue
arbitrage). Sizes uniform 1..100. With probability 0.5 the price moves one tick, up with probability
(1 + β·I_t)/2 where I_t is A's displayed-size imbalance. **Null**: β = 0. **Controlled**: β = 0.5.
I_t is observable at step t and affects only p_{t+1} − p_t (tested). Market randomness is separate
from fault/execution randomness; strategy actions never change the market stream.

## Frozen parameters (set before evaluation)
θ = 0.2 (2000 bp), q_max = 5, max order 5, cooldown 0, stale after 50 ms (5 steps of 10 ms),
fee 1 tick/unit, 1 tick = 0.01 currency units. Evaluation seeds 101–130 (development used 1–5).

## Policy
Target +q_max if I > θ, −q_max if I < −θ, else 0 (equality ⇒ 0). Order = target − confirmed
position, clipped. One decision per step (backtest) or per accepted reference quote (live). No order
while A is stale/invalid (position is held, not assumed closed), while an order is outstanding, or
while any outcome is UNKNOWN. No emergency liquidation policy exists.

## Execution (IOC limit, approved)
Buy limit = observed ask, sell limit = observed bid. On arrival the venue checks session, current
executable price (buy at ask ≤ limit, sell at bid ≥ limit) and displayed size; full fill or
rejection. A fill consumes displayed size until the venue's next quote. No partial fills, splitting,
resting orders, or retries. Lost acks ⇒ UNKNOWN ⇒ reconciliation by client order ID only.

## Accounting
cash ← cash − p·Δq − f; position ← position + Δq; FIFO realized P&L; duplicate fill IDs are no-ops,
conflicting reuse is rejected. Two ledgers are reported:
- **strategy-known**: fills the strategy has confirmed;
- **evaluator-authoritative**: replay of all venue fill records (evaluator-only).
Their difference comes only from outcomes still unresolved at the end; economics use the
authoritative ledger.

Terminal valuation is **hypothetical**: open inventory valued at the last fresh reference executable
price (bid if long, ask if short). *Post-fee terminal liquidation-value P&L* subtracts a fee for that
hypothetical liquidation; *pre-fee* adds back all fees. No liquidation trade occurs.

## Results (deterministic backtest; seeds 101–130; per-seed means ± sample s.d.)
| Cell | Fills | Turnover | Pre-fee P&L | Post-fee P&L | Hit rate |
|---|---|---|---|---|---|
| 1 no trade | 0 | 0 | 0 | 0 | .502 |
| 2 null | 3317 | 16586 | −8974 ± 198 | −25563 ± 293 | .502 |
| 3 controlled | 3317 | 16586 | −7382 ± 209 | −23971 ± 308 | .633 |
| 4 + 2-step delay | 693 | 3463 | −1846 ± 152 | −5311 ± 171 | .633 |
| 5 + faults | 2009 | 10043 | −4430 ± 281 | −14476 ± 582 | .633 |

Paired controlled − null: +1592 ± 107 on 30/30 seeds. Hit rate = share of steps with |I| > θ and a
price move where sign(I_t) = sign(p_{t+1} − p_t); directional accuracy, not profitability.

**Negative result, under this model and these seeds only.** The signal is detectable but redrawn
every step; positions turn over every ~1.5 steps; expected gain ≈ 0.13 ticks/unit/step held vs a
round-trip cost of spread + 2·fee = 3 ticks. Under delay, losses shrink only because fewer orders
fill (37.5% rejected); loss per unit traded worsens. Not a general impossibility result; not
evidence about real markets. The networked path ([state.md](state.md)) has not produced paper numbers.
