"""Execution rules and bounded idempotent order store shared by the backtest and networked venues.

Contract: docs/protocol.md "Orders on the feed connection" (O1) and docs/contracts/Q1-quant.md.
"""
from __future__ import annotations

from collections import OrderedDict

from .router import Side

DEFAULT_STORE_CAPACITY = 1024


def match(side: Side, qty: int, limit_ticks: int, quote, remaining: dict) -> tuple[str, int | None, str | None]:
    """IOC limit against the venue's current quote. Returns (status, price, reason).

    Buy fills at the current ask if ask <= limit; sell at the current bid if bid >= limit; displayed
    size must cover qty. Caller consumes `remaining[side]` on FILLED.
    """
    if quote is None:
        return "REJECTED", None, "no_quote"
    price = quote.ask_ticks if side is Side.BUY else quote.bid_ticks
    if (side is Side.BUY and price > limit_ticks) or (side is Side.SELL and price < limit_ticks):
        return "REJECTED", None, "price_moved"
    if remaining[side] < qty:
        return "REJECTED", None, "insufficient_quantity"
    return "FILLED", price, None


class OrderStore:
    """client_order_id -> (payload, outcome, released). Unreleased records are never evicted."""

    def __init__(self, capacity: int = DEFAULT_STORE_CAPACITY):
        if type(capacity) is not int or capacity < 1:
            raise ValueError("capacity must be int >= 1")
        self.capacity = capacity
        self._records: OrderedDict = OrderedDict()
        self.evicted = 0
        self.rejected_full = 0

    def __len__(self):
        return len(self._records)

    def get(self, oid):
        return self._records.get(oid)

    def has_room(self) -> bool:
        if len(self._records) < self.capacity:
            return True
        for oid, rec in self._records.items():          # oldest first
            if rec[2]:
                del self._records[oid]
                self.evicted += 1
                return True
        self.rejected_full += 1
        return False

    def put(self, oid, payload, outcome) -> None:
        assert oid not in self._records and len(self._records) < self.capacity
        self._records[oid] = (payload, outcome, False)

    def release(self, oid) -> bool:
        rec = self._records.get(oid)
        if rec is None:
            return False
        self._records[oid] = (rec[0], rec[1], True)
        return True

    def unreleased(self) -> int:
        return sum(1 for r in self._records.values() if not r[2])

    def outcomes(self):
        return [(oid, r[0], r[1]) for oid, r in self._records.items()]
