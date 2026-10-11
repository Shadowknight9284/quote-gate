"""Fill check: reconcile a broker's fill export against a bot's own trade log (two CSVs).

Pure and stdlib-only; nothing is stored. Prices, quantities and fees are exact decimals. Economics
come from the broker file (authoritative) replayed through ``ledger.Ledger`` for FIFO realized P&L,
with prices and quantities scaled to integers; fees are summed separately so rebates (negative fees)
are allowed.

Checks: repeated identical rows (likely booked twice), orders the broker filled that the bot never
recorded, orders the bot recorded that the broker has no record of, per-order quantity / notional /
fee / side mismatches, and an optional position limit.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation

from .ledger import Fill, Ledger

FIELDS = ("order_id", "side", "quantity", "price", "fee", "time")
REQUIRED = ("order_id", "quantity", "price")
MAX_BYTES = 2_000_000
MAX_ROWS = 50_000
MAX_ISSUES = 200

# Header synonyms, compared after lowercasing and dropping non-alphanumerics; earlier entries win.
SYNONYMS = {
    "order_id": ("orderid", "clientorderid", "clordid", "clientid", "ordid", "order", "tradeid", "fillid", "execid", "id"),
    "side": ("side", "action", "buysell", "direction", "dir", "bs"),
    "quantity": ("quantity", "qty", "filledqty", "size", "filledsize", "shares", "amount", "lastqty", "execqty"),
    "price": ("price", "fillprice", "avgprice", "averageprice", "executionprice", "execprice", "lastpx", "px"),
    "fee": ("fee", "fees", "commission", "commissions", "feeamount", "feeusd"),
    "time": ("time", "timestamp", "ts", "transactiontime", "filledat", "createdat", "executedat", "datetime", "date"),
}
BUY = {"buy", "b", "bought"}
SELL = {"sell", "s", "sold"}

CAUSES = {
    "booked_twice": "The same fill appears more than once. Typical when a resent order or a duplicated "
                    "execution report is booked again. Key the ledger by order or fill ID and ignore repeats.",
    "missing_from_bot": "The broker executed this order but the bot never recorded it. Typical after a lost "
                        "confirmation or a disconnect. After a timeout, query the order's status instead of "
                        "assuming it failed.",
    "not_at_broker": "The bot recorded a fill the broker has no record of. Typical when a timed-out order is "
                     "assumed filled, or when test or paper fills mix into the log.",
    "mismatch": "The order exists in both files but the details differ. Check partial fills, fee rounding, "
                "and price or quantity units.",
    "position_limit": "The broker's running position went past the limit you set.",
}


class FillCheckError(ValueError):
    """Input that cannot be checked at all (too large, missing required columns)."""


@dataclass(frozen=True)
class Row:
    line: int
    order_id: str
    signed_qty: Decimal
    price: Decimal
    fee: Decimal
    time: str | None


@dataclass
class ParsedLog:
    headers: list
    mapping: dict
    rows: list = field(default_factory=list)
    invalid: list = field(default_factory=list)       # [line, reason]


def _norm(h: str) -> str:
    return re.sub(r"[^a-z0-9]", "", h.lower())


def detect_mapping(headers: list[str]) -> dict:
    """Field -> header (or None) using SYNONYMS; each header is used at most once."""
    normed = {_norm(h): h for h in headers}
    used, out = set(), {}
    for f in FIELDS:
        out[f] = None
        for syn in SYNONYMS[f]:
            h = normed.get(syn)
            if h is not None and h not in used:
                out[f] = h
                used.add(h)
                break
    return out


def _decimal(text: str) -> Decimal:
    s = text.strip().replace(",", "").replace("$", "")
    if not s:
        raise ValueError("empty")
    try:
        d = Decimal(s)
    except InvalidOperation:
        raise ValueError(f"not a number: {text.strip()!r}") from None
    if not d.is_finite():
        raise ValueError("not a finite number")
    return d


def _reader(text: str):
    if len(text.encode()) > MAX_BYTES:
        raise FillCheckError(f"file is larger than {MAX_BYTES // 1_000_000} MB")
    return csv.reader(io.StringIO(text.lstrip("﻿")))


def read_headers(text: str) -> list[str]:
    return [h.strip() for h in next(_reader(text), [])]


def parse(text: str, mapping: dict | None = None) -> ParsedLog:
    """Parse one CSV. Bad rows are reported in ``invalid`` and skipped, never fatal."""
    rd = _reader(text)
    headers = [h.strip() for h in next(rd, [])]
    if not headers:
        raise FillCheckError("the file is empty")
    m = detect_mapping(headers) if mapping is None else {f: mapping.get(f) or None for f in FIELDS}
    missing = [f for f in REQUIRED if not m.get(f)]
    if missing:
        raise FillCheckError(f"no column mapped for: {', '.join(missing)}")
    unknown = [h for h in m.values() if h and h not in headers]
    if unknown:
        raise FillCheckError(f"mapped column not in the file: {', '.join(unknown)}")
    idx = {f: headers.index(h) for f, h in m.items() if h}
    log = ParsedLog(headers, m)
    for n, rec in enumerate(rd, start=2):
        if not any(c.strip() for c in rec):
            continue
        if len(log.rows) + len(log.invalid) >= MAX_ROWS:
            raise FillCheckError(f"more than {MAX_ROWS:,} rows")
        get = lambda f: rec[idx[f]].strip() if f in idx and idx[f] < len(rec) else ""
        try:
            oid = get("order_id")
            if not oid:
                raise ValueError("missing order ID")
            qty, price = _decimal(get("quantity")), _decimal(get("price"))
            fee = _decimal(get("fee")) if get("fee") else Decimal(0)
            if price <= 0:
                raise ValueError("price must be positive")
            if qty == 0:
                raise ValueError("quantity is zero")
            if "side" in idx:
                side = get("side").lower()
                if side in BUY:
                    signed = abs(qty)
                elif side in SELL:
                    signed = -abs(qty)
                else:
                    raise ValueError(f"unknown side {get('side')!r}")
            else:
                signed = qty                     # no side column: the quantity's sign is the side
        except (ValueError, InvalidOperation) as e:
            log.invalid.append([n, str(e) or "unreadable number"])
            continue
        log.rows.append(Row(n, oid, signed, price, fee, get("time") or None))
    return log


def _key(r: Row):
    return (r.order_id, r.signed_qty, r.price, r.fee, r.time)


def _split_repeats(rows):
    """(first occurrences, {key: [lines]} for keys seen more than once)."""
    seen, firsts, repeats = {}, [], {}
    for r in rows:
        k = _key(r)
        if k in seen:
            repeats.setdefault(k, [seen[k]]).append(r.line)
        else:
            seen[k] = r.line
            firsts.append(r)
    return firsts, repeats


def _by_order(rows) -> dict:
    out = {}
    for r in rows:
        a = out.setdefault(r.order_id, {"qty": Decimal(0), "notional": Decimal(0), "fee": Decimal(0),
                                        "rows": 0, "line": r.line})
        a["qty"] += r.signed_qty
        a["notional"] += abs(r.signed_qty) * r.price
        a["fee"] += r.fee
        a["rows"] += 1
    return out


def _s(d: Decimal) -> str:
    """Exact decimal as plain text (no exponent)."""
    if d == d.to_integral():
        return str(d.quantize(Decimal(1)))
    return format(d.normalize(), "f")


def _chronological(rows):
    """Rows sorted by parsed ISO time when every row has one, else file order."""
    try:
        times = [datetime.fromisoformat(r.time) for r in rows] if rows and all(r.time for r in rows) else None
    except ValueError:
        times = None
    if times is None:
        return list(rows), "file order"
    try:
        order = sorted(range(len(rows)), key=lambda i: times[i])
    except TypeError:                            # mixed timezone-aware and naive times
        return list(rows), "file order"
    return [rows[i] for i in order], "time column"


def _economics(rows, max_position: Decimal | None, mark: Decimal | None) -> dict:
    """Replay authoritative fills through the FIFO ledger, scaled to exact integers."""
    if not rows:
        return {"fills": 0}
    dp = max(-r.price.as_tuple().exponent for r in rows if r.price.as_tuple().exponent < 0) \
        if any(r.price.as_tuple().exponent < 0 for r in rows) else 0
    dq = max(-r.signed_qty.as_tuple().exponent for r in rows if r.signed_qty.as_tuple().exponent < 0) \
        if any(r.signed_qty.as_tuple().exponent < 0 for r in rows) else 0
    ps, qs = Decimal(10) ** dp, Decimal(10) ** dq
    ordered, order_source = _chronological(rows)
    led = Ledger(max_position=10 ** 30)          # limit is checked below and reported, not enforced
    pos, max_abs, breaches = Decimal(0), Decimal(0), []
    for i, r in enumerate(ordered):
        led.apply(Fill(f"r{i}", r.order_id, "broker", int(r.signed_qty * qs), int(r.price * ps), 0, i, i))
        pos += r.signed_qty
        max_abs = max(max_abs, abs(pos))
        if max_position is not None and abs(pos) > max_position:
            breaches.append({"line": r.line, "order_id": r.order_id, "position": _s(pos)})
    scale = ps * qs
    fees = sum((r.fee for r in rows), Decimal(0))
    realized = Decimal(led.realized_gross_ticks) / scale
    out = {"fills": len(rows), "order_source": order_source, "final_position": _s(pos),
           "max_abs_position": _s(max_abs), "realized_pre_fee": _s(realized), "fees": _s(fees),
           "realized_after_fees": _s(realized - fees), "limit": None if max_position is None else _s(max_position),
           "limit_breaches": len(breaches), "breaches": breaches[:20]}
    if mark is not None and pos != 0:
        out["mark"] = _s(mark)
        out["unrealized_at_mark"] = _s(Decimal(led.unrealized_ticks(int(mark * ps))) / scale)
    return out


def check(broker: ParsedLog, bot: ParsedLog, max_position: Decimal | None = None,
          mark: Decimal | None = None) -> dict:
    """Compare the two parsed logs. The broker file is treated as authoritative."""
    b_rows, b_rep = _split_repeats(broker.rows)
    t_rows, t_rep = _split_repeats(bot.rows)
    b_orders, t_orders = _by_order(b_rows), _by_order(t_rows)
    issues = []

    def add(kind, oid, detail, line=None):
        if len(issues) < MAX_ISSUES:
            issues.append({"kind": kind, "order_id": oid, "detail": detail, "line": line, "cause": CAUSES[kind]})

    for (oid, *_), lines in t_rep.items():
        add("booked_twice", oid, f"appears {len(lines)} times in the bot log (lines {', '.join(map(str, lines))})",
            lines[0])
    for (oid, *_), lines in b_rep.items():
        add("booked_twice", oid, f"appears {len(lines)} times in the broker file (lines "
                                 f"{', '.join(map(str, lines))}); without a time column, identical partial "
                                 "fills look the same", lines[0])
    matched = 0
    for oid, b in b_orders.items():
        t = t_orders.get(oid)
        if t is None:
            add("missing_from_bot", oid, f"broker filled {_s(b['qty'])} at {_s(b['notional'] / abs(b['qty']))} "
                                         f"(fee {_s(b['fee'])}); not in the bot log", b["line"])
            continue
        diffs = []
        if b["qty"] != t["qty"]:
            diffs.append(f"quantity broker {_s(b['qty'])} vs bot {_s(t['qty'])}")
        elif b["notional"] != t["notional"]:
            diffs.append(f"average price broker {_s(b['notional'] / abs(b['qty']))} vs bot "
                         f"{_s(t['notional'] / abs(t['qty']))}")
        if b["fee"] != t["fee"]:
            diffs.append(f"fee broker {_s(b['fee'])} vs bot {_s(t['fee'])}")
        if diffs:
            add("mismatch", oid, "; ".join(diffs), t["line"])
        else:
            matched += 1
    for oid, t in t_orders.items():
        if oid not in b_orders:
            add("not_at_broker", oid, f"bot recorded {_s(t['qty'])} at {_s(t['notional'] / abs(t['qty']))}; "
                                      "the broker has no fill with this ID", t["line"])

    econ = _economics(b_rows, max_position, mark)
    for br in econ.get("breaches", []):
        add("position_limit", br["order_id"], f"position {br['position']} after this fill "
                                              f"(limit ±{econ['limit']})", br["line"])
    counts = {k: sum(1 for i in issues if i["kind"] == k) for k in CAUSES}
    counts["position_limit"] = econ.get("limit_breaches", 0)
    bot_position = sum((r.signed_qty for r in bot.rows), Decimal(0))     # what the bot's own books hold
    return {
        "summary": {"broker_orders": len(b_orders), "bot_orders": len(t_orders), "matched": matched,
                    **counts, "invalid_rows": len(broker.invalid) + len(bot.invalid),
                    "clean": not any(counts.values())},
        "positions": {"broker": econ.get("final_position", "0"), "bot": _s(bot_position),
                      "agree": econ.get("final_position", "0") == _s(bot_position)},
        "economics": econ,
        "issues": issues,
        "issues_truncated": len(issues) >= MAX_ISSUES,
        "invalid": {"broker": broker.invalid[:50], "bot": bot.invalid[:50]},
        "mapping": {"broker": broker.mapping, "bot": bot.mapping},
        "note": "Broker file treated as authoritative. Realized P&L is FIFO over the broker's fills; "
                "open inventory is valued only if you give a mark price.",
    }
