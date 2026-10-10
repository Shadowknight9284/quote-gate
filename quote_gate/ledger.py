"""Fill ledger: integer cash/position accounting with FIFO realized P&L (contract Q1).

cash <- cash - p*dq - f ; position <- position + dq. Units: ticks and quantity units.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field


class LedgerError(Exception):
    """Rejected fill; ledger state unchanged."""


@dataclass(frozen=True, slots=True)
class Fill:
    fill_id: str
    order_id: str
    venue_id: str
    signed_qty: int          # +buy, -sell
    price_ticks: int
    fee_ticks: int           # total fee for this fill
    decision_ref: int        # decision step that produced the order
    step: int                # step at which the venue executed

    def validate(self):
        if not (isinstance(self.fill_id, str) and self.fill_id and isinstance(self.order_id, str)
                and self.order_id and isinstance(self.venue_id, str) and self.venue_id):
            raise ValueError("fill_id, order_id, venue_id must be non-empty str")
        for name in ("signed_qty", "price_ticks", "fee_ticks", "decision_ref", "step"):
            if type(getattr(self, name)) is not int:
                raise ValueError(f"{name} must be int")
        if self.signed_qty == 0 or self.price_ticks < 1 or self.fee_ticks < 0:
            raise ValueError("signed_qty != 0, price_ticks >= 1, fee_ticks >= 0 required")


@dataclass
class Ledger:
    max_position: int
    cash_ticks: int = 0
    position: int = 0
    fees_ticks: int = 0
    realized_gross_ticks: int = 0
    fills: dict = field(default_factory=dict)     # fill_id -> Fill (insertion ordered)
    _lots: deque = field(default_factory=deque)   # (signed_qty, price) open lots, FIFO

    def __post_init__(self):
        if type(self.max_position) is not int or self.max_position < 1:
            raise ValueError("max_position must be int >= 1")

    def apply(self, f: Fill) -> str:
        """Returns "applied" or "duplicate". Raises LedgerError / ValueError without side effects."""
        f.validate()
        prior = self.fills.get(f.fill_id)
        if prior is not None:
            if prior == f:
                return "duplicate"
            raise LedgerError(f"conflicting reuse of fill_id {f.fill_id}")
        new_pos = self.position + f.signed_qty
        if abs(new_pos) > self.max_position:
            raise LedgerError(f"position {new_pos} exceeds limit {self.max_position}")
        self.cash_ticks -= f.price_ticks * f.signed_qty + f.fee_ticks
        self.fees_ticks += f.fee_ticks
        self.position = new_pos
        self._match(f.signed_qty, f.price_ticks)
        self.fills[f.fill_id] = f
        return "applied"

    def _match(self, dq: int, price: int) -> None:
        while dq and self._lots and (self._lots[0][0] > 0) != (dq > 0):
            lot_q, lot_p = self._lots[0]
            m = min(abs(lot_q), abs(dq))
            # closing a long (lot_q>0) by selling at price: (price - lot_p) per unit; short: reverse
            self.realized_gross_ticks += (price - lot_p) * m if lot_q > 0 else (lot_p - price) * m
            lot_q += -m if lot_q > 0 else m
            dq += m if dq < 0 else -m
            if lot_q == 0:
                self._lots.popleft()
            else:
                self._lots[0] = (lot_q, lot_p)
        if dq:
            self._lots.append((dq, price))

    def unrealized_ticks(self, mark_ticks: int) -> int:
        return sum((mark_ticks - p) * q for q, p in self._lots)

    def mark_price(self, bid_ticks: int | None, ask_ticks: int | None) -> int | None:
        """Liquidation mark: bid if long, ask if short; None (MISSING) if that side is unknown."""
        if self.position > 0:
            return bid_ticks
        if self.position < 0:
            return ask_ticks
        return bid_ticks if bid_ticks is not None else ask_ticks

    def equity_ticks(self, mark_ticks: int | None) -> int | None:
        if self.position == 0:
            return self.cash_ticks
        return None if mark_ticks is None else self.cash_ticks + self.position * mark_ticks


def replay(fills, max_position: int) -> Ledger:
    led = Ledger(max_position)
    for f in fills:
        led.apply(f)
    return led
