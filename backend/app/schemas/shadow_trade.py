"""Pydantic schemas para os endpoints `/api/shadow-trades` (Fase 5).

Separados do modelo SQLAlchemy ``ShadowTrade`` para isolar a forma do
contrato HTTP do schema físico do banco. Mudanças DDL não devem
quebrar o frontend silenciosamente — qualquer campo novo precisa
passar por aqui explicitamente.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel


class ShadowTradeRead(BaseModel):
    """Item da listagem paginada — payload enxuto para tabelas."""

    id: UUID
    symbol: str
    direction: Optional[str] = None
    entry_price: Optional[float] = None
    current_price: Optional[float] = None
    tp_price: Optional[float] = None
    sl_price: Optional[float] = None
    amount_usdt: float
    outcome: Optional[str] = None  # TP_HIT | SL_HIT | TIMEOUT | None (pending)
    pnl_pct: Optional[float] = None
    pnl_usdt: Optional[float] = None
    status: str  # PENDING | RUNNING | COMPLETED | ERROR
    skip_reason: Optional[str] = None
    holding_seconds: Optional[int] = None
    created_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class ShadowTradeDetail(ShadowTradeRead):
    """Detalhe completo — adiciona snapshots para a página de drill-down."""

    strategy: Optional[str] = None
    entry_timestamp: Optional[datetime] = None
    exit_price: Optional[float] = None
    exit_timestamp: Optional[datetime] = None
    tp_pct: Optional[float] = None
    sl_pct: Optional[float] = None
    timeout_candles: Optional[int] = None
    decision_id: Optional[int] = None
    last_processed_time: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    config_snapshot: Optional[Dict[str, Any]] = None
    features_snapshot: Optional[Dict[str, Any]] = None
    # Snapshot dos indicadores no momento da SAÍDA (TP/SL/TIMEOUT).
    # NULL para shadows ainda em RUNNING ou criados antes da migration 051.
    features_snapshot_exit: Optional[Dict[str, Any]] = None
    # Auditoria L1/L2/L3 — replicado da decision_log original (espelha
    # o painel de drill-down em /decisions). Preenchido apenas quando
    # ``decision_id`` aponta para uma linha existente em ``decisions_log``.
    decision_strategy: Optional[str] = None
    decision_score: Optional[float] = None
    decision_decision: Optional[str] = None
    decision_event_type: Optional[str] = None
    decision_l1_pass: Optional[bool] = None
    decision_l2_pass: Optional[bool] = None
    decision_l3_pass: Optional[bool] = None
    decision_latency_ms: Optional[int] = None
    decision_created_at: Optional[datetime] = None
    decision_reasons: Optional[Dict[str, Any]] = None
    decision_metrics: Optional[Dict[str, Any]] = None


class ShadowTradeListResponse(BaseModel):
    items: List[ShadowTradeRead]
    total: int
    page: int
    page_size: int


class ShadowTradePricesResponse(BaseModel):
    """Lookup leve de preços correntes para refresh sem repaginar.

    `prices` mapeia symbol → último close 1m em USDT. Símbolos sem
    candle recente são omitidos (frontend mantém o último valor conhecido
    ou exibe '—').
    """

    prices: Dict[str, float]
    fetched_at: datetime


class ShadowTradeSummary(BaseModel):
    total: int
    pending: int  # status IN (PENDING, RUNNING)
    completed: int  # status='COMPLETED'
    win: int  # outcome='TP_HIT'
    loss: int  # outcome='SL_HIT'
    timeout: int  # outcome='TIMEOUT'
    win_rate: float  # win / completed × 100 (0 se completed=0)
    total_pnl_usdt: float
    avg_pnl_pct: float
    period_start: Optional[datetime] = None
    period_end: Optional[datetime] = None
