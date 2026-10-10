"""Bounded instrumentation: fixed-size ring of int samples plus a total count."""
from __future__ import annotations

from array import array


class Samples:
    def __init__(self, capacity: int = 100_000):
        if capacity < 1:
            raise ValueError("capacity must be positive")
        self._buf = array("q", bytes(8 * capacity))
        self._cap = capacity
        self.count = 0          # total observations ever recorded (may exceed capacity)

    def add(self, v: int) -> None:
        self._buf[self.count % self._cap] = v
        self.count += 1

    def values(self) -> list[int]:
        n = min(self.count, self._cap)
        return list(self._buf[:n])

    def summary(self) -> dict:
        """Percentiles over the retained window (the most recent `capacity` samples)."""
        vals = sorted(self.values())
        out = {"count": self.count, "retained": len(vals)}
        if vals:
            for name, p in (("p50", 50), ("p95", 95), ("p99", 99)):
                out[name] = percentile(vals, p)
            out["max"] = vals[-1]
        return out


def percentile(sorted_vals: list[int], p: float) -> int:
    """Nearest-rank percentile of a non-empty sorted list."""
    k = max(0, min(len(sorted_vals) - 1, -(-len(sorted_vals) * p // 100) - 1))
    return sorted_vals[int(k)]


class Anchor:
    """One paired (wall, monotonic) reading taken at observer start.

    UTC for a monotonic instant m is derived as wall_ns + (m - mono_ns). Wall-clock
    steps or NTP slewing after the anchor are not reflected; this is documented,
    derived time, not an independently sampled timestamp.
    """

    def __init__(self, observer: str):
        import time
        self.observer = observer
        self.wall_ns = time.time_ns()
        self.mono_ns = time.monotonic_ns()

    def header(self, schema: str, fields: list[str]) -> dict:
        return {"schema": schema, "observer": self.observer, "anchor_wall_ns": self.wall_ns,
                "anchor_mono_ns": self.mono_ns, "fields": fields}


class Ring:
    """Fixed-capacity ring of tuples; oldest entries are overwritten and counted as dropped."""

    def __init__(self, capacity: int):
        if capacity < 1:
            raise ValueError("capacity must be positive")
        self._buf: list = [None] * capacity
        self._cap = capacity
        self.count = 0

    def add(self, item: tuple) -> None:
        self._buf[self.count % self._cap] = item
        self.count += 1

    @property
    def dropped(self) -> int:
        return max(0, self.count - self._cap)

    def items(self) -> list:
        """Retained items, oldest first."""
        if self.count <= self._cap:
            return self._buf[:self.count]
        i = self.count % self._cap
        return self._buf[i:] + self._buf[:i]
