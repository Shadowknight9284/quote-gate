import random
from dataclasses import dataclass
from collections.abc import Iterator


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

    def __post_init__(self):
        # Validate types
        if type(self.venue_id) is not str:
            raise TypeError("venue_id must be a string")
        if type(self.symbol) is not str:
            raise TypeError("symbol must be a string")
        if type(self.start_bid_ticks) is not int:
            raise TypeError("start_bid_ticks must be an integer")
        if type(self.max_step_ticks) is not int:
            raise TypeError("max_step_ticks must be an integer")
        if type(self.min_spread_ticks) is not int:
            raise TypeError("min_spread_ticks must be an integer")
        if type(self.max_spread_ticks) is not int:
            raise TypeError("max_spread_ticks must be an integer")
        if type(self.min_quantity) is not int:
            raise TypeError("min_quantity must be an integer")
        if type(self.max_quantity) is not int:
            raise TypeError("max_quantity must be an integer")
        if type(self.start_sequence) is not int:
            raise TypeError("start_sequence must be an integer")

        # Validate values
        if len(self.venue_id) == 0:
            raise ValueError("venue_id cannot be empty")
        if len(self.symbol) == 0:
            raise ValueError("symbol cannot be empty")
        if self.start_bid_ticks < 1:
            raise ValueError("start_bid_ticks must be >= 1")
        if self.max_step_ticks < 0:
            raise ValueError("max_step_ticks must be >= 0")
        if self.min_spread_ticks < 1:
            raise ValueError("min_spread_ticks must be >= 1")
        if self.max_spread_ticks < self.min_spread_ticks:
            raise ValueError("max_spread_ticks must be >= min_spread_ticks")
        if self.min_quantity < 1:
            raise ValueError("min_quantity must be >= 1")
        if self.max_quantity < self.min_quantity:
            raise ValueError("max_quantity must be >= min_quantity")
        if self.start_sequence < 1:
            raise ValueError("start_sequence must be >= 1")


def _generate(seed: int, count: int, config: GeneratorConfig) -> Iterator[QuoteEvent]:
    # Create RNG instance
    rng = random.Random(seed)

    # Generate quotes
    bid = config.start_bid_ticks
    for i in range(count):
        if i > 0:
            bid = max(1, bid + rng.randint(-config.max_step_ticks, config.max_step_ticks))
        spread = rng.randint(config.min_spread_ticks, config.max_spread_ticks)
        bid_qty = rng.randint(config.min_quantity, config.max_quantity)
        ask_qty = rng.randint(config.min_quantity, config.max_quantity)
        yield QuoteEvent(
            config.venue_id,
            config.symbol,
            config.start_sequence + i,
            bid,
            bid + spread,
            bid_qty,
            ask_qty
        )


def iter_quotes(seed: int, count: int, config: GeneratorConfig | None = None) -> Iterator[QuoteEvent]:
    # Validate inputs
    if type(seed) is not int:
        raise TypeError("seed must be an integer")
    if type(count) is not int:
        raise TypeError("count must be an integer")
    if config is not None and type(config) is not GeneratorConfig:
        raise TypeError("config must be None or a GeneratorConfig instance")
    if count < 0:
        raise ValueError("count must be >= 0")
    if count > MAX_EVENTS:
        raise ValueError(f"count must be <= {MAX_EVENTS}")

    # Set default config if none provided
    if config is None:
        config = GeneratorConfig()

    # Return the generator
    return _generate(seed, count, config)


def generate_quotes(seed: int, count: int, config: GeneratorConfig | None = None) -> list[QuoteEvent]:
    return list(iter_quotes(seed, count, config))
