"""Generate the realistic sample for Check fills (deterministic; synthetic data only).

  python3 examples/fills/make_realistic.py

Writes realistic_broker.csv (a broker-style export: partial fills, rebates, one instrument) and
realistic_bot.csv (a bot's own log with different column names and side codes), with problems planted
in the bot log. The planted list is printed and kept in README.md.
"""
from __future__ import annotations

import csv
import os
import random
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

SEED = 2026
ORDERS = 150
HERE = os.path.dirname(os.path.abspath(__file__))


def generate():
    rng = random.Random(SEED)
    t = datetime(2026, 10, 9, 13, 30, tzinfo=timezone.utc)
    price, pos = Decimal("431.20"), 0
    broker = []                                   # one row per fill
    for i in range(ORDERS):
        oid = f"ORD-{5000 + i}"
        qty = rng.randint(1, 10)
        side = "buy" if (pos < -12 or (pos <= 12 and rng.random() < 0.5)) else "sell"
        pos += qty if side == "buy" else -qty
        parts = [qty] if qty < 4 or rng.random() < 0.75 else [qty // 2, qty - qty // 2]
        for p in parts:
            t += timedelta(seconds=rng.randint(2, 40))
            price += Decimal(rng.randint(-8, 8)) / 100
            per_share = Decimal("-0.0020") if rng.random() < 0.3 else Decimal("0.0035")   # rebate or fee
            fee = (per_share * p).quantize(Decimal("0.0001"))
            broker.append({"oid": oid, "side": side, "qty": p, "price": price, "fee": fee,
                           "time": t.isoformat(timespec="seconds")})
    return broker


def plant(broker):
    """Copy the broker fills into a bot log and plant known problems."""
    bot = [dict(r) for r in broker]
    by_order = {}
    for i, r in enumerate(bot):
        by_order.setdefault(r["oid"], []).append(i)
    singles = [o for o, ix in by_order.items() if len(ix) == 1]
    splits = [o for o, ix in by_order.items() if len(ix) > 1]
    planted, drop, extra = [], set(), []
    pick = iter(singles[5::9])

    for _ in range(2):                            # booked twice: a resent order or duplicate report
        o = next(pick)
        extra.append((by_order[o][0], dict(bot[by_order[o][0]])))
        planted.append((o, "booked twice"))
    for _ in range(2):                            # lost confirmation: never booked
        o = next(pick)
        drop.add(by_order[o][0])
        planted.append((o, "missing from bot"))
    o = next(pick)                                # fee rounded to cents by the bot
    r = bot[by_order[o][0]]
    r["fee"] = r["fee"].quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) + Decimal("0.01")
    planted.append((o, "mismatch: fee"))
    o = next(pick)                                # bot logged its limit price, not the fill price
    bot[by_order[o][0]]["price"] += Decimal("0.05")
    planted.append((o, "mismatch: price"))
    o = splits[3]                                 # bot booked only the first partial fill
    drop.add(by_order[o][-1])
    planted.append((o, "mismatch: quantity (second partial fill never booked)"))

    rows = []
    for i, r in enumerate(bot):
        if i not in drop:
            rows.append(r)
        rows.extend(e for j, e in extra if j == i)
    t_last = datetime.fromisoformat(broker[-1]["time"])
    rows.append({"oid": "ORD-9001", "side": "buy", "qty": 3, "price": broker[-1]["price"],   # timed out,
                 "fee": Decimal("0.0105"), "time": (t_last + timedelta(seconds=5)).isoformat()})  # assumed filled
    planted.append(("ORD-9001", "not at broker"))
    return rows, planted


def write(broker, bot):
    with open(os.path.join(HERE, "realistic_broker.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Order ID", "Side", "Filled Qty", "Avg Price", "Commission", "Filled At"])
        for r in broker:
            w.writerow([r["oid"], r["side"].upper(), r["qty"], f"{r['price']:.2f}", r["fee"], r["time"]])
    with open(os.path.join(HERE, "realistic_bot.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["client_id", "dir", "size", "px", "fee_usd", "ts"])
        for k, r in enumerate(bot):
            w.writerow([r["oid"], "B" if r["side"] == "buy" else "S", r["qty"], f"{r['price']:.2f}", r["fee"], r["time"]])
            if k == 40:                           # two unreadable rows, as real logs have
                w.writerow(["ORD-XXXX", "B", "2", "N/A", "0", r["time"]])
            if k == 90:
                w.writerow(["ORD-YYYY", "HOLD", "1", "431.00", "0", r["time"]])


def main():
    broker = generate()
    bot, planted = plant(broker)
    write(broker, bot)
    print(f"{len(broker)} broker fills across {ORDERS} orders; {len(bot)} bot rows + 2 unreadable")
    for o, what in planted:
        print(f"  {o}: {what}")


if __name__ == "__main__":
    main()
