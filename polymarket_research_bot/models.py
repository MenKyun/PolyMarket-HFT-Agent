from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, Iterable, Optional


def now_ms() -> int:
    return int(time.time() * 1000)


@dataclass(slots=True)
class Level:
    price: float
    size: float


@dataclass(slots=True)
class BookState:
    asset_id: str
    bids: Dict[float, float] = field(default_factory=dict)
    asks: Dict[float, float] = field(default_factory=dict)
    last_update_ms: int = 0
    last_event_type: str = ""

    def replace_from_snapshot(self, bids: Iterable[dict], asks: Iterable[dict], ts_ms: int, event_type: str) -> None:
        self.bids = {}
        self.asks = {}
        for item in bids:
            price = float(item["price"])
            size = float(item["size"])
            if size > 0:
                self.bids[price] = size
        for item in asks:
            price = float(item["price"])
            size = float(item["size"])
            if size > 0:
                self.asks[price] = size
        self.last_update_ms = ts_ms
        self.last_event_type = event_type

    def apply_price_level(self, side: str, price: float, size: float, ts_ms: int, event_type: str) -> None:
        target = self.bids if side.upper() == "BUY" else self.asks
        if size <= 0:
            target.pop(price, None)
        else:
            target[price] = size
        self.last_update_ms = ts_ms
        self.last_event_type = event_type

    def set_best_bid_ask(self, best_bid: float, best_ask: float, ts_ms: int, event_type: str) -> None:
        if best_bid > 0:
            self.bids[best_bid] = max(self.bids.get(best_bid, 0.0), 0.0)
        if best_ask > 0:
            self.asks[best_ask] = max(self.asks.get(best_ask, 0.0), 0.0)
        self.last_update_ms = ts_ms
        self.last_event_type = event_type

    @property
    def best_bid(self) -> Optional[Level]:
        if not self.bids:
            return None
        price = max(self.bids)
        return Level(price=price, size=self.bids[price])

    @property
    def best_ask(self) -> Optional[Level]:
        if not self.asks:
            return None
        price = min(self.asks)
        return Level(price=price, size=self.asks[price])

    @property
    def midpoint(self) -> Optional[float]:
        bid = self.best_bid
        ask = self.best_ask
        if not bid or not ask:
            return None
        return (bid.price + ask.price) / 2.0

    @property
    def spread(self) -> Optional[float]:
        bid = self.best_bid
        ask = self.best_ask
        if not bid or not ask:
            return None
        return ask.price - bid.price

    def spread_bps(self) -> Optional[float]:
        mid = self.midpoint
        spread = self.spread
        if mid is None or spread is None or mid <= 0:
            return None
        return (spread / mid) * 10_000.0


@dataclass(slots=True)
class PredictionPoint:
    source: str
    probability_yes: float
    received_at_ms: int
    source_ts_ms: Optional[int]
    source_latency_ms: Optional[int]
    weight: float = 1.0


@dataclass(slots=True)
class ConsensusPrediction:
    probability_yes: float
    valid_sources: int
    disagreement_bps: float
    received_at_ms: int
    slow_ema: float
    fast_ema: float
    regime: str


@dataclass(slots=True)
class TradeIntent:
    asset_id: str
    action: str
    price: float
    shares: float
    edge_bps: float
    rationale: str
    generated_at_ms: int


@dataclass(slots=True)
class FillResult:
    accepted: bool
    reason: str
    filled_shares: float
    avg_price: float
    cash_after: float
    equity_after: float
    filled_at_ms: int


@dataclass(slots=True)
class PositionSnapshot:
    asset_id: str
    shares: float
    mark_price: float
    market_value: float


class EmaPair:
    def __init__(self, fast_alpha: float, slow_alpha: float) -> None:
        self.fast_alpha = fast_alpha
        self.slow_alpha = slow_alpha
        self.fast: Optional[float] = None
        self.slow: Optional[float] = None

    def update(self, value: float) -> tuple[float, float]:
        if self.fast is None:
            self.fast = value
        else:
            self.fast = self.fast_alpha * value + (1.0 - self.fast_alpha) * self.fast
        if self.slow is None:
            self.slow = value
        else:
            self.slow = self.slow_alpha * value + (1.0 - self.slow_alpha) * self.slow
        return self.fast, self.slow


class RollingWindow:
    def __init__(self, maxlen: int) -> None:
        self.items: Deque[float] = deque(maxlen=maxlen)

    def append(self, value: float) -> None:
        self.items.append(value)

    def __len__(self) -> int:
        return len(self.items)

    def stdev(self) -> float:
        if len(self.items) < 2:
            return 0.0
        values = list(self.items)
        mean = sum(values) / len(values)
        variance = sum((x - mean) ** 2 for x in values) / (len(values) - 1)
        return math.sqrt(max(variance, 0.0))
