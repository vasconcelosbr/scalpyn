"""Shadow Timeout Analyzer — Fase Quant: Timeout Post-Analysis.

Para cada shadow com outcome='TIMEOUT' e timeout_post_analysis_done=FALSE,
consulta OHLCV histórico para rastrear passivamente o que aconteceu com o
preço nas 24h após o encerramento.

Regras obrigatórias
-------------------
* NÃO reabre trades. NÃO altera outcome, pnl_pct, pnl_usdt, holding_seconds.
* NÃO altera TP, SL, timeout, labels, FEATURE_COLUMNS ou inferência XGBoost.
* Puramente observacional: os campos preenchidos são additive-only.
* Idempotente: timeout_post_analysis_done=TRUE bloqueia reprocessamento.
* Processa em batches (SHADOW_ANALYZER_BATCH_SIZE, default 100) ordenados
  por exit_timestamp ASC para backfill progressivo de trades antigos.

Campos preenchidos (migration 063)
-----------------------------------
price_after_1h / 2h / 4h / 12h / 24h   — close da candle 1m mais próxima
                                           do horizonte pós-exit_timestamp.
max_profit_after_timeout_pct            — (max_high_24h - entry) / entry * 100
max_drawdown_after_timeout_pct          — (min_low_24h  - entry) / entry * 100
delayed_tp                              — TRUE se max_high_24h >= tp_price
delayed_tp_hours                        — horas até a primeira candle onde
                                           high >= tp_price (NULL se delayed_tp=FALSE)
timeout_post_analysis_done              — TRUE ao finalizar este trade

Métricas calculáveis a posteriori (não armazenadas, calculadas em analytics)
-----------------------------------------------------------------------------
* Timeout Recovery Rate  = COUNT(delayed_tp=TRUE) / COUNT(outcome=TIMEOUT)
* Avg Delayed TP Time    = AVG(delayed_tp_hours) WHERE delayed_tp=TRUE
* Avg MFE After Timeout  = AVG(max_profit_after_timeout_pct)
* Avg MAE After Timeout  = AVG(max_drawdown_after_timeout_pct)

Knobs (env)
-----------
* SHADOW_ANALYZER_BATCH_SIZE   (default 100) — trades por execução.
* SHADOW_ANALYZER_INTERVAL_S   (default 3600) — beat schedule (1h).
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select, text

from .celery_app import celery_app
from ..models.shadow_trade import ShadowTrade

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


SHADOW_ANALYZER_BATCH_SIZE = _env_int("SHADOW_ANALYZER_BATCH_SIZE", 100)

# Horizontes temporais pós-timeout em horas
_HORIZONS_H = [1, 2, 4, 12, 24]
# Janela de busca ao redor do horizonte (para achar a candle 1m mais próxima)
_HORIZON_TOLERANCE_MIN = 5


def _run_async(coro):
    """Run async coroutine em task Celery síncrono.

    Mesma lógica de teardown de 5 passos do shadow_trade_monitor
    (Task #274 canonical pattern).
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        try:
            pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
            for t in pending:
                t.cancel()
            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
        except BaseException as exc:
            logger.debug("[timeout-analyzer] pending-task drain: %s", exc)

        try:
            from ..database import _celery_engine
            loop.run_until_complete(_celery_engine.dispose())
            loop.run_until_complete(asyncio.sleep(0))
        except BaseException as exc:
            logger.debug("[timeout-analyzer] engine dispose: %s", exc)

        try:
            from ..database import _celery_engine as _ce
            sync_pool = _ce.sync_engine.pool
            records = list(getattr(sync_pool, "_all_conns", None) or [])
            for record in records:
                raw = (
                    getattr(record, "dbapi_connection", None)
                    or getattr(record, "connection", None)
                )
                asyncpg_conn = (
                    getattr(raw, "_connection", None)
                    or getattr(raw, "connection", None)
                    or raw
                )
                terminate = getattr(asyncpg_conn, "terminate", None)
                if callable(terminate):
                    try:
                        terminate()
                    except BaseException:
                        pass
        except BaseException as exc:
            logger.debug("[timeout-analyzer] hard-terminate: %s", exc)

        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        except BaseException as exc:
            logger.debug("[timeout-analyzer] shutdown_asyncgens: %s", exc)

        try:
            loop.close()
        except BaseException as exc:
            logger.debug("[timeout-analyzer] loop.close: %s", exc)
        try:
            asyncio.set_event_loop(None)
        except BaseException:
            pass


async def _fetch_ohlcv_window(
    db,
    symbol: str,
    after_ts: datetime,
    before_ts: datetime,
) -> List[Dict[str, Any]]:
    """Candles 1m em [after_ts, before_ts] para um símbolo."""
    res = await db.execute(
        text(
            """
            SELECT time, high, low, close
              FROM ohlcv
             WHERE symbol = :s
               AND timeframe = '1m'
               AND time > :t_start
               AND time <= :t_end
             ORDER BY time ASC
            """
        ),
        {"s": symbol, "t_start": after_ts, "t_end": before_ts},
    )
    return [
        {
            "time": r.time,
            "high": float(r.high) if r.high is not None else None,
            "low": float(r.low) if r.low is not None else None,
            "close": float(r.close) if r.close is not None else None,
        }
        for r in res.fetchall()
    ]


async def _fetch_close_near_horizon(
    db,
    symbol: str,
    target_ts: datetime,
) -> Optional[float]:
    """Close da candle 1m mais próxima de target_ts (dentro de ±5 min)."""
    t_start = target_ts - timedelta(minutes=_HORIZON_TOLERANCE_MIN)
    t_end = target_ts + timedelta(minutes=_HORIZON_TOLERANCE_MIN)
    res = await db.execute(
        text(
            """
            SELECT close
              FROM ohlcv
             WHERE symbol = :s
               AND timeframe = '1m'
               AND time >= :t_start
               AND time <= :t_end
             ORDER BY ABS(EXTRACT(EPOCH FROM (time - :target)))
             LIMIT 1
            """
        ),
        {"s": symbol, "t_start": t_start, "t_end": t_end, "target": target_ts},
    )
    row = res.fetchone()
    if row and row.close is not None:
        return float(row.close)
    return None


async def _analyze_shadow(db, shadow: ShadowTrade) -> None:
    """Processa um único shadow TIMEOUT: coleta dados pós-saída e grava resultados.

    Nunca propaga exceção — falha silenciosa + timeout_post_analysis_done=TRUE
    para evitar reprocessamento infinito de trade com dados ausentes.
    """
    shadow_id = shadow.id
    symbol = shadow.symbol
    exit_ts = shadow.exit_timestamp
    entry_price = float(shadow.entry_price) if shadow.entry_price is not None else None
    tp_price = float(shadow.tp_price) if shadow.tp_price is not None else None

    try:
        if exit_ts is None:
            logger.warning(
                "[timeout-analyzer] shadow %s sem exit_timestamp — skipping", shadow_id
            )
            shadow.timeout_post_analysis_done = True
            return

        # Garante timezone-aware para aritmética
        if exit_ts.tzinfo is None:
            exit_ts = exit_ts.replace(tzinfo=timezone.utc)

        window_end = exit_ts + timedelta(hours=24)

        # ── 1. Preços nos horizontes fixos ───────────────────────────────
        for hours in _HORIZONS_H:
            target = exit_ts + timedelta(hours=hours)
            close = await _fetch_close_near_horizon(db, symbol, target)
            field = f"price_after_{hours}h"
            setattr(shadow, field, close)

        # ── 2. OHLCV completo das 24h pós-timeout ────────────────────────
        candles = await _fetch_ohlcv_window(db, symbol, exit_ts, window_end)

        if not candles or entry_price is None or entry_price <= 0:
            shadow.timeout_post_analysis_done = True
            return

        # ── 3. Excursão pós-timeout (max_high, min_low) ──────────────────
        highs = [c["high"] for c in candles if c["high"] is not None]
        lows = [c["low"] for c in candles if c["low"] is not None]

        if highs:
            max_high = max(highs)
            shadow.max_profit_after_timeout_pct = (
                (max_high - entry_price) / entry_price * 100.0
            )
        if lows:
            min_low = min(lows)
            shadow.max_drawdown_after_timeout_pct = (
                (min_low - entry_price) / entry_price * 100.0
            )

        # ── 4. Delayed TP detection ──────────────────────────────────────
        if tp_price is not None and highs:
            max_high = max(highs)
            if max_high >= tp_price:
                shadow.delayed_tp = True
                # Tempo até primeira candle onde high >= tp_price
                for candle in candles:
                    if candle["high"] is not None and candle["high"] >= tp_price:
                        candle_ts = candle["time"]
                        if candle_ts.tzinfo is None:
                            candle_ts = candle_ts.replace(tzinfo=timezone.utc)
                        delta_h = (candle_ts - exit_ts).total_seconds() / 3600.0
                        shadow.delayed_tp_hours = round(delta_h, 4)
                        break
            else:
                shadow.delayed_tp = False
                shadow.delayed_tp_hours = None
        else:
            shadow.delayed_tp = False
            shadow.delayed_tp_hours = None

    except Exception as exc:
        logger.error(
            "[timeout-analyzer] Erro ao analisar shadow %s (%s): %s",
            shadow_id, symbol, exc,
        )
    finally:
        # Marca como processado independentemente de sucesso/falha
        shadow.timeout_post_analysis_done = True


async def _run_analyzer() -> None:
    """Corpo async da task — busca batch de shadows pendentes e analisa."""
    from ..database import get_celery_session

    async with get_celery_session() as db:
        # Busca batch de TIMEOUT não processados, ordenado por exit_timestamp ASC
        # (backfill progressivo — trades mais antigos primeiro).
        stmt = (
            select(ShadowTrade)
            .where(
                ShadowTrade.outcome == "TIMEOUT",
                ShadowTrade.timeout_post_analysis_done.is_(False)
                | ShadowTrade.timeout_post_analysis_done.is_(None),
                ShadowTrade.exit_timestamp.is_not(None),
            )
            .order_by(ShadowTrade.exit_timestamp.asc())
            .limit(SHADOW_ANALYZER_BATCH_SIZE)
            .with_for_update(skip_locked=True)
        )
        result = await db.execute(stmt)
        shadows = list(result.scalars().all())

        if not shadows:
            logger.debug("[timeout-analyzer] Nenhum shadow TIMEOUT pendente.")
            return

        logger.info(
            "[timeout-analyzer] Processando %d shadows TIMEOUT...", len(shadows)
        )

        processed = 0
        delayed_tp_count = 0

        for shadow in shadows:
            await _analyze_shadow(db, shadow)
            processed += 1
            if shadow.delayed_tp:
                delayed_tp_count += 1

        await db.commit()

        logger.info(
            "[timeout-analyzer] %d/%d processados | delayed_tp=%d (%.1f%%)",
            processed,
            len(shadows),
            delayed_tp_count,
            (delayed_tp_count / processed * 100) if processed else 0.0,
        )


@celery_app.task(name="app.tasks.shadow_timeout_analyzer.analyze")
def analyze() -> None:
    """Celery task: processa batch de shadow TIMEOUT para post-analysis passiva."""
    _run_async(_run_analyzer())
