"""Typed read-only contract for the Shadow Portfolio profile monitor."""

from datetime import date
from typing import Dict, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel


ProfileTrend = Literal["IMPROVING", "STABLE", "DETERIORATING", "INSUFFICIENT_DATA"]
ProfileMonitorStatus = Literal[
    "POSITIVE",
    "STABLE",
    "ATTENTION",
    "DETERIORATING",
    "LOW_SAMPLE",
]
ProfileDailyRange = Literal["7d", "15d", "30d", "90d", "total"]


class ProfileTrendEvidence(BaseModel):
    points: int
    slope: float
    net_change: float
    positive_days: int
    negative_days: int


class ProfilePerformanceHistoryPoint(BaseModel):
    date: date
    trades: int
    closed_trades: int
    tp: int
    sl: int
    timeout: int
    ev_score: float
    win_rate: Optional[float] = None
    pnl_usdt: float
    holding_seconds: Optional[float] = None


class ProfileDailyPerformancePoint(BaseModel):
    date: date
    closed_trades: int
    wins: int  # TP_HIT count
    win_rate: Optional[float] = None  # TP_HIT / (TP_HIT + SL_HIT)
    pnl_usdt: float


class ProfileDailyPerformanceResponse(BaseModel):
    contract_version: str
    as_of: date
    range: ProfileDailyRange
    timezone: str
    points: List[ProfileDailyPerformancePoint]
    metric_definitions: Dict[str, str]


class ProfilePerformanceRow(BaseModel):
    rank: int
    profile_id: UUID
    profile_name: str
    watchlist_name: Optional[str] = None
    trades: int
    closed_trades: int
    open_trades: int
    tp: int
    sl: int
    timeout: int
    ev_score: float
    ev_delta: Optional[float] = None
    win_rate: Optional[float] = None
    win_rate_delta_pp: Optional[float] = None
    pnl_day_usdt: float
    pnl_period_usdt: float
    avg_pnl_pct: Optional[float] = None
    holding_seconds: Optional[float] = None
    trend: ProfileTrend
    trend_evidence: ProfileTrendEvidence
    sample_status: str
    status: ProfileMonitorStatus
    priority: str
    priority_reason: str
    history: List[ProfilePerformanceHistoryPoint]


class ProfilePerformanceSummary(BaseModel):
    active_profiles: int
    ev_score_mean: Optional[float] = None
    ev_score_delta: Optional[float] = None
    win_rate: Optional[float] = None
    win_rate_delta_pp: Optional[float] = None
    pnl_day_usdt: float
    pnl_period_usdt: float
    trades_period: int
    closed_trades_period: int
    alerts: int


class ProfilePerformanceHighlight(BaseModel):
    profile_id: UUID
    profile_name: str
    ev_score: float
    ev_delta: Optional[float] = None
    ev_period_change: Optional[float] = None
    win_rate: Optional[float] = None
    pnl_period_usdt: float


class ProfilePerformanceHighlights(BaseModel):
    best_profile: Optional[ProfilePerformanceHighlight] = None
    biggest_improvement: Optional[ProfilePerformanceHighlight] = None
    biggest_deterioration: Optional[ProfilePerformanceHighlight] = None
    highest_pnl: Optional[ProfilePerformanceHighlight] = None


class ProfilePerformanceResponse(BaseModel):
    contract_version: str
    as_of: date
    range_days: int
    timezone: str
    available_from: Optional[date] = None
    available_to: Optional[date] = None
    summary: ProfilePerformanceSummary
    highlights: ProfilePerformanceHighlights
    profiles: List[ProfilePerformanceRow]
    metric_definitions: Dict[str, str]
