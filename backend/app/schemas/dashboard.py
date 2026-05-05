"""Pydantic schemas for the operational dashboard endpoints (Task #224)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional

from pydantic import BaseModel


class HealthResponse(BaseModel):
    rows_window: int
    distinct_symbols: int
    last_candle: Optional[datetime]
    delay_seconds: Optional[float]
    status: str  # "ok" | "warn" | "critical" | "unknown"
    status_label: str  # "Pipeline saudável" | "Atrasado" | "Parado" | "Sem dados"


class SystemStatusResponse(BaseModel):
    redis_alive: bool
    redis_error: Optional[str] = None
    last_ohlcv_ts: Optional[datetime] = None
    last_ohlcv_age_seconds: Optional[float] = None
    last_decision_ts: Optional[datetime] = None
    last_decision_age_seconds: Optional[float] = None
    last_pipeline_scan_ts: Optional[datetime] = None
    last_pipeline_scan_age_seconds: Optional[float] = None


class OhlcvBucket(BaseModel):
    bucket: datetime
    candles: int


class OhlcvRateResponse(BaseModel):
    window_minutes: int
    timeframe: str
    total_candles: int
    buckets: List[OhlcvBucket]


class ScoreBucket(BaseModel):
    bucket: str  # "0-20", "20-40", ...
    count: int


class BlockReason(BaseModel):
    reason: str
    count: int


class DecisionsResponse(BaseModel):
    window_hours: int
    total: int
    allow: int
    block: int
    allow_rate: float  # 0..1
    avg_score: Optional[float]
    score_distribution: List[ScoreBucket]
    top_block_reasons: List[BlockReason]


class TradesAggResponse(BaseModel):
    window_days: int
    total: int
    win_rate: Optional[float]  # 0..1
    avg_pnl_pct: Optional[float]
    avg_holding_seconds: Optional[float]
    cumulative_pnl: List[dict]  # [{time: iso, cumulative_pnl_pct: float}]


class TradeComparisonItem(BaseModel):
    kind: str  # "real" | "simulated"
    total: int
    win_rate: Optional[float]
    avg_pnl_pct: Optional[float]


class TradeComparisonResponse(BaseModel):
    window_days: int
    items: List[TradeComparisonItem]


class MlDatasetItem(BaseModel):
    id: str
    symbol: str
    direction: str
    decision_type: str
    result: str
    time_to_result: Optional[int]
    entry_price: float
    exit_price: Optional[float]
    timestamp_entry: datetime


class MlDatasetResponse(BaseModel):
    total: int
    items: List[MlDatasetItem]
