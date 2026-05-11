"""Celery Task — collect 30-minute structural OHLCV candles.

Architectural role (refactor Task #structural-30m):
    This task replaces the OHLCV-1h fetch that was embedded inside
    ``collect_market_data.collect_all``. That coupling forced the heavy
    per-symbol Gate.io fetch loop (95 symbols × fetch_ohlcv × INSERT) to
    run every 60 s alongside the lightweight ticker/metadata path, causing:

        • ~5 700 OHLCV API calls/hour to Gate.io
        • Bulk UPSERT contention in ``ohlcv`` and ``market_metadata``
        • ``command_timeout`` overruns and orphan transactions

    The new design splits responsibilities:

        collect_all  @ 60 s   — ticker bulk + market_metadata only (fast)
        collect_structural_30m @ 0,30  — OHLCV 30m + enqueue compute_30m

Beat schedule:
    crontab(minute="0,30")  — fires exactly when a 30m candle closes on
    Gate.io (UTC-aligned), so the fetch always captures a closed candle.
    No partial-candle filtering needed.

Chain:
    collect_structural_30m
        └─ task_dispatch.enqueue → compute_indicators.compute_30m
                └─ task_dispatch.enqueue → compute_scores.score
                        └─ task_dispatch.enqueue → evaluate_signals.evaluate

Invariants enforced:
    • Only ``is_active = true`` pool coins are fetched (ingestion gate,
      Task #232). ``is_tradable`` is irrelevant here — indicators must flow
      for all monitored symbols so L2/L3 and dashboards stay fresh.
    • Symbols are sorted ASC before any DB write — deadlock-prevention
      invariant (Task #251): Postgres acquires row-locks in tuple order;
      all writers must use the same order.
    • OHLCV rows use ON CONFLICT DO NOTHING — idempotent; safe to re-run.
    • Chain fires only when count > 0 — no compute enqueue for empty cycles.
    • Persistence-queue path (``_pq.is_enabled()``) mirrors the pattern
      established in ``collect_market_data.collect_all`` (Task #236).
"""

import asyncio
import logging
from datetime import datetime, timezone

from ..services import persistence as _pq
from ..tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

_REQUIRED_OHLCV_COLUMNS = ["time", "open", "high", "low", "close", "volume"]

# Timeframe this collector owns. Single source of truth — compute_30m reads
# the same literal so they stay in sync without config coupling.
_TIMEFRAME = "30m"

# Gate.io returns up to 1000 candles per request. 200 × 30m = 100h of history,
# enough for all indicator lookback windows (EMA200 being the longest).
_OHLCV_LIMIT = 200


