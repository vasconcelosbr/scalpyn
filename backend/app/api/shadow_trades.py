"""Shadow Portfolio API — read-only endpoints para a aba Shadow Trade.

Expõe três endpoints sob `/api/shadow-trades`:

* ``GET /api/shadow-trades`` — listagem paginada com filtros
  (status, symbol, min_date, max_date).
* ``GET /api/shadow-trades/summary`` — agregado single-query
  (``COUNT(*) FILTER (WHERE …)``) com win_rate / pnl total / pnl médio
  para o range filtrado.
* ``GET /api/shadow-trades/{id}`` — detalhe + ``features_snapshot`` /
  ``config_snapshot`` completos. 404 se a row não pertencer ao
  ``user_id`` autenticado.

Todos os filtros são aplicados sempre por cima de ``user_id == current
user`` — multi-tenancy é hard-required, igual aos outros routers
(``decisions.py``, ``positions.py``).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, case, desc, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..database import get_db
from ..models.backoffice import DecisionLog
from ..models.shadow_trade import ShadowTrade
from ..services.exit_metrics import flatten_entry_snapshot
from ..services.watchlist_performance_ranking_service import (
    RankingConfigError,
    get_performance_rankings,
)
from ..schemas.shadow_trade import (
    HoldingTimeAnalytics,
    OutcomeMetrics,
    ProfileReportRow,
    ShadowTradeAnalytics,
    ShadowTradeDetail,
    ShadowTradeListResponse,
    ShadowTradePricesResponse,
    ShadowTradeRead,
    ShadowTradeSummary,
    TimeoutAnalyticsResponse,
    TimeoutPostAnalysis,
)
from .config import get_current_user_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/shadow-trades", tags=["Shadow Trades"])

_VALID_STATUSES = {"PENDING", "RUNNING", "COMPLETED", "ERROR"}
# Filtro de origem da promoção. ``None`` = todos (default).
_VALID_SOURCES = {"L3", "L3_REJECTED", "L3_SIMULATED", "L1_SPECTRUM", "L3_LAB"}
_DEFAULT_PAGE_SIZE = 50
_MAX_PAGE_SIZE = 200


def _parse_iso_datetime(
    value: Optional[str], *, is_end: bool = False
) -> Optional[datetime]:
    """Aceita YYYY-MM-DD ou ISO-8601 completo (igual ``decisions.py``)."""
    if not value:
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        if len(raw) == 10:
            parsed_date = date.fromisoformat(raw)
            parsed = datetime.combine(parsed_date, time.max if is_end else time.min)
        else:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail=f"Invalid datetime: {value}"
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _sanitize_symbol(symbol: Optional[str]) -> Optional[str]:
    if not symbol:
        return None
    cleaned = symbol.strip().upper()
    return cleaned or None


def _sanitize_status(status: Optional[str]) -> Optional[str]:
    if not status:
        return None
    cleaned = status.strip().upper()
    if not cleaned:
        return None
    if cleaned not in _VALID_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"status must be one of: {', '.join(sorted(_VALID_STATUSES))}",
        )
    return cleaned


def _sanitize_source(source: Optional[str]) -> Optional[str]:
    """Valida o filtro ``?source=l3|l3_rejected``. ``None`` = sem filtro."""
    if not source:
        return None
    cleaned = source.strip().upper()
    if not cleaned:
        return None
    if cleaned not in _VALID_SOURCES:
        raise HTTPException(
            status_code=422,
            detail=f"source must be one of: {', '.join(sorted(_VALID_SOURCES))}",
        )
    return cleaned


def _build_filters(
    *,
    user_id: UUID,
    status: Optional[str],
    symbol: Optional[str],
    min_date: Optional[str],
    max_date: Optional[str],
    source: Optional[str] = None,
    profile_id: Optional[UUID] = None,
    profile_version: Optional[datetime] = None,
) -> list[Any]:
    conditions: list[Any] = [ShadowTrade.user_id == user_id]
    sanitized_status = _sanitize_status(status)
    sanitized_symbol = _sanitize_symbol(symbol)
    sanitized_source = _sanitize_source(source)
    start_dt = _parse_iso_datetime(min_date)
    end_dt = _parse_iso_datetime(max_date, is_end=True)
    if sanitized_status:
        conditions.append(ShadowTrade.status == sanitized_status)
    if sanitized_symbol:
        conditions.append(ShadowTrade.symbol == sanitized_symbol)
    if sanitized_source:
        conditions.append(ShadowTrade.source == sanitized_source)
    if start_dt is not None:
        conditions.append(ShadowTrade.created_at >= start_dt)
    if end_dt is not None:
        conditions.append(ShadowTrade.created_at <= end_dt)
    if profile_id is not None:
        conditions.append(ShadowTrade.profile_id == profile_id)
    if profile_version is not None:
        conditions.append(ShadowTrade.profile_version == profile_version)
    return conditions


async def _fetch_latest_prices(
    db: AsyncSession, symbols: list[str]
) -> Dict[str, float]:
    """Batch lookup do último close em ``ohlcv`` para uma lista de símbolos.

    UMA query agregada via ``DISTINCT ON (symbol) … ORDER BY symbol, time DESC``
    — O(N) em vez de N round-trips. **Multi-timeframe** intencionalmente:
    nem todo símbolo do shadow_trades tem candle 1m em ``ohlcv`` (em prod o
    coletor ingere principalmente 5m/30m). Aceitar 1m/5m/15m/30m garante
    cobertura em ~100% dos símbolos ativos do pipeline. O preço retornado é o
    close mais recente em qualquer um desses timeframes (o mais "vivo" por
    construção da ORDER BY time DESC). Símbolos sem candle nenhum simplesmente
    não aparecem no dict (caller decide fallback — frontend mostra "—").
    """
    if not symbols:
        return {}
    unique = sorted({s for s in symbols if s})
    if not unique:
        return {}
    res = await db.execute(
        text(
            """
            SELECT DISTINCT ON (symbol) symbol, close
              FROM ohlcv
             WHERE symbol = ANY(:symbols)
               AND timeframe IN ('1m','5m','15m','30m')
             ORDER BY symbol, time DESC
            """
        ),
        {"symbols": unique},
    )
    out: Dict[str, float] = {}
    for row in res.fetchall():
        if row.close is None:
            continue
        try:
            out[row.symbol] = float(row.close)
        except (TypeError, ValueError):
            continue
    return out


def _to_read(
    row: ShadowTrade, *, current_price: Optional[float] = None
) -> ShadowTradeRead:
    return ShadowTradeRead(
        id=row.id,
        symbol=row.symbol,
        direction=row.direction,
        entry_price=float(row.entry_price) if row.entry_price is not None else None,
        current_price=current_price,
        tp_price=float(row.tp_price) if row.tp_price is not None else None,
        sl_price=float(row.sl_price) if row.sl_price is not None else None,
        amount_usdt=float(row.amount_usdt or 0.0),
        outcome=row.outcome,
        pnl_pct=float(row.pnl_pct) if row.pnl_pct is not None else None,
        pnl_usdt=float(row.pnl_usdt) if row.pnl_usdt is not None else None,
        status=row.status,
        skip_reason=row.skip_reason,
        holding_seconds=row.holding_seconds,
        created_at=row.created_at,
        completed_at=row.completed_at,
        entry_timestamp=row.entry_timestamp,
        profile_id=row.profile_id,
        profile_name=row.profile_name,
        btc_price_at_entry=float(row.btc_price_at_entry)
        if row.btc_price_at_entry is not None else None,
        btc_change_1h_pct=float(row.btc_change_1h_pct)
        if row.btc_change_1h_pct is not None else None,
        funding_rate_at_entry=float(row.funding_rate_at_entry)
        if row.funding_rate_at_entry is not None else None,
        n_concurrent_signals=row.n_concurrent_signals,
        mae_pct=float(row.mae_pct) if row.mae_pct is not None else None,
        mfe_pct=float(row.mfe_pct) if row.mfe_pct is not None else None,
        max_drawdown_pct=float(row.max_drawdown_pct)
        if row.max_drawdown_pct is not None else None,
        max_profit_pct=float(row.max_profit_pct)
        if row.max_profit_pct is not None else None,
    )


def _to_detail(
    row: ShadowTrade, *, decision: Optional[DecisionLog] = None
) -> ShadowTradeDetail:
    # Task #316 — pair entry/exit para o painel lado-a-lado.
    # ``features_snapshot`` já é flat (gotcha #290). Fallback para o
    # entry da decision quando o shadow não capturou (shadows legados).
    entry_metrics: Optional[Dict[str, Any]] = None
    exit_metrics: Optional[Dict[str, Any]] = None
    if settings.ENABLE_EXIT_METRICS_UI:
        if isinstance(row.features_snapshot, dict):
            entry_metrics = dict(row.features_snapshot)
        elif decision is not None and isinstance(decision.metrics, dict):
            snap = decision.metrics.get("indicators_snapshot")
            if isinstance(snap, dict):
                entry_metrics = flatten_entry_snapshot(snap)
        if isinstance(row.features_snapshot_exit, dict) and (
            row.features_snapshot_exit.get("_capture_failed") is not True
        ):
            exit_metrics = dict(row.features_snapshot_exit)

    return ShadowTradeDetail(
        id=row.id,
        symbol=row.symbol,
        direction=row.direction,
        entry_price=float(row.entry_price) if row.entry_price is not None else None,
        tp_price=float(row.tp_price) if row.tp_price is not None else None,
        sl_price=float(row.sl_price) if row.sl_price is not None else None,
        amount_usdt=float(row.amount_usdt or 0.0),
        outcome=row.outcome,
        pnl_pct=float(row.pnl_pct) if row.pnl_pct is not None else None,
        pnl_usdt=float(row.pnl_usdt) if row.pnl_usdt is not None else None,
        status=row.status,
        skip_reason=row.skip_reason,
        holding_seconds=row.holding_seconds,
        created_at=row.created_at,
        completed_at=row.completed_at,
        strategy=row.strategy,
        entry_timestamp=row.entry_timestamp,
        exit_price=float(row.exit_price) if row.exit_price is not None else None,
        exit_timestamp=row.exit_timestamp,
        tp_pct=float(row.tp_pct) if row.tp_pct is not None else None,
        sl_pct=float(row.sl_pct) if row.sl_pct is not None else None,
        timeout_candles=row.timeout_candles,
        decision_id=row.decision_id,
        last_processed_time=row.last_processed_time,
        updated_at=row.updated_at,
        config_snapshot=row.config_snapshot
        if isinstance(row.config_snapshot, dict)
        else None,
        decision_strategy=decision.strategy if decision else None,
        decision_score=float(decision.score)
        if decision and decision.score is not None
        else None,
        decision_decision=decision.decision if decision else None,
        decision_event_type=decision.event_type if decision else None,
        decision_l1_pass=decision.l1_pass if decision else None,
        decision_l2_pass=decision.l2_pass if decision else None,
        decision_l3_pass=decision.l3_pass if decision else None,
        decision_latency_ms=decision.latency_ms if decision else None,
        decision_created_at=decision.created_at if decision else None,
        decision_reasons=(decision.reasons or {})
        if decision and isinstance(decision.reasons, dict)
        else None,
        decision_metrics=(decision.metrics or {})
        if decision and isinstance(decision.metrics, dict)
        else None,
        features_snapshot=row.features_snapshot
        if isinstance(row.features_snapshot, dict)
        else None,
        features_snapshot_exit=row.features_snapshot_exit
        if isinstance(row.features_snapshot_exit, dict)
        else None,
        btc_price_at_entry=float(row.btc_price_at_entry)
        if row.btc_price_at_entry is not None else None,
        btc_change_1h_pct=float(row.btc_change_1h_pct)
        if row.btc_change_1h_pct is not None else None,
        funding_rate_at_entry=float(row.funding_rate_at_entry)
        if row.funding_rate_at_entry is not None else None,
        n_concurrent_signals=row.n_concurrent_signals,
        entry_metrics=entry_metrics,
        exit_metrics=exit_metrics,
        mae_pct=float(row.mae_pct) if row.mae_pct is not None else None,
        mfe_pct=float(row.mfe_pct) if row.mfe_pct is not None else None,
        max_drawdown_pct=float(row.max_drawdown_pct)
        if row.max_drawdown_pct is not None else None,
        max_profit_pct=float(row.max_profit_pct)
        if row.max_profit_pct is not None else None,
        min_price_post_entry=float(row.min_price_post_entry)
        if row.min_price_post_entry is not None else None,
        max_price_post_entry=float(row.max_price_post_entry)
        if row.max_price_post_entry is not None else None,
        exit_metrics_json=row.exit_metrics_json
        if isinstance(row.exit_metrics_json, dict)
        else None,
        # Strategy Lab fields (migration 077)
        profile_id=row.profile_id if hasattr(row, "profile_id") else None,
        profile_version=row.profile_version if hasattr(row, "profile_version") else None,
        profile_name=row.profile_name if hasattr(row, "profile_name") else None,
        strategy_type=row.strategy_type if hasattr(row, "strategy_type") else None,
        rules_snapshot=row.rules_snapshot
        if hasattr(row, "rules_snapshot") and isinstance(row.rules_snapshot, dict)
        else None,
        ml_probability=float(row.ml_probability)
        if hasattr(row, "ml_probability") and row.ml_probability is not None else None,
        ml_model_id=row.ml_model_id if hasattr(row, "ml_model_id") else None,
        final_priority_score=float(row.final_priority_score)
        if hasattr(row, "final_priority_score") and row.final_priority_score is not None else None,
    )


@router.get("", response_model=ShadowTradeListResponse)
async def list_shadow_trades(
    status: Optional[str] = Query(None),
    symbol: Optional[str] = Query(None),
    min_date: Optional[str] = Query(None),
    max_date: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    profile_id: Optional[UUID] = Query(None, description="Filter by Strategy Lab profile"),
    profile_version: Optional[datetime] = Query(None, description="Filter by profile version"),
    page: int = Query(1, ge=1),
    page_size: int = Query(_DEFAULT_PAGE_SIZE, ge=1, le=_MAX_PAGE_SIZE),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> ShadowTradeListResponse:
    """Lista paginada de shadow trades do usuário autenticado."""
    try:
        filters = _build_filters(
            user_id=user_id,
            status=status,
            symbol=symbol,
            min_date=min_date,
            max_date=max_date,
            source=source,
            profile_id=profile_id,
            profile_version=profile_version,
        )
        total_q = select(func.count(ShadowTrade.id)).where(and_(*filters))
        total = int((await db.execute(total_q)).scalar_one() or 0)

        offset = (page - 1) * page_size
        page_q = (
            select(ShadowTrade)
            .where(and_(*filters))
            .order_by(desc(ShadowTrade.created_at), desc(ShadowTrade.id))
            .offset(offset)
            .limit(page_size)
        )
        rows = (await db.execute(page_q)).scalars().all()

        prices = await _fetch_latest_prices(db, [r.symbol for r in rows])

        return ShadowTradeListResponse(
            items=[_to_read(r, current_price=prices.get(r.symbol)) for r in rows],
            total=total,
            page=page,
            page_size=page_size,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to list shadow trades: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500, detail="Failed to list shadow trades"
        ) from exc


@router.get("/summary", response_model=ShadowTradeSummary)
async def shadow_trades_summary(
    status: Optional[str] = Query(None),
    symbol: Optional[str] = Query(None),
    min_date: Optional[str] = Query(None),
    max_date: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    profile_id: Optional[UUID] = Query(None, description="Filter by Strategy Lab profile"),
    profile_version: Optional[datetime] = Query(None, description="Filter by profile version"),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> ShadowTradeSummary:
    """Agregado single-query do range filtrado."""
    try:
        filters = _build_filters(
            user_id=user_id,
            status=status,
            symbol=symbol,
            min_date=min_date,
            max_date=max_date,
            source=source,
            profile_id=profile_id,
            profile_version=profile_version,
        )

        # Avg de pnl_pct deve considerar APENAS trades com outcome
        # finalizado (TP/SL/TIMEOUT) — pendentes não têm pnl.
        completed_filter = ShadowTrade.outcome.in_(("TP_HIT", "SL_HIT", "TIMEOUT"))

        stats_q = select(
            func.count(ShadowTrade.id).label("total"),
            func.count(ShadowTrade.id)
            .filter(ShadowTrade.status.in_(("PENDING", "RUNNING")))
            .label("pending"),
            func.count(ShadowTrade.id)
            .filter(ShadowTrade.status == "COMPLETED")
            .label("completed"),
            func.count(ShadowTrade.id)
            .filter(ShadowTrade.outcome == "TP_HIT")
            .label("win"),
            func.count(ShadowTrade.id)
            .filter(ShadowTrade.outcome == "SL_HIT")
            .label("loss"),
            func.count(ShadowTrade.id)
            .filter(ShadowTrade.outcome == "TIMEOUT")
            .label("timeout"),
            func.coalesce(
                func.sum(
                    case((ShadowTrade.pnl_usdt.isnot(None), ShadowTrade.pnl_usdt), else_=0.0)
                ),
                0.0,
            ).label("total_pnl_usdt"),
            func.avg(case((completed_filter, ShadowTrade.pnl_pct), else_=None)).label(
                "avg_pnl_pct"
            ),
            func.min(ShadowTrade.created_at).label("period_start"),
            func.max(ShadowTrade.created_at).label("period_end"),
        ).where(and_(*filters))

        row = (await db.execute(stats_q)).one()
        total = int(row.total or 0)
        completed = int(row.completed or 0)
        win = int(row.win or 0)
        win_rate = round((win / completed) * 100, 2) if completed else 0.0

        return ShadowTradeSummary(
            total=total,
            pending=int(row.pending or 0),
            completed=completed,
            win=win,
            loss=int(row.loss or 0),
            timeout=int(row.timeout or 0),
            win_rate=win_rate,
            total_pnl_usdt=round(float(row.total_pnl_usdt or 0.0), 4),
            avg_pnl_pct=round(float(row.avg_pnl_pct or 0.0), 4),
            period_start=row.period_start,
            period_end=row.period_end,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to summarize shadow trades: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500, detail="Failed to summarize shadow trades"
        ) from exc


@router.get("/prices", response_model=ShadowTradePricesResponse)
async def shadow_trade_prices(
    symbols: str = Query(
        ...,
        description="CSV de símbolos (ex.: BTC_USDT,ETH_USDT). Limite 200.",
    ),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> ShadowTradePricesResponse:
    """Lookup leve de preços correntes — usado pelo polling do frontend.

    Sem checagem de ownership por símbolo (preços de OHLCV são públicos
    e não revelam nada sobre o portfólio do usuário). Limita a 200
    símbolos por chamada para evitar abuso.
    """
    parsed = [s.strip().upper() for s in (symbols or "").split(",") if s.strip()]
    if not parsed:
        return ShadowTradePricesResponse(prices={}, fetched_at=datetime.now(timezone.utc))
    if len(parsed) > 200:
        raise HTTPException(status_code=422, detail="too many symbols (max 200)")
    try:
        prices = await _fetch_latest_prices(db, parsed)
        return ShadowTradePricesResponse(
            prices=prices, fetched_at=datetime.now(timezone.utc)
        )
    except Exception as exc:
        logger.error("Failed to fetch shadow prices: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500, detail="Failed to fetch prices"
        ) from exc


@router.get("/analytics", response_model=ShadowTradeAnalytics)
async def shadow_trades_analytics(
    min_date: Optional[str] = Query(None),
    max_date: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> ShadowTradeAnalytics:
    """Analytics segmentado por outcome — Fase Quant 3.

    Retorna taxas por outcome (TP/SL/TIMEOUT), holding times médios,
    e métricas MAE/MFE por grupo (requer migration 062).

    Recovery analysis:
    - near_sl_winners_pct: % de TP_HIT com mae_pct < -2% (quase bateram SL)
    - sl_after_strong_mfe_pct: % de SL_HIT com mfe_pct > 1% (SL após forte MFE)
    - avg_recovery_pct: avg(mfe_pct - mae_pct) nos TP_HIT (spread de excursão)
    """
    try:
        start_dt = _parse_iso_datetime(min_date)
        end_dt = _parse_iso_datetime(max_date, is_end=True)
        sanitized_source = _sanitize_source(source)

        conditions = ["user_id = :uid", "status = 'COMPLETED'"]
        params: Dict[str, Any] = {"uid": str(user_id)}
        if start_dt is not None:
            conditions.append("created_at >= :start_dt")
            params["start_dt"] = start_dt
        if end_dt is not None:
            conditions.append("created_at <= :end_dt")
            params["end_dt"] = end_dt
        if sanitized_source:
            conditions.append("source = :source")
            params["source"] = sanitized_source

        where_clause = " AND ".join(conditions)

        # Single-query aggregate — todos os outcomes em paralelo.
        q = text(f"""
            SELECT
                COUNT(*) FILTER (WHERE outcome = 'TP_HIT')       AS tp_count,
                COUNT(*) FILTER (WHERE outcome = 'SL_HIT')       AS sl_count,
                COUNT(*) FILTER (WHERE outcome = 'TIMEOUT')      AS to_count,
                COUNT(*)                                          AS total,
                -- avg pnl por outcome
                AVG(pnl_pct) FILTER (WHERE outcome = 'TP_HIT')   AS tp_avg_pnl,
                AVG(pnl_pct) FILTER (WHERE outcome = 'SL_HIT')   AS sl_avg_pnl,
                AVG(pnl_pct) FILTER (WHERE outcome = 'TIMEOUT')  AS to_avg_pnl,
                -- avg holding por outcome
                AVG(holding_seconds) FILTER (WHERE outcome = 'TP_HIT')  AS tp_avg_hold,
                AVG(holding_seconds) FILTER (WHERE outcome = 'SL_HIT')  AS sl_avg_hold,
                AVG(holding_seconds) FILTER (WHERE outcome = 'TIMEOUT') AS to_avg_hold,
                -- MAE/MFE por grupo (nullable: None antes da migration 062)
                AVG(mae_pct) FILTER (WHERE outcome = 'TP_HIT')   AS tp_avg_mae,
                AVG(mfe_pct) FILTER (WHERE outcome = 'TP_HIT')   AS tp_avg_mfe,
                AVG(mae_pct) FILTER (WHERE outcome = 'SL_HIT')   AS sl_avg_mae,
                AVG(mfe_pct) FILTER (WHERE outcome = 'SL_HIT')   AS sl_avg_mfe,
                -- Recovery: TP_HIT com mae < -2% (quase perdeu mas venceu)
                COUNT(*) FILTER (WHERE outcome = 'TP_HIT' AND mae_pct < -2.0)  AS near_sl_winners,
                -- SL após forte MFE: SL_HIT com mfe > 1%
                COUNT(*) FILTER (WHERE outcome = 'SL_HIT' AND mfe_pct > 1.0)   AS sl_after_mfe,
                -- avg recovery spread em TP_HIT (mfe - mae = spread de excursão)
                AVG(mfe_pct - mae_pct) FILTER (
                    WHERE outcome = 'TP_HIT' AND mae_pct IS NOT NULL AND mfe_pct IS NOT NULL
                ) AS avg_recovery,
                MIN(created_at) AS period_start,
                MAX(created_at) AS period_end
            FROM shadow_trades
            WHERE {where_clause}
        """)

        row = (await db.execute(q, params)).one()

        total = int(row.total or 0)
        tp_count = int(row.tp_count or 0)
        sl_count = int(row.sl_count or 0)
        to_count = int(row.to_count or 0)

        def _rate(n: int) -> float:
            return round((n / total) * 100, 2) if total else 0.0

        def _f(v) -> Optional[float]:
            return round(float(v), 4) if v is not None else None

        near_sl_winners = int(row.near_sl_winners or 0)
        sl_after_mfe = int(row.sl_after_mfe or 0)

        return ShadowTradeAnalytics(
            total_completed=total,
            tp=OutcomeMetrics(
                count=tp_count,
                rate_pct=_rate(tp_count),
                avg_pnl_pct=_f(row.tp_avg_pnl),
                avg_holding_seconds=_f(row.tp_avg_hold),
                avg_mae_pct=_f(row.tp_avg_mae),
                avg_mfe_pct=_f(row.tp_avg_mfe),
            ),
            sl=OutcomeMetrics(
                count=sl_count,
                rate_pct=_rate(sl_count),
                avg_pnl_pct=_f(row.sl_avg_pnl),
                avg_holding_seconds=_f(row.sl_avg_hold),
                avg_mae_pct=_f(row.sl_avg_mae),
                avg_mfe_pct=_f(row.sl_avg_mfe),
            ),
            timeout=OutcomeMetrics(
                count=to_count,
                rate_pct=_rate(to_count),
                avg_pnl_pct=_f(row.to_avg_pnl),
                avg_holding_seconds=_f(row.to_avg_hold),
                avg_mae_pct=None,
                avg_mfe_pct=None,
            ),
            avg_mae_winners=_f(row.tp_avg_mae),
            avg_mfe_winners=_f(row.tp_avg_mfe),
            avg_mae_losers=_f(row.sl_avg_mae),
            avg_mfe_losers=_f(row.sl_avg_mfe),
            near_sl_winners_pct=round((near_sl_winners / tp_count) * 100, 2)
                if tp_count else None,
            sl_after_strong_mfe_pct=round((sl_after_mfe / sl_count) * 100, 2)
                if sl_count else None,
            avg_recovery_pct=_f(row.avg_recovery),
            period_start=row.period_start,
            period_end=row.period_end,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to compute shadow analytics: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500, detail="Failed to compute shadow analytics"
        ) from exc


@router.get("/timeout-analysis", response_model=TimeoutAnalyticsResponse)
async def shadow_trades_timeout_analysis(
    min_date: Optional[str] = Query(None),
    max_date: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> TimeoutAnalyticsResponse:
    """Timeout Post-Analysis + Holding Time Validation (Fases Quant 1+2).

    Retorna métricas observacionais sobre o comportamento de preço
    após trades TIMEOUT: recovery rate, delayed TP, MFE/MAE adicionais,
    e validação do holding time por outcome.

    Todos os campos são puramente analíticos — outcomes originais
    nunca são alterados por este endpoint.
    """
    try:
        dt_min = _parse_iso_datetime(min_date, is_end=False)
        dt_max = _parse_iso_datetime(max_date, is_end=True)

        base_filters = [ShadowTrade.user_id == user_id]
        if dt_min:
            base_filters.append(ShadowTrade.created_at >= dt_min)
        if dt_max:
            base_filters.append(ShadowTrade.created_at <= dt_max)

        # ── Fase 1: Timeout Post-Analysis ────────────────────────────────
        timeout_filter = base_filters + [ShadowTrade.outcome == "TIMEOUT"]

        row = await db.execute(
            select(
                func.count().label("total"),
                func.count(
                    case((ShadowTrade.timeout_post_analysis_done.is_(True), 1))
                ).label("analyzed"),
                func.count(
                    case((ShadowTrade.delayed_tp.is_(True), 1))
                ).label("delayed_tp_count"),
                func.avg(ShadowTrade.delayed_tp_hours).label("avg_delayed_tp_hours"),
                func.percentile_cont(0.5).within_group(
                    ShadowTrade.delayed_tp_hours
                ).label("median_delayed_tp_hours"),
                func.avg(ShadowTrade.max_profit_after_timeout_pct).label("avg_mfe_after"),
                func.avg(ShadowTrade.max_drawdown_after_timeout_pct).label("avg_mae_after"),
                # Variação pós-timeout relativa ao entry_price
                func.avg(
                    case(
                        (
                            and_(
                                ShadowTrade.price_after_1h.is_not(None),
                                ShadowTrade.entry_price.is_not(None),
                                ShadowTrade.entry_price > 0,
                            ),
                            (ShadowTrade.price_after_1h - ShadowTrade.entry_price)
                            / ShadowTrade.entry_price * 100,
                        )
                    )
                ).label("avg_chg_1h"),
                func.avg(
                    case(
                        (
                            and_(
                                ShadowTrade.price_after_2h.is_not(None),
                                ShadowTrade.entry_price.is_not(None),
                                ShadowTrade.entry_price > 0,
                            ),
                            (ShadowTrade.price_after_2h - ShadowTrade.entry_price)
                            / ShadowTrade.entry_price * 100,
                        )
                    )
                ).label("avg_chg_2h"),
                func.avg(
                    case(
                        (
                            and_(
                                ShadowTrade.price_after_4h.is_not(None),
                                ShadowTrade.entry_price.is_not(None),
                                ShadowTrade.entry_price > 0,
                            ),
                            (ShadowTrade.price_after_4h - ShadowTrade.entry_price)
                            / ShadowTrade.entry_price * 100,
                        )
                    )
                ).label("avg_chg_4h"),
                func.avg(
                    case(
                        (
                            and_(
                                ShadowTrade.price_after_12h.is_not(None),
                                ShadowTrade.entry_price.is_not(None),
                                ShadowTrade.entry_price > 0,
                            ),
                            (ShadowTrade.price_after_12h - ShadowTrade.entry_price)
                            / ShadowTrade.entry_price * 100,
                        )
                    )
                ).label("avg_chg_12h"),
                func.avg(
                    case(
                        (
                            and_(
                                ShadowTrade.price_after_24h.is_not(None),
                                ShadowTrade.entry_price.is_not(None),
                                ShadowTrade.entry_price > 0,
                            ),
                            (ShadowTrade.price_after_24h - ShadowTrade.entry_price)
                            / ShadowTrade.entry_price * 100,
                        )
                    )
                ).label("avg_chg_24h"),
                func.min(ShadowTrade.exit_timestamp).label("period_start"),
                func.max(ShadowTrade.exit_timestamp).label("period_end"),
            ).where(and_(*timeout_filter))
        )
        tr = row.fetchone()

        total = int(tr.total or 0)
        analyzed = int(tr.analyzed or 0)
        delayed_tp_count = int(tr.delayed_tp_count or 0)
        recovery_rate = (delayed_tp_count / analyzed * 100.0) if analyzed > 0 else 0.0

        timeout_post_analysis = TimeoutPostAnalysis(
            total_timeouts=total,
            analyzed=analyzed,
            pending_analysis=total - analyzed,
            delayed_tp_count=delayed_tp_count,
            timeout_recovery_rate_pct=round(recovery_rate, 2),
            avg_delayed_tp_hours=float(tr.avg_delayed_tp_hours)
                if tr.avg_delayed_tp_hours is not None else None,
            median_delayed_tp_hours=float(tr.median_delayed_tp_hours)
                if tr.median_delayed_tp_hours is not None else None,
            avg_mfe_after_timeout_pct=float(tr.avg_mfe_after)
                if tr.avg_mfe_after is not None else None,
            avg_mae_after_timeout_pct=float(tr.avg_mae_after)
                if tr.avg_mae_after is not None else None,
            avg_price_change_1h_pct=float(tr.avg_chg_1h)
                if tr.avg_chg_1h is not None else None,
            avg_price_change_2h_pct=float(tr.avg_chg_2h)
                if tr.avg_chg_2h is not None else None,
            avg_price_change_4h_pct=float(tr.avg_chg_4h)
                if tr.avg_chg_4h is not None else None,
            avg_price_change_12h_pct=float(tr.avg_chg_12h)
                if tr.avg_chg_12h is not None else None,
            avg_price_change_24h_pct=float(tr.avg_chg_24h)
                if tr.avg_chg_24h is not None else None,
            period_start=tr.period_start,
            period_end=tr.period_end,
        )

        # ── Fase 2: Holding Time Validation ──────────────────────────────
        completed_filter = base_filters + [ShadowTrade.status == "COMPLETED"]

        ht_row = await db.execute(
            select(
                func.avg(
                    case((ShadowTrade.outcome == "TP_HIT", ShadowTrade.holding_seconds))
                ).label("avg_hold_tp"),
                func.avg(
                    case((ShadowTrade.outcome == "SL_HIT", ShadowTrade.holding_seconds))
                ).label("avg_hold_sl"),
                func.avg(
                    case((ShadowTrade.outcome == "TIMEOUT", ShadowTrade.holding_seconds))
                ).label("avg_hold_timeout"),
                # Delayed TP holding (TIMEOUT trades que teriam batido TP)
                func.avg(
                    case(
                        (
                            and_(
                                ShadowTrade.outcome == "TIMEOUT",
                                ShadowTrade.delayed_tp.is_(True),
                            ),
                            ShadowTrade.holding_seconds,
                        )
                    )
                ).label("avg_hold_delayed_tp"),
                # Slow winners: TP_HIT com mae_pct < -2% (passaram por drawdown significativo)
                func.count(
                    case(
                        (
                            and_(
                                ShadowTrade.outcome == "TP_HIT",
                                ShadowTrade.mae_pct < -2.0,
                            ),
                            1,
                        )
                    )
                ).label("slow_winners"),
                func.count(case((ShadowTrade.outcome == "TP_HIT", 1))).label("tp_count"),
                # Fast winners: TP_HIT com mfe_pct > 1% (movimento explosivo)
                func.count(
                    case(
                        (
                            and_(
                                ShadowTrade.outcome == "TP_HIT",
                                ShadowTrade.mfe_pct > 1.0,
                                ShadowTrade.mae_pct.is_not(None),
                                ShadowTrade.mae_pct > -0.5,
                            ),
                            1,
                        )
                    )
                ).label("fast_winners"),
                # Fake momentum: SL_HIT com mfe_pct > 1%
                func.count(
                    case(
                        (
                            and_(
                                ShadowTrade.outcome == "SL_HIT",
                                ShadowTrade.mfe_pct > 1.0,
                            ),
                            1,
                        )
                    )
                ).label("fake_momentum"),
                func.count(case((ShadowTrade.outcome == "SL_HIT", 1))).label("sl_count"),
            ).where(and_(*completed_filter))
        )
        ht = ht_row.fetchone()

        tp_count = int(ht.tp_count or 0)
        sl_count = int(ht.sl_count or 0)
        slow_w = int(ht.slow_winners or 0)
        fast_w = int(ht.fast_winners or 0)
        fake_m = int(ht.fake_momentum or 0)

        holding_time = HoldingTimeAnalytics(
            avg_holding_tp_seconds=float(ht.avg_hold_tp) if ht.avg_hold_tp is not None else None,
            avg_holding_sl_seconds=float(ht.avg_hold_sl) if ht.avg_hold_sl is not None else None,
            avg_holding_timeout_seconds=float(ht.avg_hold_timeout)
                if ht.avg_hold_timeout is not None else None,
            avg_holding_delayed_tp_seconds=float(ht.avg_hold_delayed_tp)
                if ht.avg_hold_delayed_tp is not None else None,
            slow_winners_count=slow_w,
            slow_winners_pct=round(slow_w / tp_count * 100, 2) if tp_count > 0 else None,
            fast_winners_count=fast_w,
            fast_winners_pct=round(fast_w / tp_count * 100, 2) if tp_count > 0 else None,
            fake_momentum_count=fake_m,
            fake_momentum_pct=round(fake_m / sl_count * 100, 2) if sl_count > 0 else None,
        )

        return TimeoutAnalyticsResponse(
            timeout_post_analysis=timeout_post_analysis,
            holding_time=holding_time,
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to compute timeout analytics: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500, detail="Failed to compute timeout analytics"
        ) from exc


@router.get("/profile-report", response_model=List[ProfileReportRow])
async def profile_report(
    order_by: str = Query("ev_score", pattern="^(ev_score|performance_priority)$"),
    direction: str = Query("desc", pattern="^(asc|desc)$"),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> List[ProfileReportRow]:
    """Canonical performance ranking shared by Shadow Portfolio and L3."""
    try:
        rows = await get_performance_rankings(db, user_id)
        if direction == "asc":
            rows.reverse()
        return rows
    except RankingConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to compute performance ranking: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to compute performance ranking") from exc


@router.get("/{shadow_id}", response_model=ShadowTradeDetail)
async def get_shadow_trade(
    shadow_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> ShadowTradeDetail:
    """Detalhe de um shadow trade. 404 se não pertencer ao user."""
    try:
        q = select(ShadowTrade).where(
            and_(ShadowTrade.id == shadow_id, ShadowTrade.user_id == user_id)
        )
        row = (await db.execute(q)).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="Shadow trade not found")
        decision: Optional[DecisionLog] = None
        if row.decision_id is not None:
            # Defesa em profundidade: shadow já foi filtrado por user_id, mas
            # restringimos a decision_log pelo mesmo user_id para evitar
            # vazamento entre tenants caso decision_id seja "envenenado".
            dq = select(DecisionLog).where(
                and_(
                    DecisionLog.id == row.decision_id,
                    DecisionLog.user_id == user_id,
                )
            )
            decision = (await db.execute(dq)).scalar_one_or_none()
        return _to_detail(row, decision=decision)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to fetch shadow trade %s: %s", shadow_id, exc, exc_info=True)
        raise HTTPException(
            status_code=500, detail="Failed to fetch shadow trade"
        ) from exc


@router.post("/monitor/trigger", status_code=202)
async def trigger_monitor(
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    """Dispara o shadow_trade_monitor ad-hoc na fila de execução.

    Útil para recuperação de produção quando o beat não está processando
    (ex.: após deploy, janela entre ticks, debug). Requer autenticação normal.
    A task é idempotente e usa FOR UPDATE SKIP LOCKED — múltiplos disparos
    simultâneos são seguros.
    """
    try:
        from ..tasks.shadow_trade_monitor import run as _shadow_monitor_task
        result = _shadow_monitor_task.apply_async(queue="execution")
        return {
            "dispatched": True,
            "task_id": result.id,
            "queue": "execution",
            "message": "Shadow monitor task dispatched. Check logs in ~5s.",
        }
    except Exception as exc:
        logger.error("Failed to dispatch shadow monitor: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Dispatch failed: {exc}"
        ) from exc


@router.get("/barrier-status")
async def shadow_barrier_status(
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    """Relatório de barreiras TP/SL rompidas em trades shadow abertos.

    Retorna contagem por source de trades RUNNING/PENDING cujo preço atual
    (market_metadata) já cruzou TP ou SL mas ainda não foram fechados pelo
    monitor. Útil para detectar atraso no shadow closer.

    Stale guard: market_metadata sem last_updated ou com last_updated
    anterior a 10 min não é considerado preço válido para esta contagem.
    """
    from datetime import timedelta

    stale_cutoff = datetime.now(timezone.utc) - timedelta(seconds=600)

    try:
        res = await db.execute(
            text("""
                SELECT
                    st.source,
                    COUNT(*) FILTER (WHERE mm.price <= st.sl_price) AS open_below_sl,
                    COUNT(*) FILTER (WHERE mm.price >= st.tp_price) AS open_above_tp,
                    COUNT(*) AS open_total
                FROM shadow_trades st
                JOIN market_metadata mm ON mm.symbol = st.symbol
                WHERE st.user_id = :uid
                  AND st.status IN ('RUNNING', 'PENDING')
                  AND st.tp_price IS NOT NULL
                  AND st.sl_price IS NOT NULL
                  AND (mm.last_updated IS NULL OR mm.last_updated >= :stale_cutoff)
                GROUP BY st.source
                ORDER BY st.source
            """),
            {"uid": str(user_id), "stale_cutoff": stale_cutoff},
        )
        rows = res.fetchall()

        by_source = {}
        total_below_sl = 0
        total_above_tp = 0
        for row in rows:
            src = row[0] or "UNKNOWN"
            below = int(row[1] or 0)
            above = int(row[2] or 0)
            total_below_sl += below
            total_above_tp += above
            by_source[src] = {
                "open_below_sl": below,
                "open_above_tp": above,
                "open_breached": below + above,
                "open_total": int(row[3] or 0),
            }

        open_breached = total_below_sl + total_above_tp
        closer_status = "OK" if open_breached == 0 else "BARRIER_BREACH_DETECTED"

        # Last closure audit row (best-effort)
        last_run_res = await db.execute(
            text("""
                SELECT MAX(created_at) AS last_at
                FROM shadow_trade_closure_audit
            """)
        )
        last_run_row = last_run_res.fetchone()
        last_closer_run_at = last_run_row[0] if last_run_row else None

        return {
            "open_below_sl": total_below_sl,
            "open_above_tp": total_above_tp,
            "open_breached_barriers": open_breached,
            "closer_status": closer_status,
            "last_closer_run_at": last_closer_run_at.isoformat() if last_closer_run_at else None,
            "by_source": by_source,
            "price_freshness_cutoff_seconds": 600,
        }
    except Exception as exc:
        logger.error("Failed to compute barrier status: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500, detail="Failed to compute barrier status"
        ) from exc
