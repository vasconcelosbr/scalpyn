"""Shadow Trade Monitor — Fase 3.

Avança shadow trades em PENDING/RUNNING candle-a-candle (1m) até
atingir TP, SL ou timeout, e replica o resultado em
``trade_simulations`` com ``source='SHADOW'`` (alimenta
``DatasetBuilder.load_simulations``).

Regras
------
* **SL antes de TP** na mesma candle (regra conservadora explícita).
  Quando ``low <= sl_price`` E ``high >= tp_price`` na mesma candle,
  assumimos SL_HIT — pior caso para o trader.
* Timeout: ``timeout_candles`` vem de ``config_snapshot``
  (``SHADOW_TIMEOUT_CANDLES`` env quando ausente).
* Batch máximo ``SHADOW_MONITOR_BATCH_SIZE`` (default 50) por execução,
  iterando IDs em ordem ``sorted()`` (deadlock-safety, gotcha #251/#273).
* Janela máxima por shadow ``SHADOW_MONITOR_MAX_CANDLES_PER_RUN``
  (default 720 = 12 h de 1m) — evita um shadow muito antigo monopolizar
  o ciclo. Se passar disso sem outcome, atualiza ``last_processed_time``
  e o próximo tick continua.
* ``acks_late=False`` (idempotente, beat re-roda; gotcha #245).

Knobs (env)
-----------
* ``SHADOW_MONITOR_INTERVAL_S`` (default 300) — beat schedule.
* ``SHADOW_MONITOR_BATCH_SIZE`` (default 50).
* ``SHADOW_MONITOR_MAX_CANDLES_PER_RUN`` (default 720).
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
from ..services import indicators_provider, shadow_trade_service

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


SHADOW_MONITOR_BATCH_SIZE = _env_int("SHADOW_MONITOR_BATCH_SIZE", 50)
SHADOW_MONITOR_MAX_CANDLES_PER_RUN = _env_int("SHADOW_MONITOR_MAX_CANDLES_PER_RUN", 720)


def _run_async(coro):
    """Run async coroutine in a sync Celery task — same pattern as
    ``health_checks._run_async`` (drains pending asyncpg tasks before
    closing the loop)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        try:
            pending = asyncio.all_tasks(loop)
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        except Exception:
            pass
        loop.close()


async def _fetch_candles(
    db, symbol: str, after_ts: datetime, limit: int
) -> List[Dict[str, Any]]:
    """1m OHLCV candles após ``after_ts`` (exclusive), em ordem temporal."""
    res = await db.execute(
        text(
            """
            SELECT time, open, high, low, close
              FROM ohlcv
             WHERE symbol = :s
               AND timeframe = '1m'
               AND time > :t
             ORDER BY time ASC
             LIMIT :lim
            """
        ),
        {"s": symbol, "t": after_ts, "lim": limit},
    )
    return [
        {
            "time": r.time,
            "open": float(r.open) if r.open is not None else None,
            "high": float(r.high) if r.high is not None else None,
            "low": float(r.low) if r.low is not None else None,
            "close": float(r.close) if r.close is not None else None,
        }
        for r in res.fetchall()
    ]


async def _ensure_entry(db, shadow: ShadowTrade) -> bool:
    """Garante que o shadow tem ``entry_price`` + ``entry_timestamp``.

    Novo fluxo (Task 2026-05-13): a criação já tenta preencher entry
    com o preço CORRENTE multi-timeframe (1m/5m/15m/30m), então a
    grande maioria dos shadows nasce em RUNNING. Esse helper cobre:

    * shadows legados criados antes da migration (status PENDING +
      entry NULL — backfill on-the-fly aqui);
    * raros casos onde nem candle 1m, nem 5m/15m/30m estavam
      disponíveis no instante da decisão.

    Estratégia: tenta primeiro o preço corrente multi-tf (mesmo helper
    usado pelo creator). Só recai pro ``_next_1m_open`` legado se nada
    disponível — mantendo o contrato antigo como último recurso.
    """
    if shadow.entry_price is not None and shadow.entry_timestamp is not None:
        return True

    entry_price, entry_ts = await shadow_trade_service._get_current_price_multi_tf(
        db, shadow.symbol
    )
    if entry_price is None or entry_ts is None:
        entry_price, entry_ts = await shadow_trade_service._next_1m_open(
            db, shadow.symbol, shadow.created_at
        )
    if entry_price is None or entry_ts is None:
        return False

    shadow.entry_price = entry_price
    shadow.entry_timestamp = entry_ts
    # Promove legado a RUNNING assim que a entrada é resolvida — caso
    # contrário, se não houver candles 1m novas neste tick, o trade
    # ficaria PENDING mesmo já tendo entry_price (ressalva do review).
    if shadow.status == "PENDING":
        shadow.status = "RUNNING"
    if shadow.last_processed_time is None:
        shadow.last_processed_time = entry_ts
    if (
        shadow.tp_pct is not None
        and shadow.sl_pct is not None
        and entry_price > 0
    ):
        shadow.tp_price = entry_price * (1 + float(shadow.tp_pct) / 100.0)
        shadow.sl_price = entry_price * (1 - float(shadow.sl_pct) / 100.0)
    return True