async def _collect_structural_30m_async() -> int:
    """Fetch 30m OHLCV for all active pool coins and persist to TimescaleDB.

    Returns the number of symbols successfully collected. Zero means no
    symbols in the pool or all fetches failed — chain is skipped in that case.
    """
    from ..services.market_data_service import market_data_service
    from ..services.pool_service import get_active_pool_symbols, get_pool_symbols
    from ..database import run_db_task
    from sqlalchemy import text

    logger.info("[STRUCTURAL-30m] Starting 30m OHLCV collection (active spot symbols)…")

    # ── Load active symbols ───────────────────────────────────────────────────
    async def _load_spot_syms(db):
        active = await get_active_pool_symbols(db, "spot")
        total = await get_pool_symbols(db, "spot")
        return active, len(total)

    raw_symbols, pool_count = await run_db_task(_load_spot_syms, celery=True)

    logger.info(
        "[STRUCTURAL-30m] active_symbols=%d pool_count=%d",
        len(raw_symbols), pool_count,
    )

    if not raw_symbols:
        logger.warning(
            "[STRUCTURAL-30m] no active symbols — skipping cycle "
            "(pool_count=%d, active_count=0)",
            pool_count,
        )
        return 0

    # ── Validate and deduplicate ──────────────────────────────────────────────
    valid_symbols = []
    for sym in dict.fromkeys(raw_symbols):
        s = sym.strip().upper() if sym else ""
        if not s:
            logger.warning("[STRUCTURAL-30m][SKIP] raw=%r reason=empty", sym)
            continue
        if "USDT" not in s:
            logger.warning("[STRUCTURAL-30m][SKIP] raw=%r reason=no_usdt", sym)
            continue
        if not (5 <= len(s) <= 20):
            logger.warning(
                "[STRUCTURAL-30m][SKIP] raw=%r reason=invalid_length len=%d",
                sym, len(s),
            )
            continue
        valid_symbols.append(s)

    if not valid_symbols:
        logger.warning(
            "[STRUCTURAL-30m] all symbols failed validation — skipping cycle "
            "(pool_count=%d, active_count=%d, valid_count=0)",
            pool_count, len(raw_symbols),
        )
        return 0

    # Task #251: deterministic sort — deadlock-prevention invariant.
    symbols = sorted(valid_symbols)
    logger.info("[STRUCTURAL-30m] valid_symbols=%d", len(symbols))

    # ── Inner async function (runs inside a DB session) ───────────────────────
    async def _inner(db, queue_mode: bool = False) -> int:
        import time as _time
        import sqlalchemy.exc as _sqla_exc

        # Metrics import (optional — defensive)
        try:
            from ..services import ohlcv_metrics as _ohlcv_metrics
        except Exception:
            _ohlcv_metrics = None  # type: ignore[assignment]

        _cycle_t0 = _time.monotonic()
        collected = 0
        failures = 0

        if queue_mode and db.in_transaction():
            await db.rollback()
            logger.warning(
                "[STRUCTURAL-30m] Stale transaction on queue-mode session, rolled back"
            )

        for symbol in symbols:
            try:
                logger.info("[STRUCTURAL-30m][START] symbol=%s", symbol)

                df = await market_data_service.fetch_ohlcv(
                    symbol, _TIMEFRAME, limit=_OHLCV_LIMIT
                )

                rx_rows = (
                    len(df)
                    if df is not None and not getattr(df, "empty", True)
                    else 0
                )
                logger.info(
                    "[OHLCV-RX] symbol=%s timeframe=%s rows=%d",
                    symbol, _TIMEFRAME, rx_rows,
                )
                if _ohlcv_metrics is not None and rx_rows:
                    _ohlcv_metrics.record_received(symbol, _TIMEFRAME, rx_rows)

                if df is None or df.empty:
                    logger.error(
                        "[STRUCTURAL-30m][EMPTY] symbol=%s reason=%s",
                        symbol,
                        "fetch_returned_none" if df is None else "df_empty",
                    )
                    failures += 1
                    continue

                missing = [c for c in _REQUIRED_OHLCV_COLUMNS if c not in df.columns]
                if missing:
                    logger.error(
                        "[STRUCTURAL-30m][INVALID_COLUMNS] symbol=%s missing=%s",
                        symbol, missing,
                    )
                    failures += 1
                    continue

                ohlcv_exchange = df.attrs.get("exchange", "gate.io")
                logger.info(
                    "[OHLCV-PERSIST] symbol=%s rows=%d exchange=%s timeframe=%s",
                    symbol, len(df), ohlcv_exchange, _TIMEFRAME,
                )

                # ── Persist OHLCV ─────────────────────────────────────────────
                if queue_mode:
                    rows_payload = tuple(
                        {
                            "time":         row["time"],
                            "open":         float(row["open"]),
                            "high":         float(row["high"]),
                            "low":          float(row["low"]),
                            "close":        float(row["close"]),
                            "volume":       float(row["volume"]),
                            "quote_volume": float(
                                row.get(
                                    "quote_volume",
                                    float(row["close"]) * float(row["volume"]),
                                )
                            ),
                        }
                        for _, row in df.iterrows()
                    )
                    await _pq.enqueue_or_log(
                        producer="collect-structural-30m",
                        msg=_pq.OhlcvBatch(
                            category="ingest",
                            enqueued_at=_pq.now_monotonic(),
                            symbol=symbol,
                            exchange=ohlcv_exchange,
                            timeframe=_TIMEFRAME,
                            market_type="spot",
                            rows=rows_payload,
                        ),
                    )
                else:
                    # SAVEPOINT per symbol — failure of one never aborts the cycle.
                    try:
                        async with db.begin_nested():
                            for _, row in df.iterrows():
                                await db.execute(
                                    text("""
                                        INSERT INTO ohlcv
                                            (time, symbol, exchange, timeframe,
                                             market_type, open, high, low,
                                             close, volume, quote_volume)
                                        VALUES
                                            (:time, :symbol, :exchange, :timeframe,
                                             :market_type, :open, :high, :low,
                                             :close, :volume, :quote_volume)
                                        ON CONFLICT DO NOTHING
                                    """),
                                    {
                                        "time":         row["time"],
                                        "symbol":       symbol,
                                        "exchange":     ohlcv_exchange,
                                        "timeframe":    _TIMEFRAME,
                                        "market_type":  "spot",
                                        "open":         float(row["open"]),
                                        "high":         float(row["high"]),
                                        "low":          float(row["low"]),
                                        "close":        float(row["close"]),
                                        "volume":       float(row["volume"]),
                                        "quote_volume": float(
                                            row.get(
                                                "quote_volume",
                                                float(row["close"]) * float(row["volume"]),
                                            )
                                        ),
                                    },
                                )
                    except Exception as sp_exc:
                        if not db.is_active:
                            logger.error(
                                "[STRUCTURAL-30m] outer tx poisoned after %s — stopping. %s",
                                symbol, sp_exc,
                            )
                            failures += 1
                            break
                        logger.error(
                            "[STRUCTURAL-30m] SAVEPOINT failed for %s — savepoint "
                            "rolled back, continuing. %s",
                            symbol, sp_exc,
                        )
                        failures += 1
                        continue

                if _ohlcv_metrics is not None:
                    _ohlcv_metrics.record_received(symbol, _TIMEFRAME, len(df))

                logger.info("[STRUCTURAL-30m][OK] symbol=%s rows=%d", symbol, len(df))
                collected += 1

            except Exception as exc:
                _err_str = str(exc)
                if "InFailedSQLTransaction" in _err_str or \
                        "current transaction is aborted" in _err_str:
                    try:
                        await db.rollback()
                    except Exception:
                        pass
                    remaining = len(symbols) - symbols.index(symbol) - 1
                    logger.error(
                        "[STRUCTURAL-30m] outer tx aborted after %s — "
                        "rolled back, skipping %d remaining; collected=%d",
                        symbol, remaining, collected,
                    )
                    failures += 1
                    break
                import sqlalchemy.exc as _sqla_exc  # noqa: F811
                if isinstance(exc, _sqla_exc.PendingRollbackError):
                    await db.rollback()
                    logger.error(
                        "[STRUCTURAL-30m] PendingRollbackError after %s — "
                        "rolled back, stopping. %s",
                        symbol, exc,
                    )
                    failures += 1
                    break
                logger.error(
                    "[STRUCTURAL-30m][FAILED] symbol=%s error=%s",
                    symbol, exc, exc_info=True,
                )
                failures += 1
                if not db.is_active:
                    break
                continue

        _cycle_dt = _time.monotonic() - _cycle_t0
        _outcome = "ok" if collected > 0 else "fail"
        logger.info(
            "[OHLCV-COMMIT] cycle_done flow=30m outcome=%s success=%d "
            "fail=%d total=%d duration_s=%.2f",
            _outcome, collected, failures, len(symbols), _cycle_dt,
        )
        if collected == 0:
            # Não levantar — beat re-dispara em ≤30min, e o broker
            # global tem ``acks_late=False`` nesta task (ver gotcha #245).
            # Raise aqui faria SIGKILL/requeue cascatear ruído sem ganho;
            # o wrapper Celery já pula o chain quando count == 0.
            logger.warning(
                "[STRUCTURAL-30m] zero success — all %d symbols failed; "
                "compute_30m chain será pulado neste ciclo.",
                len(symbols),
            )
        return collected

    # ── Dispatch to persistence layer ─────────────────────────────────────────
    if _pq.is_enabled():
        from ..database import CeleryAsyncSessionLocal
        async with CeleryAsyncSessionLocal() as db:
            return await _inner(db, queue_mode=True)
    return await run_db_task(_inner, celery=True)


