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
from typing import Any, Dict, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, case, desc, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models.backoffice import DecisionLog
from ..models.shadow_trade import ShadowTrade
from ..schemas.shadow_trade import (
    ShadowTradeDetail,
    ShadowTradeListResponse,
    ShadowTradePricesResponse,
    ShadowTradeRead,
    ShadowTradeSummary,
)
from .config import get_current_user_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/shadow-trades", tags=["Shadow Trades"])

_VALID_STATUSES = {"PENDING", "RUNNING", "COMPLETED", "ERROR"}
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


def _build_filters(
    *,
    user_id: UUID,
    status: Optional[str],
    symbol: Optional[str],
    min_date: Optional[str],
    max_date: Optional[str],
) -> list[Any]:
    conditions: list[Any] = [ShadowTrade.user_id == user_id]
    sanitized_status = _sanitize_status(status)
    sanitized_symbol = _sanitize_symbol(symbol)
    start_dt = _parse_iso_datetime(min_date)
    end_dt = _parse_iso_datetime(max_date, is_end=True)
    if sanitized_status:
        conditions.append(ShadowTrade.status == sanitized_status)
    if sanitized_symbol:
        conditions.append(ShadowTrade.symbol == sanitized_symbol)
    if start_dt is not None:
        conditions.append(ShadowTrade.created_at >= start_dt)
    if end_dt is not None:
        conditions.append(ShadowTrade.created_at <= end_dt)
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
    )


def _to_detail(
    row: ShadowTrade, *, decision: Optional[DecisionLog] = None
) -> ShadowTradeDetail:
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
    )


@router.get("", response_model=ShadowTradeListResponse)
async def list_shadow_trades(
    status: Optional[str] = Query(None),
    symbol: Optional[str] = Query(None),
    min_date: Optional[str] = Query(None),
    max_date: Optional[str] = Query(None),
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
