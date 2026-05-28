"""TTT Analyzer — Time-To-Target Post-Analysis.

Para cada shadow com ttt_enabled=TRUE e ttt_analysis_done=FALSE,
consulta OHLCV histórico para preencher as métricas TTT que não puderam
ser capturadas inline (caminho live-close ou símbolos sem candles 1m).

Regras obrigatórias
-------------------
* NÃO reabre trades. NÃO altera outcome, pnl_pct, TP/SL originais.
* ttt_outcome é EXCLUSIVAMENTE label de ML — nunca afeta P&L real.
* Idempotente: ttt_analysis_done=TRUE bloqueia reprocessamento.
* Processa em batches (TTT_ANALYZER_BATCH_SIZE, default 100) ordenados
  por completed_at ASC (backfill progressivo — trades mais antigos primeiro).
* Nunca propaga exceção por shadow: falha silenciosa + ttt_analysis_done=TRUE
  para evitar loop infinito em trade com OHLCV ausente.

Campos preenchidos (migration 065)
------------------------------------
time_to_tp_minutes        — minutos até price >= ttt_tp_pct desde entry_ts
max_profit_first_15m/30m/60m — max_high em janela [entry, entry+Xmin]
candles_to_peak           — candle index do max_high total
candles_to_first_positive — candle index do primeiro close > entry_price
ttt_outcome               — 'FAST_WIN' | 'TIMEOUT'
ttt_close_reason          — 'TP_HIT_IN_WINDOW' | 'HARD_TIMEOUT'
ttt_fast_win_bucket       — 'WIN_0_15M' | 'WIN_15_30M' | 'WIN_30_60M' | 'WIN_60_180M'
elapsed_minutes           — (backfill se ainda NULL)
profit_velocity           — (backfill se ainda NULL)
profit_velocity_per_hour  — (backfill se ainda NULL)
ttt_analysis_done         — TRUE ao finalizar (sucesso ou falha graceful)

Knobs (env)
-----------
* TTT_ANALYZER_BATCH_SIZE   (default 100) — shadows por execução.
* TTT_ANALYZER_INTERVAL_S   (default 3600) — beat schedule (1h).
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


TTT_ANALYZER_BATCH_SIZE = _env_int("TTT_ANALYZER_BATCH_SIZE", 100)


def _run_async(coro):
    """Run async coroutine em task Celery síncrono.

    Padrão canonical 5-step teardown (Task #274).
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
            logger.debug("[ttt-analyzer] pending-task drain: %s", exc)

        try:
            from ..database import _celery_engine
            loop.run_until_complete(_celery_engine.dispose())
            loop.run_until_complete(asyncio.sleep(0))
        except BaseException as exc:
            logger.debug("[ttt-analyzer] engine dispose: %s", exc)

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
            logger.debug("[ttt-analyzer] hard-terminate: %s", exc)

        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        except BaseException as exc:
            logger.debug("[ttt-analyzer] shutdown_asyncgens: %s", exc)

        try:
            loop.close()
        except BaseException as exc:
            logger.debug("[ttt-analyzer] loop.close: %s", exc)
        try:
            asyncio.set_event_loop(None)
        except BaseException:
            pass


async def _fetch_ohlcv_window(
    db,
    symbol: str,
    t_start: datetime,
    t_end: datetime,
) -> List[Dict[str, Any]]:
    """Candles 1m em (t_start, t_end] para o símbolo."""
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
        {"s": symbol, "t_start": t_start, "t_end": t_end},
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


def _set_ttt_fast_win_bucket(shadow: ShadowTrade) -> None:
    t = shadow.time_to_tp_minutes or 0.0
    if t < 15.0:
        shadow.ttt_fast_win_bucket = "WIN_0_15M"
    elif t < 30.0:
        shadow.ttt_fast_win_bucket = "WIN_15_30M"
    elif t < 60.0:
        shadow.ttt_fast_win_bucket = "WIN_30_60M"
    else:
        shadow.ttt_fast_win_bucket = "WIN_60_180M"


async def _analyze_ttt_shadow(db, shadow: ShadowTrade) -> None:
    """Computa métricas TTT para um shadow via OHLCV histórico.

    Nunca propaga exceção — falha silenciosa + ttt_analysis_done=TRUE
    para evitar reprocessamento infinito (OHLCV pode estar ausente).
    """
    shadow_id = shadow.id
    symbol = shadow.symbol
    entry_ts = shadow.entry_timestamp
    entry_price = float(shadow.entry_price) if shadow.entry_price is not None else None
    ttt_tp_pct = float(shadow.ttt_tp_pct) if shadow.ttt_tp_pct is not None else 1.0
    ttt_timeout_m = int(shadow.ttt_timeout_minutes) if shadow.ttt_timeout_minutes is not None else 180

    try:
        if entry_ts is None or entry_price is None or entry_price <= 0:
            logger.warning(
                "[ttt-analyzer] shadow %s sem entry_ts/entry_price — skip", shadow_id
            )
            return

        # Garante timezone-aware para aritmética.
        if entry_ts.tzinfo is None:
            entry_ts = entry_ts.replace(tzinfo=timezone.utc)

        ttt_tp_price = entry_price * (1.0 + ttt_tp_pct / 100.0)
        window_end = entry_ts + timedelta(minutes=ttt_timeout_m)

        # ── OHLCV da janela TTT ───────────────────────────────────────────
        candles = await _fetch_ohlcv_window(db, symbol, entry_ts, window_end)

        if not candles:
            # Sem OHLCV 1m disponível — aplica TIMEOUT por ausência de dados.
            # Não temos como saber se TP foi atingido.
            shadow.ttt_outcome = "TIMEOUT"
            shadow.ttt_close_reason = "HARD_TIMEOUT"
            logger.debug(
                "[ttt-analyzer] shadow %s sem OHLCV 1m — TIMEOUT por ausência",
                shadow_id,
            )
            return

        # ── Scan candle-a-candle para métricas TTT ────────────────────────
        running_max_high: Optional[float] = None
        running_max_high_idx: Optional[int] = None

        for idx, c in enumerate(candles, start=1):
            ch = c["high"]
            cl = c["low"]
            cc = c["close"]
            c_ts = c["time"]

            if ch is None:
                continue

            # Atualiza max running (para candles_to_peak e milestones).
            if running_max_high is None or ch > running_max_high:
                running_max_high = ch
                running_max_high_idx = idx

            # Milestones de lucro máximo por janela temporal.
            if entry_price > 0 and running_max_high is not None:
                if idx == 15 and shadow.max_profit_first_15m is None:
                    shadow.max_profit_first_15m = round(
                        (running_max_high - entry_price) / entry_price * 100.0, 6
                    )
                if idx == 30 and shadow.max_profit_first_30m is None:
                    shadow.max_profit_first_30m = round(
                        (running_max_high - entry_price) / entry_price * 100.0, 6
                    )
                if idx == 60 and shadow.max_profit_first_60m is None:
                    shadow.max_profit_first_60m = round(
                        (running_max_high - entry_price) / entry_price * 100.0, 6
                    )

            # candles_to_first_positive
            if shadow.candles_to_first_positive is None and cc is not None:
                if cc > entry_price:
                    shadow.candles_to_first_positive = idx

            # time_to_tp_minutes: primeira vez que high >= ttt_tp_price.
            if shadow.time_to_tp_minutes is None and ch >= ttt_tp_price:
                if c_ts is not None:
                    if c_ts.tzinfo is None:
                        c_ts = c_ts.replace(tzinfo=timezone.utc)
                    try:
                        delta_m = (c_ts - entry_ts).total_seconds() / 60.0
                        shadow.time_to_tp_minutes = round(max(delta_m, 0.0), 4)
                    except TypeError:
                        pass

        # candles_to_peak
        if running_max_high_idx is not None and shadow.candles_to_peak is None:
            shadow.candles_to_peak = running_max_high_idx

        # Milestones que ficaram além do scope das candles disponíveis —
        # preenche com o max visto até ao final da janela.
        if running_max_high is not None and entry_price > 0:
            mfe_total = round(
                (running_max_high - entry_price) / entry_price * 100.0, 6
            )
            if shadow.max_profit_first_15m is None and len(candles) >= 15:
                # Candle 15 existia mas high era None — usa fallback do max total.
                shadow.max_profit_first_15m = mfe_total
            if shadow.max_profit_first_30m is None and len(candles) >= 30:
                shadow.max_profit_first_30m = mfe_total
            if shadow.max_profit_first_60m is None and len(candles) >= 60:
                shadow.max_profit_first_60m = mfe_total

        # ── Determina ttt_outcome ─────────────────────────────────────────
        if shadow.time_to_tp_minutes is not None:
            shadow.ttt_outcome = "FAST_WIN"
            shadow.ttt_close_reason = "TP_HIT_IN_WINDOW"
            _set_ttt_fast_win_bucket(shadow)
            logger.info(
                "[ttt-analyzer] shadow %s symbol=%s FAST_WIN bucket=%s "
                "time_to_tp=%.1fmin",
                shadow_id, symbol, shadow.ttt_fast_win_bucket,
                shadow.time_to_tp_minutes,
            )
        else:
            shadow.ttt_outcome = "TIMEOUT"
            shadow.ttt_close_reason = "HARD_TIMEOUT"
            logger.info(
                "[ttt-analyzer] shadow %s symbol=%s TIMEOUT — ttt_tp=%.2f%% "
                "não atingido em %d candles",
                shadow_id, symbol, ttt_tp_pct, len(candles),
            )

        # ── Backfill de métricas de velocidade (se ainda NULL) ────────────
        if shadow.elapsed_minutes is None and shadow.holding_seconds is not None:
            shadow.elapsed_minutes = round(shadow.holding_seconds / 60.0, 4)
        if (
            shadow.profit_velocity is None
            and shadow.max_profit_pct is not None
            and shadow.elapsed_minutes is not None
        ):
            elapsed_safe_min = max(shadow.elapsed_minutes, 1.0)
            elapsed_safe_h = max(shadow.elapsed_minutes / 60.0, 1.0 / 60.0)
            shadow.profit_velocity = round(
                float(shadow.max_profit_pct) / elapsed_safe_min, 6
            )
            shadow.profit_velocity_per_hour = round(
                float(shadow.max_profit_pct) / elapsed_safe_h, 4
            )

    except Exception as exc:
        logger.error(
            "[ttt-analyzer] Erro ao analisar shadow %s (%s): %s",
            shadow_id, symbol, exc, exc_info=True,
        )
    finally:
        # Marca como processado independentemente de sucesso/falha.
        # Evita loop infinito para trades com OHLCV permanentemente ausente.
        shadow.ttt_analysis_done = True


async def _run_analyzer() -> None:
    """Corpo async da task — processa batch de shadows TTT pendentes."""
    from ..database import get_celery_session

    async with get_celery_session() as db:
        stmt = (
            select(ShadowTrade)
            .where(
                ShadowTrade.ttt_enabled.is_(True),
                ShadowTrade.status == "COMPLETED",
                ShadowTrade.ttt_analysis_done.is_(False)
                | ShadowTrade.ttt_analysis_done.is_(None),
            )
            .order_by(ShadowTrade.completed_at.asc())
            .limit(TTT_ANALYZER_BATCH_SIZE)
            .with_for_update(skip_locked=True)
        )
        result = await db.execute(stmt)
        shadows = list(result.scalars().all())

        if not shadows:
            logger.debug("[ttt-analyzer] Nenhum shadow TTT pendente.")
            return

        logger.info(
            "[ttt-analyzer] Processando %d shadows TTT pendentes...", len(shadows)
        )

        fast_win = 0
        timeout_count = 0

        for shadow in shadows:
            await _analyze_ttt_shadow(db, shadow)
            if shadow.ttt_outcome == "FAST_WIN":
                fast_win += 1
            else:
                timeout_count += 1

        await db.commit()

        total = len(shadows)
        logger.info(
            "[ttt-analyzer] %d processados | FAST_WIN=%d (%.1f%%) | TIMEOUT=%d (%.1f%%)",
            total,
            fast_win, fast_win / total * 100 if total else 0.0,
            timeout_count, timeout_count / total * 100 if total else 0.0,
        )


@celery_app.task(name="app.tasks.ttt_analyzer.analyze")
def analyze() -> None:
    """Celery task: processa batch de shadows TTT para post-analysis."""
    _run_async(_run_analyzer())