def _run_async(coro):
    """Run async code in sync Celery task.

    Drains pending asyncpg callbacks (NullPool connection close, etc.)
    before closing the loop. Without this, callbacks scheduled by asyncpg
    during connection cleanup hit a closed loop, leaving sessions in
    PendingRollbackError and poisoning the next task invocation.
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        try:
            pending = asyncio.all_tasks(loop)
            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
        except Exception:
            pass
        loop.close()


@celery_app.task(name="app.tasks.collect_structural_30m.run")
def run():
    """Celery entry point — collect 30m OHLCV and chain to compute_30m.

    Beat schedule: crontab(minute="0,30") — fires at candle close only.
    Chain is skipped when count == 0 (empty pool or all fetches failed).
    """
    count = _run_async(_collect_structural_30m_async())

    # Chain to compute_30m only when there is fresh data to process.
    # TTL = compute_30m time_limit (600s) + 60s margin.
    if count > 0:
        from . import task_dispatch
        task_dispatch.enqueue(
            "app.tasks.compute_indicators.compute_30m",
            dedup_key="compute",
            ttl_seconds=660,
        )
        logger.info(
            "[STRUCTURAL-30m] collected=%d — compute_30m enqueued", count
        )
    else:
        logger.warning(
            "[STRUCTURAL-30m] collected=0 — compute_30m chain skipped"
        )

    return f"[STRUCTURAL-30m] Collected 30m OHLCV for {count} symbols"
