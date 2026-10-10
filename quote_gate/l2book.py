"""Pure L2 order-book reconstruction for Coinbase `level2_batch` (contract R1). No I/O.

Prices/sizes arrive as decimal strings and are converted exactly to integer ticks / size units.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

MAX_LEVELS_PER_SIDE = 200_000


class BookInvalid(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Units:
    price_increment: Decimal       # e.g. Decimal("0.01")
    size_increment: Decimal        # e.g. Decimal("0.00000001")

    def to_int(self, text, inc: Decimal, what: str) -> int:
        if not isinstance(text, str) or not text or len(text) > 40:
            raise BookInvalid(f"{what}: not a decimal string")
        try:
            d = Decimal(text)
        except InvalidOperation:
            raise BookInvalid(f"{what}: not a decimal") from None
        if not d.is_finite() or d < 0:
            raise BookInvalid(f"{what}: not finite / negative")
        q = d / inc
        if q != q.to_integral_value():
            raise BookInvalid(f"{what}: {text} is not a multiple of {inc}")
        return int(q)

    def price(self, text) -> int:
        p = self.to_int(text, self.price_increment, "price")
        if p == 0:
            raise BookInvalid("price: zero")
        return p

    def size(self, text) -> int:
        return self.to_int(text, self.size_increment, "size")


@dataclass(frozen=True, slots=True)
class Top:
    bid_ticks: int
    bid_units: int
    ask_ticks: int
    ask_units: int


class L2Book:
    """States: EMPTY -> VALID (snapshot) -> INVALID (until a new session's snapshot)."""

    def __init__(self, units: Units, max_levels: int = MAX_LEVELS_PER_SIDE):
        self.units = units
        self.max_levels = max_levels
        self.state = "EMPTY"
        self.bids: dict[int, int] = {}
        self.asks: dict[int, int] = {}
        self.invalid_reason: str | None = None
        self.stats = {"snapshots": 0, "batches_applied": 0, "changes_applied": 0, "deletes": 0,
                      "delete_absent": 0, "updates_ignored_invalid": 0, "invalidations": 0}

    # --- session lifecycle ---
    def new_session(self) -> None:
        """Connection (re)established: prior state is unusable until a snapshot arrives."""
        self.state, self.bids, self.asks, self.invalid_reason = "EMPTY", {}, {}, None

    def disconnect(self) -> None:
        self._invalidate("disconnect")

    def _invalidate(self, why: str) -> None:
        if self.state != "INVALID":
            self.stats["invalidations"] += 1
        self.state, self.invalid_reason = "INVALID", why
        self.bids, self.asks = {}, {}

    # --- messages ---
    def apply_snapshot(self, bids, asks) -> Top | None:
        if self.state != "EMPTY":
            # A snapshot mid-session is unexpected for level2_batch; treat as protocol error.
            self._invalidate("unexpected_snapshot")
            return None
        try:
            nb = self._levels(bids)
            na = self._levels(asks)
        except BookInvalid as e:
            self._invalidate(f"snapshot: {e}")
            return None
        self.bids, self.asks, self.state = nb, na, "VALID"
        self.stats["snapshots"] += 1
        return self._top_or_invalidate()

    def apply_batch(self, changes) -> Top | None:
        """Apply one l2update message atomically. Returns the new top, or None if not VALID."""
        if self.state != "VALID":
            self.stats["updates_ignored_invalid"] += 1
            return None
        try:
            if not isinstance(changes, list) or not changes:
                raise BookInvalid("changes: not a non-empty list")
            parsed = []
            for ch in changes:
                if not (isinstance(ch, list) and len(ch) == 3 and ch[0] in ("buy", "sell")):
                    raise BookInvalid("change: expected [side, price, size] with side buy|sell")
                parsed.append((ch[0], self.units.price(ch[1]), self.units.size(ch[2])))
        except BookInvalid as e:
            self._invalidate(f"batch: {e}")
            return None
        for side, p, s in parsed:
            book = self.bids if side == "buy" else self.asks
            if s == 0:
                if p in book:
                    del book[p]
                    self.stats["deletes"] += 1
                else:
                    self.stats["delete_absent"] += 1
            else:
                book[p] = s
            self.stats["changes_applied"] += 1
        if len(self.bids) > self.max_levels or len(self.asks) > self.max_levels:
            self._invalidate("level bound exceeded")
            return None
        self.stats["batches_applied"] += 1
        return self._top_or_invalidate()

    def _levels(self, rows) -> dict[int, int]:
        if not isinstance(rows, list):
            raise BookInvalid("levels: not a list")
        if len(rows) > self.max_levels:
            raise BookInvalid("level bound exceeded")
        out = {}
        for r in rows:
            if not (isinstance(r, list) and len(r) >= 2):
                raise BookInvalid("level: expected [price, size]")
            p, s = self.units.price(r[0]), self.units.size(r[1])
            if s == 0:
                raise BookInvalid("level: zero size in snapshot")
            if p in out:
                raise BookInvalid("level: duplicate price in snapshot")
            out[p] = s
        return out

    def top(self) -> Top | None:
        if self.state != "VALID" or not self.bids or not self.asks:
            return None
        b = max(self.bids)
        a = min(self.asks)
        return Top(b, self.bids[b], a, self.asks[a])

    def _top_or_invalidate(self) -> Top | None:
        t = self.top()
        if t is not None and t.bid_ticks >= t.ask_ticks:
            self._invalidate("crossed_or_locked")
            return None
        return t
