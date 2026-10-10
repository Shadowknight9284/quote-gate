"""Per-venue quote state with connection epochs, sequence ordering and freshness.

Pure and clock-free: every time-dependent call takes ``now_ns`` (a monotonic
nanosecond reading supplied by the caller), so tests inject time directly.

Freshness is elapsed *local* time since receipt. It is not source age: a quote
received just now may have been delayed arbitrarily before it arrived.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

MAX_TICKS = 10**12
MAX_QUANTITY = 10**9


@dataclass(frozen=True, slots=True)
class Quote:
    venue_id: str
    symbol: str
    sequence: int
    bid_ticks: int
    ask_ticks: int
    bid_quantity: int
    ask_quantity: int


class Reject(str, Enum):
    MALFORMED = "malformed"            # wrong types / missing fields / out-of-range values
    BAD_QUANTITY = "bad_quantity"      # quantity < 1 or > MAX_QUANTITY
    CROSSED = "crossed"                # ask <= bid (locked counts as crossed)
    WRONG_SYMBOL = "wrong_symbol"
    WRONG_VENUE = "wrong_venue"        # venue_id differs from the connection's venue
    OLD_EPOCH = "old_epoch"            # message from a superseded connection
    NOT_CONNECTED = "not_connected"
    DUPLICATE = "duplicate"            # sequence == last accepted
    OUT_OF_ORDER = "out_of_order"      # sequence < last accepted


# Rejections that make the venue's current state unknown (quote invalidated).
INVALIDATING = frozenset({Reject.MALFORMED, Reject.BAD_QUANTITY, Reject.CROSSED,
                          Reject.WRONG_SYMBOL, Reject.WRONG_VENUE})


class FeedStatus(str, Enum):
    DISCONNECTED = "disconnected"
    NO_QUOTE = "no_quote"      # connected, no valid quote yet in this epoch
    INVALID = "invalid"        # last message in this epoch failed validation
    STALE = "stale"
    FRESH = "fresh"


def validate_quote(q: Quote) -> Reject | None:
    """Synthetic validity rules; returns None when valid."""
    if not (isinstance(q.venue_id, str) and q.venue_id and isinstance(q.symbol, str) and q.symbol):
        return Reject.MALFORMED
    ints = (q.sequence, q.bid_ticks, q.ask_ticks, q.bid_quantity, q.ask_quantity)
    if any(type(v) is not int for v in ints):
        return Reject.MALFORMED
    if q.sequence < 1 or not (1 <= q.bid_ticks <= MAX_TICKS and 1 <= q.ask_ticks <= MAX_TICKS):
        return Reject.MALFORMED
    if not (1 <= q.bid_quantity <= MAX_QUANTITY and 1 <= q.ask_quantity <= MAX_QUANTITY):
        return Reject.BAD_QUANTITY
    if q.ask_ticks <= q.bid_ticks:
        return Reject.CROSSED
    return None


@dataclass(slots=True)
class VenueState:
    venue_id: str
    connected: bool = False
    connection_epoch: int = 0
    last_sequence: int = 0          # last accepted sequence in this epoch (0 = none)
    quote: Quote | None = None
    received_monotonic_ns: int = 0
    invalid: bool = False
    accepted: int = 0
    rejected: dict[str, int] = field(default_factory=dict)
    gaps: int = 0                   # accepted quotes that skipped sequence numbers

    def status(self, now_ns: int, stale_after_ns: int) -> FeedStatus:
        if not self.connected:
            return FeedStatus.DISCONNECTED
        if self.invalid:
            return FeedStatus.INVALID
        if self.quote is None:
            return FeedStatus.NO_QUOTE
        if now_ns - self.received_monotonic_ns >= stale_after_ns:
            return FeedStatus.STALE
        return FeedStatus.FRESH


class Book:
    """Latest quote per venue for one symbol."""

    def __init__(self, symbol: str, venue_ids, stale_after_ns: int):
        if stale_after_ns <= 0:
            raise ValueError("stale_after_ns must be positive")
        self.symbol = symbol
        self.stale_after_ns = stale_after_ns
        self.venues = {v: VenueState(v) for v in sorted(venue_ids)}

    def on_connect(self, venue_id: str) -> int:
        """Start a new session; prior quote and sequence are discarded."""
        st = self.venues[venue_id]
        st.connection_epoch += 1
        st.connected = True
        st.last_sequence = 0
        st.quote = None
        st.invalid = False
        return st.connection_epoch

    def on_disconnect(self, venue_id: str, epoch: int) -> None:
        st = self.venues[venue_id]
        if epoch == st.connection_epoch:
            st.connected = False
            st.quote = None

    def on_quote(self, venue_id: str, epoch: int, q: Quote, now_ns: int) -> Reject | None:
        st = self.venues[venue_id]
        r = self._check(st, epoch, q)
        if r is not None:
            st.rejected[r.value] = st.rejected.get(r.value, 0) + 1
            if r in INVALIDATING:
                st.invalid = True
                st.quote = None
            return r
        if st.last_sequence and q.sequence != st.last_sequence + 1:
            st.gaps += 1
        st.last_sequence = q.sequence
        st.quote = q
        st.received_monotonic_ns = now_ns
        st.invalid = False
        st.accepted += 1
        return None

    def _check(self, st: VenueState, epoch: int, q: Quote) -> Reject | None:
        if epoch != st.connection_epoch:
            return Reject.OLD_EPOCH
        if not st.connected:
            return Reject.NOT_CONNECTED
        r = validate_quote(q)
        if r is not None:
            return r
        if q.venue_id != st.venue_id:
            return Reject.WRONG_VENUE
        if q.symbol != self.symbol:
            return Reject.WRONG_SYMBOL
        if q.sequence == st.last_sequence:
            return Reject.DUPLICATE
        if q.sequence < st.last_sequence:
            return Reject.OUT_OF_ORDER
        return None

    def status(self, venue_id: str, now_ns: int) -> FeedStatus:
        return self.venues[venue_id].status(now_ns, self.stale_after_ns)
