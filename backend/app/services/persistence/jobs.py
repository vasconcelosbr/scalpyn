from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class OhlcvCandle:
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float


@dataclass(frozen=True)
class IndicatorWrite:
    time: datetime
    timeframe: str
    market_type: str
    indicators_json: str
    scheduler_group: str | None = None


@dataclass(frozen=True)
class MarketMetadataWrite:
    updated_at: datetime
    price: float | None = None
    price_change_24h: float | None = None
    volume_24h: float | None = None
    spread_pct: float | None = None
    orderbook_depth_usdt: float | None = None
    volume_24h_updated_at: datetime | None = None


@dataclass(frozen=True)
class PersistenceJob:
    domain: str
    symbol: str
    market_type: str = "spot"
    exchange: str = "gate.io"
    candles: tuple[OhlcvCandle, ...] = field(default_factory=tuple)
    timeframe: str | None = None
    indicator: IndicatorWrite | None = None
    market_metadata: MarketMetadataWrite | None = None
    context: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        timeframe = self.indicator.timeframe if self.indicator else self.timeframe
        return f"{self.domain}:{self.symbol}:{timeframe or self.market_type}"