async def _advance_shadow(db, shadow: ShadowTrade) -> str:
    """Avança um único shadow trade até outcome ou esgotar candles do tick.

    Retorna um label de transição: ``"completed"``, ``"running"`` ou
    ``"pending"``.
    """
    if not await _ensure_entry(db, shadow):
        # Sem candle 1m disponível ainda — deixa em PENDING, próximo tick.
        return "pending"

    # Sem TP/SL utilizáveis (config sem tp_pct/sl_pct) → não dá pra simular.
    if shadow.tp_price is None or shadow.sl_price is None:
        logger.warning(
            "[shadow-monitor] shadow_id=%s sem tp/sl utilizáveis (tp_pct=%s "
            "sl_pct=%s) — marcado ERROR",
            shadow.id, shadow.tp_pct, shadow.sl_pct,
        )
        shadow.status = "ERROR"
        shadow.completed_at = datetime.now(timezone.utc)
        return "completed"

    after_ts = shadow.last_processed_time or shadow.entry_timestamp
    candles = await _fetch_candles(
        db, shadow.symbol, after_ts, SHADOW_MONITOR_MAX_CANDLES_PER_RUN
    )
    if not candles:
        # Sem candles novas — mantém status atual.
        return "pending" if shadow.status == "PENDING" else "running"

    # Quantas candles já vimos antes deste tick?
    timeout_candles = int(shadow.timeout_candles or 0) or None
    candles_seen_before = 0
    if timeout_candles and shadow.last_processed_time and shadow.entry_timestamp:
        delta = shadow.last_processed_time - shadow.entry_timestamp
        candles_seen_before = max(int(delta.total_seconds() // 60), 0)

    tp = float(shadow.tp_price)
    sl = float(shadow.sl_price)
    entry_price = float(shadow.entry_price)
    outcome: Optional[str] = None
    exit_price: Optional[float] = None
    exit_ts: Optional[datetime] = None
    last_seen_ts: Optional[datetime] = None

    for idx, c in enumerate(candles, start=1):
        last_seen_ts = c["time"]
        if c["high"] is None or c["low"] is None:
            continue
        # SL antes de TP na mesma candle — regra conservadora.
        if c["low"] <= sl:
            outcome = "SL_HIT"
            exit_price = sl
            exit_ts = c["time"]
            break
        if c["high"] >= tp:
            outcome = "TP_HIT"
            exit_price = tp
            exit_ts = c["time"]
            break
        # Timeout (count global desde entry, NÃO só dentro do tick atual).
        if timeout_candles and (candles_seen_before + idx) >= timeout_candles:
            outcome = "TIMEOUT"
            exit_price = c["close"] if c["close"] is not None else c["open"]
            exit_ts = c["time"]
            break

    if outcome is None:
        # Não atingiu TP/SL/timeout dentro da janela — registra progresso
        # e passa pra próxima execução.
        shadow.last_processed_time = last_seen_ts
        if shadow.status == "PENDING":
            shadow.status = "RUNNING"
            return "running"
        return "running"

    # Outcome atingido — atualiza shadow + record_as_simulation.
    shadow.outcome = outcome
    shadow.exit_price = exit_price
    shadow.exit_timestamp = exit_ts
    if entry_price > 0 and exit_price is not None:
        pnl_pct = (exit_price - entry_price) / entry_price * 100.0
        shadow.pnl_pct = pnl_pct
        shadow.pnl_usdt = float(shadow.amount_usdt) * pnl_pct / 100.0
    if shadow.entry_timestamp and exit_ts:
        shadow.holding_seconds = int(
            (exit_ts - shadow.entry_timestamp).total_seconds()
        )
    shadow.status = "COMPLETED"
    shadow.completed_at = datetime.now(timezone.utc)
    shadow.last_processed_time = exit_ts

    # Captura indicadores no momento da SAÍDA (Task 2026-05-13).
    # Mesmo formato FLAT do entry (build_indicators_snapshot devolve o
    # envelope {value, source_group, ts, stale}; aqui achatamos para
    # {key: value} via _build_features_snapshot-style, alimentando o
    # ML com "indicadores na entrada vs indicadores na saída").
    # Nunca propaga falha — perda de snapshot de saída não anula o
    # outcome; só loga e segue.
    try:
        merged_map = await indicators_provider.get_merged_indicators(
            db, [shadow.symbol], include_stale=True
        )
        merged = merged_map.get(shadow.symbol)
        if merged is not None:
            envelope = indicators_provider.build_indicators_snapshot(
                merged, keys=list(merged.values.keys())
            )
            shadow.features_snapshot_exit = {
                k: (v.get("value") if isinstance(v, dict) else v)
                for k, v in envelope.items()
            }
        else:
            shadow.features_snapshot_exit = {}
    except Exception:
        logger.exception(
            "[shadow-monitor] features_snapshot_exit failed for shadow_id=%s "
            "— outcome persisted, exit snapshot empty",
            shadow.id,
        )

    try:
        await shadow_trade_service.record_as_simulation(db, shadow)
    except Exception:
        logger.exception(
            "[shadow-monitor] record_as_simulation failed for shadow_id=%s — "
            "shadow stays COMPLETED, simulation row missing",
            shadow.id,
        )
    return "completed"


async def _monitor_async() -> Dict[str, int]:
    """Uma execução do monitor — processa até ``BATCH_SIZE`` shadows."""
    from ..database import CeleryAsyncSessionLocal

    summary = {"processed": 0, "completed": 0, "errors": 0}

    async with CeleryAsyncSessionLocal() as db:
        async with db.begin():
            # Carrega batch determinístico (sorted by id) — gotcha #251/#273.
            # FOR UPDATE SKIP LOCKED garante que duas execuções
            # concorrentes do monitor (ad-hoc dispatch + beat tick
            # sobreposto, ou múltiplos workers da execution queue) NÃO
            # processem o mesmo shadow_trade no mesmo tick.
            res = await db.execute(
                select(ShadowTrade)
                .where(ShadowTrade.status.in_(("PENDING", "RUNNING")))
                .order_by(ShadowTrade.id.asc())
                .with_for_update(skip_locked=True)
                .limit(SHADOW_MONITOR_BATCH_SIZE)
            )
            shadows = list(res.scalars().all())
            # Re-sort defensivamente — ORM já devolve em ordem por
            # ORDER BY, mas sorted() reforça invariante deadlock-safety.
            shadows.sort(key=lambda s: s.id)

            for shadow in shadows:
                summary["processed"] += 1
                try:
                    transition = await _advance_shadow(db, shadow)
                    if transition == "completed":
                        summary["completed"] += 1
                except Exception:
                    summary["errors"] += 1
                    logger.exception(
                        "[shadow-monitor] advance failed for shadow_id=%s",
                        shadow.id,
                    )

    return summary


@celery_app.task(name="app.tasks.shadow_trade_monitor.run", bind=True)
def run(self) -> str:
    """Beat-driven monitor — default a cada ``SHADOW_MONITOR_INTERVAL_S`` s."""
    try:
        result = _run_async(_monitor_async())
        msg = (
            f"Shadow monitor: {result['processed']} processed, "
            f"{result['completed']} completed, {result['errors']} errors"
        )
        logger.info("[shadow-monitor] %s", msg)
        return msg
    except Exception as exc:
        logger.error("[shadow-monitor] task failed: %s", exc, exc_info=True)
        raise
