"""Reference-venue imbalance signal and target-position strategy (contract Q1). Pure; no I/O."""
from __future__ import annotations

from dataclasses import dataclass

from .book import Book, FeedStatus
from .router import Side


def _int(x, name, lo):
    if type(x) is not int or x < lo:
        raise ValueError(f"{name} must be int >= {lo}")


def imbalance_bp(bid_quantity: int, ask_quantity: int) -> int:
    """trunc(10000*(bq-aq)/(bq+aq)), exact integer arithmetic."""
    _int(bid_quantity, "bid_quantity", 1)
    _int(ask_quantity, "ask_quantity", 1)
    num = 10000 * (bid_quantity - ask_quantity)
    den = bid_quantity + ask_quantity
    return num // den if num >= 0 else -((-num) // den)


def reference_signal(book: Book, venue_id: str, now_ns: int) -> int | None:
    """Observable only: the reference venue's current accepted quote, if FRESH."""
    if book.status(venue_id, now_ns) is not FeedStatus.FRESH:
        return None
    q = book.venues[venue_id].quote
    return imbalance_bp(q.bid_quantity, q.ask_quantity)


@dataclass(frozen=True, slots=True)
class PolicyConfig:
    theta_bp: int = 2000
    q_max: int = 5
    max_order_qty: int = 5
    cooldown_steps: int = 0

    def __post_init__(self):
        _int(self.theta_bp, "theta_bp", 0)
        _int(self.q_max, "q_max", 1)
        _int(self.max_order_qty, "max_order_qty", 1)
        _int(self.cooldown_steps, "cooldown_steps", 0)


def target_position(signal_bp: int | None, cfg: PolicyConfig) -> int | None:
    """Strict thresholds: equality maps to 0. None signal -> None (hold)."""
    if signal_bp is None:
        return None
    if signal_bp > cfg.theta_bp:
        return cfg.q_max
    if signal_bp < -cfg.theta_bp:
        return -cfg.q_max
    return 0


@dataclass(frozen=True, slots=True)
class OrderIntent:
    side: Side
    qty: int
    target: int
    signal_bp: int
    step: int


class Strategy:
    """Holds confirmed position and order state; decides at most one order per step."""

    def __init__(self, cfg: PolicyConfig):
        self.cfg = cfg
        self.position = 0                 # confirmed (from booked fills only)
        self.outstanding: str | None = None
        self.halted_unknown: set[str] = set()
        self.last_resolution_step: int | None = None

    def decide(self, signal_bp: int | None, step: int) -> tuple[str, OrderIntent | None]:
        if self.halted_unknown:
            return "halted_unknown", None
        if self.outstanding is not None:
            return "outstanding", None
        if signal_bp is None:
            return "no_signal", None
        if (self.last_resolution_step is not None
                and step - self.last_resolution_step < self.cfg.cooldown_steps):
            return "cooldown", None
        target = target_position(signal_bp, self.cfg)
        delta = target - self.position
        if delta == 0:
            return "at_target", None
        side = Side.BUY if delta > 0 else Side.SELL
        return "order", OrderIntent(side, min(abs(delta), self.cfg.max_order_qty), target, signal_bp, step)

    # Order lifecycle notifications from the execution layer.
    def on_sent(self, order_id: str) -> None:
        assert self.outstanding is None
        self.outstanding = order_id

    def on_resolved(self, order_id: str, signed_fill_qty: int, step: int) -> None:
        """Known outcome (filled qty, or 0 for rejection)."""
        if self.outstanding == order_id:
            self.outstanding = None
        self.halted_unknown.discard(order_id)
        self.position += signed_fill_qty
        self.last_resolution_step = step

    def on_unknown(self, order_id: str) -> None:
        """Ack timed out: halt until reconciliation. The order stays unresolved."""
        self.halted_unknown.add(order_id)
        if self.outstanding == order_id:
            self.outstanding = None       # tracked in halted_unknown instead; still blocks orders
