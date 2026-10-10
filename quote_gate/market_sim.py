"""Two-venue synthetic market with null / controlled-signal regimes (contract Q1).

Exogenous: depends only on MarketConfig (incl. market seed); strategy actions never feed back.
Draw order per step t (fixed): bq_A, aq_A, bq_B, aq_B, oB_bid, oB_ask, move, direction.
`latent_price` is yielded for the evaluator only; the backtest never passes it to the strategy.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from .book import Quote
from .quant import imbalance_bp


@dataclass(frozen=True, slots=True)
class MarketConfig:
    seed: int
    steps: int
    step_ns: int = 10_000_000
    start_price: int = 10_000
    spread_ticks: int = 1
    move_prob_pct: int = 50
    beta_pct: int = 0                 # 0 = null regime; 50 = controlled regime (beta = 0.5)
    b_offset_max: int = 1
    max_size: int = 100
    symbol: str = "SYN1"

    def __post_init__(self):
        for name in ("seed", "steps", "step_ns", "start_price", "spread_ticks", "move_prob_pct",
                     "beta_pct", "b_offset_max", "max_size"):
            if type(getattr(self, name)) is not int:
                raise ValueError(f"{name} must be int")
        if self.steps < 0 or self.step_ns < 1 or self.start_price < 1 or self.spread_ticks < 1:
            raise ValueError("invalid steps/step_ns/start_price/spread_ticks")
        if not (0 <= self.move_prob_pct <= 100 and 0 <= self.beta_pct < 100):
            raise ValueError("move_prob_pct in [0,100], beta_pct in [0,100)")
        if self.b_offset_max < 0 or self.max_size < 1:
            raise ValueError("b_offset_max >= 0, max_size >= 1")


@dataclass(frozen=True, slots=True)
class Step:
    t: int
    now_ns: int
    quotes: tuple            # (Quote A, Quote B)
    latent_price: int        # evaluator-only


def generate(cfg: MarketConfig):
    rng = random.Random(cfg.seed)
    p = cfg.start_price
    for t in range(cfg.steps):
        bq_a, aq_a = rng.randint(1, cfg.max_size), rng.randint(1, cfg.max_size)
        bq_b, aq_b = rng.randint(1, cfg.max_size), rng.randint(1, cfg.max_size)
        ob, oa = rng.randint(0, cfg.b_offset_max), rng.randint(0, cfg.b_offset_max)
        moves = rng.randrange(100) < cfg.move_prob_pct
        u = rng.randrange(20000)
        qa = Quote("A", cfg.symbol, t + 1, p, p + cfg.spread_ticks, bq_a, aq_a)
        qb = Quote("B", cfg.symbol, t + 1, max(1, p - ob), p + cfg.spread_ticks + oa, bq_b, aq_b)
        yield Step(t, t * cfg.step_ns, (qa, qb), p)
        if moves:
            # P(up) = (1 + beta*imb)/2 = (10000 + beta_pct*imb_bp/100) / 20000, exact integers.
            up = u < 10000 + (cfg.beta_pct * imbalance_bp(bq_a, aq_a)) // 100
            p = p + 1 if up else max(1, p - 1)
