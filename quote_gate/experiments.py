"""Fixed evaluation matrix (contract Q1). Parameters frozen before evaluation; seeds 101-130.

  python3 -m quote_gate.experiments [--out artifacts/quant]

Units: ticks x quantity units (1 tick = 0.01 currency, declared). Gross = net + fees paid.
"Net if liquidated" marks open inventory at the last fresh reference-venue executable price and
subtracts the liquidation fee; it is hypothetical (ignores displayed size at that moment).
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from dataclasses import asdict

from .backtest import ExecConfig, run
from .market_sim import MarketConfig
from .quant import PolicyConfig

SEEDS = tuple(range(101, 131))
STEPS = 5000
POLICY = PolicyConfig(theta_bp=2000, q_max=5, max_order_qty=5, cooldown_steps=0)
FEE = 1          # ticks per unit
CELLS = {
    "1_no_trade": (0, ExecConfig(trade=False, fee_per_unit=FEE)),
    "2_null": (0, ExecConfig(fee_per_unit=FEE)),
    "3_controlled": (50, ExecConfig(fee_per_unit=FEE)),
    "4_controlled_delay": (50, ExecConfig(fee_per_unit=FEE, exec_delay_steps=2, ack_delay_steps=1)),
    "5_controlled_fault": (50, ExecConfig(fee_per_unit=FEE, outage_every=500, outage_len=50,
                                          drop_ack_pct=5, ack_timeout_steps=5,
                                          reconcile_after_steps=10)),
}


def _stats(xs):
    xs = [x for x in xs if x is not None]
    if not xs:
        return None
    return {"mean": round(statistics.fmean(xs), 1), "stdev": round(statistics.stdev(xs), 1) if len(xs) > 1 else 0.0,
            "min": min(xs), "max": max(xs), "n": len(xs)}


def run_cell(name, beta_pct, xcfg):
    per_seed = []
    for seed in SEEDS:
        m = MarketConfig(seed=seed, steps=STEPS, beta_pct=beta_pct)
        x = ExecConfig(**{**asdict(xcfg), "fault_seed": seed + 10_000})
        r = run(m, POLICY, x)
        f = r.final
        per_seed.append({"seed": seed, "fills": r.fills, "turnover": r.turnover,
                         "fees": f["fees_ticks"], "gross": f["gross_if_liquidated_ticks"],
                         "net": f["net_if_liquidated_ticks"], "realized_gross": f["realized_gross_ticks"],
                         "open_position": f["position"], "unrealized": f["unrealized_ticks"],
                         "spread_cost_halfticks": r.spread_cost_halfticks,
                         "max_abs_position": r.max_abs_position, "max_drawdown": r.max_drawdown_ticks,
                         "marks_missing": r.marks_missing, "rejections": r.rejections,
                         "unknown": r.unknown, "reconciled": r.reconciled,
                         "halted_unknown_at_end": f["halted_unknown_at_end"],
                         "decisions": r.decisions, "hit": r.hit, "hit_n": r.hit_n})
    agg = {k: _stats([s[k] for s in per_seed]) for k in
           ("fills", "turnover", "fees", "gross", "net", "spread_cost_halfticks", "max_abs_position",
            "max_drawdown", "unknown", "marks_missing")}
    hit, n = sum(s["hit"] for s in per_seed), sum(s["hit_n"] for s in per_seed)
    agg["hit_rate"] = {"hits": hit, "n": n, "rate": round(hit / n, 4) if n else None}
    agg["frac_seeds_gross_pos"] = round(sum(1 for s in per_seed if (s["gross"] or 0) > 0) / len(SEEDS), 3)
    agg["frac_seeds_net_pos"] = round(sum(1 for s in per_seed if (s["net"] or 0) > 0) / len(SEEDS), 3)
    rej = {}
    for s in per_seed:
        for k, v in s["rejections"].items():
            rej[k] = rej.get(k, 0) + v
    agg["rejections_total"] = rej
    agg["open_position_seeds"] = sum(1 for s in per_seed if s["open_position"])
    return {"cell": name, "beta_pct": beta_pct, "exec": asdict(xcfg), "aggregate": agg, "per_seed": per_seed}


def main(argv=None):
    p = argparse.ArgumentParser(prog="quote_gate.experiments")
    p.add_argument("--out", default="artifacts/quant")
    a = p.parse_args(argv)
    run_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-matrix"
    out = {"run_id": run_id, "seeds": list(SEEDS), "steps": STEPS, "policy": asdict(POLICY),
           "market": asdict(MarketConfig(seed=0, steps=STEPS)), "fee_per_unit": FEE,
           "cells": [run_cell(n, b, x) for n, (b, x) in CELLS.items()]}
    os.makedirs(a.out, exist_ok=True)
    path = os.path.join(a.out, run_id + ".json")
    with open(path, "w") as f:
        json.dump(out, f, indent=1)
    for c in out["cells"]:
        g = c["aggregate"]
        print(json.dumps({"cell": c["cell"], "fills_mean": g["fills"]["mean"],
                          "gross": g["gross"], "net": g["net"], "fees_mean": g["fees"]["mean"],
                          "hit_rate": g["hit_rate"], "frac_gross_pos": g["frac_seeds_gross_pos"],
                          "max_abs_pos_max": g["max_abs_position"]["max"],
                          "drawdown_mean": g["max_drawdown"]["mean"], "unknown_mean": g["unknown"]["mean"],
                          "rejections": g["rejections_total"], "open_pos_seeds": g["open_position_seeds"]}))
    print(json.dumps({"saved": path}))


if __name__ == "__main__":
    main()
