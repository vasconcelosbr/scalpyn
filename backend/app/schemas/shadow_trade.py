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
    # Necessário para o frontend calcular holding "ao vivo" enquanto a
    # operação está em RUNNING (created_at ≈ entry_timestamp para
    # shadows novos, mas legados podem ter delta).
    entry_timestamp: Optional[datetime] = None

    # Market-context (migration 052). Preenchidos pelo monitor após
    # resolver a entrada; ficam None em shadows legados sem backfill.
    btc_price_at_entry: Optional[float] = None
    btc_change_1h_pct: Optional[float] = None
    funding_rate_at_entry: Optional[float] = None
    n_concurrent_signals: Optional[int] = None

    # MAE/MFE (migration 062, Fase Quant 1). None em trades ainda abertos
    # ou criados antes desta migration. Observacional — não afeta inferência.
    mae_pct: Optional[float] = None
    mfe_pct: Optional[float] = None
    max_drawdown_pct: Optional[float] = None
    max_profit_pct: Optional[float] = None


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
    # Task #316 — pair entry/exit flat snapshots para o painel
    # lado-a-lado com deltas. ``entry_metrics`` é o ``features_snapshot``
    # já achatado (ou ``flatten_entry_snapshot(decision.metrics["indicators_snapshot"])``
    # como fallback). ``exit_metrics`` mira ``features_snapshot_exit``
    # quando este é flat — quando o marcador ``_capture_failed`` está
    # presente, o frontend usa ``exit_snapshotEmptyMessage`` para
    # renderizar a razão.
    entry_metrics: Optional[Dict[str, Any]] = None
    exit_metrics: Optional[Dict[str, Any]] = None

    # Campos de preço extremo e snapshot rico (migration 062, Fase Quant 1+2).
    min_price_post_entry: Optional[float] = None
    max_price_post_entry: Optional[float] = None
    exit_metrics_json: Optional[Dict[str, Any]] = None

    # Strategy Lab fields (migration 077) — null for non-lab shadows.
    profile_id: Optional[UUID] = None
    profile_version: Optional[datetime] = None
    profile_name: Optional[str] = None
    strategy_type: Optional[str] = None
    rules_snapshot: Optional[Dict[str, Any]] = None
    ml_probability: Optional[float] = None
    ml_model_id: Optional[UUID] = None
    final_priority_score: Optional[float] = None


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


class OutcomeMetrics(BaseModel):
    """Métricas agregadas por outcome (TP_HIT / SL_HIT / TIMEOUT)."""

    count: int
    rate_pct: float                      # count / total_completed * 100
    avg_pnl_pct: Optional[float] = None
    avg_holding_seconds: Optional[float] = None
    avg_mae_pct: Optional[float] = None  # avg MAE para este grupo
    avg_mfe_pct: Optional[float] = None  # avg MFE para este grupo


class ShadowTradeAnalytics(BaseModel):
    """Analytics segmentado por outcome — Fase Quant 3.

    Alimentado por ``GET /api/shadow-trades/analytics``.
    Contém taxas por outcome, holding times, MAE/MFE médios por grupo,
    e análise de recovery para trades vencedores que passaram por drawdown.

    Todos os campos de MAE/MFE são None quando ainda não há dados
    suficientes (migration 062 não preenchida retroativamente).
    """

    total_completed: int
    tp: OutcomeMetrics
    sl: OutcomeMetrics
    timeout: OutcomeMetrics

    # MAE/MFE cross-outcome (requer migration 062)
    avg_mae_winners: Optional[float] = None   # avg MAE dos TP_HIT
    avg_mfe_winners: Optional[float] = None   # avg MFE dos TP_HIT
    avg_mae_losers: Optional[float] = None    # avg MAE dos SL_HIT
    avg_mfe_losers: Optional[float] = None    # avg MFE dos SL_HIT

    # Recovery analysis — % de trades com comportamento específico
    near_sl_winners_pct: Optional[float] = None   # TP_HIT com mae_pct < -2%
    sl_after_strong_mfe_pct: Optional[float] = None  # SL_HIT com mfe_pct > 1%
    avg_recovery_pct: Optional[float] = None         # avg(mfe_pct - mae_pct) em TP_HIT

    period_start: Optional[datetime] = None
    period_end: Optional[datetime] = None


class TimeoutPostAnalysis(BaseModel):
    """Fase Quant — Timeout Post-Analysis (migration 063).

    Alimentado por ``GET /api/shadow-trades/timeout-analysis``.
    Todas as métricas são puramente observacionais: outcomes originais
    não são alterados.
    """

    total_timeouts: int
    analyzed: int                        # timeout_post_analysis_done=TRUE
    pending_analysis: int                # ainda não processados

    # Recovery
    delayed_tp_count: int
    timeout_recovery_rate_pct: float     # delayed_tp_count / analyzed * 100

    # Delayed TP timing
    avg_delayed_tp_hours: Optional[float] = None
    median_delayed_tp_hours: Optional[float] = None

    # Excursão pós-timeout
    avg_mfe_after_timeout_pct: Optional[float] = None
    avg_mae_after_timeout_pct: Optional[float] = None

    # Variação de preço média por horizonte (vs entry_price)
    avg_price_change_1h_pct: Optional[float] = None
    avg_price_change_2h_pct: Optional[float] = None
    avg_price_change_4h_pct: Optional[float] = None
    avg_price_change_12h_pct: Optional[float] = None
    avg_price_change_24h_pct: Optional[float] = None

    period_start: Optional[datetime] = None
    period_end: Optional[datetime] = None


class HoldingTimeAnalytics(BaseModel):
    """Fase Quant — Holding Time Validation (Fase 2).

    Alimentado por ``GET /api/shadow-trades/timeout-analysis``
    (campo aninhado ``holding_time``).
    """

    avg_holding_tp_seconds: Optional[float] = None
    avg_holding_sl_seconds: Optional[float] = None
    avg_holding_timeout_seconds: Optional[float] = None
    avg_holding_delayed_tp_seconds: Optional[float] = None

    # Winners lentos: TP_HIT com holding acima da mediana e mae_pct < -2%
    slow_winners_count: Optional[int] = None
    slow_winners_pct: Optional[float] = None

    # Winners rápidos: TP_HIT com holding abaixo da mediana e mfe_pct > 1%
    fast_winners_count: Optional[int] = None
    fast_winners_pct: Optional[float] = None

    # Fake momentum: SL_HIT com mfe_pct > 1% antes da reversão
    fake_momentum_count: Optional[int] = None
    fake_momentum_pct: Optional[float] = None


class TimeoutAnalyticsResponse(BaseModel):
    """Resposta agregada do endpoint timeout-analysis (Fases 1+2)."""

    timeout_post_analysis: TimeoutPostAnalysis
    holding_time: HoldingTimeAnalytics
