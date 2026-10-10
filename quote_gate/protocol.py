"""Newline-delimited JSON framing shared by venues, aggregator and clients.

One JSON object per line, UTF-8, at most MAX_FRAME bytes including the newline.
An oversized or undecodable frame is a protocol violation: the receiver closes
the connection (it cannot resynchronise reliably). A frame that decodes but has
the wrong shape is counted and rejected without closing.
"""
from __future__ import annotations

import asyncio
import json

from .book import Quote

MAX_FRAME = 4096
QUOTE_FIELDS = ("venue_id", "symbol", "sequence", "bid_ticks", "ask_ticks",
                "bid_quantity", "ask_quantity")


class FrameError(Exception):
    """Unrecoverable framing error; close the connection."""


def encode(obj: dict) -> bytes:
    data = json.dumps(obj, separators=(",", ":")).encode() + b"\n"
    if len(data) > MAX_FRAME:
        raise FrameError("frame exceeds MAX_FRAME")
    return data


def encode_quote(q: Quote) -> bytes:
    return encode({"type": "quote", **{f: getattr(q, f) for f in QUOTE_FIELDS}})


async def read_frame(reader: asyncio.StreamReader) -> dict | None:
    """Return the next decoded object, or None on clean EOF.

    The reader must be created with ``limit=MAX_FRAME`` so oversize lines fail
    without unbounded buffering.
    """
    try:
        line = await reader.readuntil(b"\n")
    except asyncio.IncompleteReadError as e:
        if e.partial:
            raise FrameError("truncated frame at EOF") from None
        return None
    except asyncio.LimitOverrunError:
        raise FrameError("frame exceeds MAX_FRAME") from None
    try:
        obj = json.loads(line)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise FrameError("undecodable frame") from None
    if not isinstance(obj, dict):
        raise FrameError("frame is not a JSON object")
    return obj


def quote_from_obj(obj: dict) -> Quote | None:
    """Shape check only (field presence); value rules live in book.validate_quote."""
    if obj.get("type") != "quote" or len(obj) != len(QUOTE_FIELDS) + 1:
        return None
    try:
        return Quote(*(obj[f] for f in QUOTE_FIELDS))
    except KeyError:
        return None
