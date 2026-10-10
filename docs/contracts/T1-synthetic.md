# Contract T1: synthetic quote-event generator

Owner of contract: Claude. Implementer: Hermes (delegated).
Allowed files: `quote_gate/synthetic.py`, `tests/test_synthetic.py`. No others.

## Constraints

- Python 3.12 standard library only. Allowed imports: `random`, `dataclasses`,
  `typing`, `collections.abc`. Tests use `unittest` only (no pytest).
- No networking, filesystem access, subprocesses, environment reads, clocks,
  logging, printing, global state, or model calls.
- Never use the module-level `random` functions; use one local
  `random.Random(seed)` instance per call.
- Independent of transport and routing. Do not import other `quote_gate` modules.

## Public API (exact names)

```python
MAX_EVENTS: int = 1_000_000

@dataclass(frozen=True, slots=True)
class QuoteEvent:
    venue_id: str
    symbol: str
    sequence: int
    bid_ticks: int
    ask_ticks: int
    bid_quantity: int
    ask_quantity: int

@dataclass(frozen=True, slots=True)
class GeneratorConfig:
    venue_id: str = "A"
    symbol: str = "SYN1"
    start_bid_ticks: int = 10000
    max_step_ticks: int = 2
    min_spread_ticks: int = 1
    max_spread_ticks: int = 4
    min_quantity: int = 1
    max_quantity: int = 1000
    start_sequence: int = 1

def iter_quotes(seed: int, count: int,
                config: GeneratorConfig | None = None) -> Iterator[QuoteEvent]: ...

def generate_quotes(seed: int, count: int,
                    config: GeneratorConfig | None = None) -> list[QuoteEvent]: ...
```

`config=None` means `GeneratorConfig()`. `generate_quotes` returns
`list(iter_quotes(seed, count, config))`.

## Validation (all explicit, nothing silently clamped)

"int" below means `type(x) is int` (so `bool` is rejected).

`GeneratorConfig.__post_init__` raises:
- `TypeError` if `venue_id` or `symbol` is not `str`, or any numeric field is not int.
- `ValueError` if `venue_id` or `symbol` is empty,
  `start_bid_ticks < 1`, `max_step_ticks < 0`, `min_spread_ticks < 1`,
  `max_spread_ticks < min_spread_ticks`, `min_quantity < 1`,
  `max_quantity < min_quantity`, or `start_sequence < 1`.

`iter_quotes` raises, **at call time, before returning the iterator**
(i.e. `iter_quotes` itself must not be a generator function):
- `TypeError` if `seed` is not int, `count` is not int, or `config` is
  neither `None` nor a `GeneratorConfig`.
- `ValueError` if `count < 0` or `count > MAX_EVENTS`.

## Generation algorithm (exact draw order; required for reproducibility)

```
rng = random.Random(seed)
bid = config.start_bid_ticks
for i in range(count):
    if i > 0:
        bid = max(1, bid + rng.randint(-max_step_ticks, max_step_ticks))
    spread  = rng.randint(min_spread_ticks, max_spread_ticks)
    bid_qty = rng.randint(min_quantity, max_quantity)
    ask_qty = rng.randint(min_quantity, max_quantity)
    yield QuoteEvent(venue_id, symbol, start_sequence + i,
                     bid, bid + spread, bid_qty, ask_qty)
```

When `max_step_ticks == 0`, `rng.randint(0, 0)` is still called (it consumes no
randomness in CPython but keep the call for uniformity).

## Guaranteed properties

- Same `(seed, count, config)` gives identical output within CPython 3.12.
- Exactly `count` events; `count == 0` gives an empty result.
- `sequence` is `start_sequence, start_sequence + 1, ...` (strictly +1).
- `min_spread_ticks <= ask_ticks - bid_ticks <= max_spread_ticks`, so never crossed or locked.
- `bid_ticks >= 1`; all prices and quantities are `int`; quantities in
  `[min_quantity, max_quantity]`.
- A prefix property holds: `generate_quotes(s, n)[:k] == generate_quotes(s, k)` for `k <= n`.

## Required tests in `tests/test_synthetic.py`

1. Same seed + config gives identical events; different seeds differ (for count 50).
2. Output length equals requested count (0, 1, 1000).
3. Sequence numbers start at `start_sequence` and increase by exactly 1.
4. Spread rule holds for every event; bid >= 1.
5. Quantities are positive `int`s within configured bounds.
6. Invalid arguments raise the specified exception type (including bool seed/count,
   negative count, count > MAX_EVENTS, each invalid config field).
7. `count == 0` returns `[]`.
8. Validation is eager: `iter_quotes(0, -1)` raises without calling `next()`.

Run with: `python3 -m unittest discover -s tests -t .`

## Deliverable

Only the two files above. A short report (under 15 lines): what was
implemented, test command output, any deviation from this contract.
