"""Freshness-aware best-venue selection. Pure; no order splitting."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .book import Book, FeedStatus


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


INSUFFICIENT_QUANTITY = "insufficient_quantity"


@dataclass(frozen=True, slots=True)
class RouteDecision:
    venue_id: str | None            # None means no eligible route
    price_ticks: int | None
    quote_sequence: int | None
    connection_epoch: int | None
    excluded: tuple[tuple[str, str], ...]   # (venue_id, reason), venue-ID order

    @property
    def routed(self) -> bool:
        return self.venue_id is not None


def route(book: Book, side: Side, quantity: int, now_ns: int) -> RouteDecision:
    """Buy: lowest eligible ask. Sell: highest eligible bid. Ties: lowest venue_id.

    Eligible = FRESH status and displayed quantity >= requested quantity.
    """
    if not isinstance(side, Side):
        raise ValueError(f"invalid side: {side!r}")
    if type(quantity) is not int or quantity < 1:
        raise ValueError(f"invalid quantity: {quantity!r}")
    best = None
    best_key = None
    excluded = []
    for vid, st in book.venues.items():          # venues are stored sorted by id
        status = st.status(now_ns, book.stale_after_ns)
        if status is not FeedStatus.FRESH:
            excluded.append((vid, status.value))
            continue
        q = st.quote
        if side is Side.BUY:
            price, qty, key = q.ask_ticks, q.ask_quantity, q.ask_ticks
        else:
            price, qty, key = q.bid_ticks, q.bid_quantity, -q.bid_ticks
        if qty < quantity:
            excluded.append((vid, INSUFFICIENT_QUANTITY))
            continue
        if best_key is None or key < best_key:    # strict: earlier (lower) id wins ties
            best_key = key
            best = (vid, price, q.sequence, st.connection_epoch)
    if best is None:
        return RouteDecision(None, None, None, None, tuple(excluded))
    return RouteDecision(*best, excluded=tuple(excluded))
