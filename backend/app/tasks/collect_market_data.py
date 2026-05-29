"""Celery Task — collect market data from exchanges into TimescaleDB."""

import asyncio
import logging
from datetime import datetime, timezone

from ..services import persistence as _pq
from ..tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

_REQUIRED_OHLCV_COLUMNS = ["time", "open", "high", "low", "close", "volume"]

# Task #251: chunk size for bulk UPSERT into market_metadata. Postgres
# tolerates much larger batches, but 200 keeps the SAVEPOINT window short
# (sub-second under contention) so a transient lock wait doesn't block the
# rest of the cycle. ~500 USDT pairs/cycle → 3 chunks.
_MARKET_METADATA_BULK_CHUNK = 200


async def _bulk_upsert_market_metadata(db, rows: list[dict], origin: str) -> int:
    """Task #251: single-statement multi-row UPSERT into ``market_metadata``.

    Replaces the per-ticker SAVEPOINT+UPSERT loop in ``collect_all`` and
    ``collect_5m`` (~500 round-trips/cycle → 3 statements). Postgres acquires
    row-locks in the order rows appear in the VALUES tuple, so callers MUST
    pre-sort ``rows`` by ``symbol`` ASC — that's the deadlock-prevention
    invariant this whole task is built around.

    Each row is a dict with keys: ``symbol``, ``price``, ``change``,
    ``volume``, ``spread``, ``updated``. Caller must filter out invalid
    rows (price <= 0, missing pair) BEFORE passing — bulk path does no
    per-row validation.

    Fault isolation: the bulk INSERT runs inside a SAVEPOINT per chunk. If
    a chunk fails (e.g. transient lock, a bad row that escaped validation),
    the SAVEPOINT auto-rolls back and the caller falls back to per-row
    UPSERT for THAT chunk only — rest of the cycle keeps going. Outer
    transaction is preserved per the "Nested-savepoint rollback rule"
    gotcha (no ``db.rollback()`` here).

    Returns the number of rows successfully upserted (best-effort).
    """
    from sqlalchemy import text

    if not rows:
        return 0

    sql_template = """
        INSERT INTO market_metadata
            (symbol, price, price_change_24h, volume_24h,
             spread_pct, last_updated, volume_24h_updated_at)
        VALUES {placeholders}
        ON CONFLICT (symbol) DO UPDATE SET
            price = EXCLUDED.price,
            price_change_24h = EXCLUDED.price_change_24h,
            volume_24h = EXCLUDED.volume_24h,
            spread_pct = EXCLUDED.spread_pct,
            last_updated = EXCLUDED.last_updated,
            volume_24h_updated_at = EXCLUDED.volume_24h_updated_at
    """

    upserted = 0
    for chunk_start in range(0, len(rows), _MARKET_METADATA_BULK_CHUNK):
        chunk = rows[chunk_start:chunk_start + _MARKET_METADATA_BULK_CHUNK]
        placeholders = []
        params: dict = {}
        for i, r in enumerate(chunk):
            placeholders.append(
                f"(:s{i}, :p{i}, :c{i}, :v{i}, :sp{i}, :u{i}, :u{i})"
            )
            params[f"s{i}"] = r["symbol"]
            params[f"p{i}"] = r["price"]
            params[f"c{i}"] = r["change"]
            params[f"v{i}"] = r["volume"]
            params[f"sp{i}"] = r["spread"]
            params[f"u{i}"] = r["updated"]
        sql = sql_template.format(placeholders=", ".join(placeholders))

        try:
            async with db.begin_nested():
                await db.execute(text(sql), params)
            upserted += len(chunk)
        except Exception as bulk_exc:
            # SAVEPOINT auto-rolled back. Per "Nested-savepoint rollback
            # rule" gotcha, do NOT call db.rollback().
            if not db.is_active:
                logger.error(
                    "[BULK-UPSERT %s] outer tx poisoned mid-chunk %d/%d — stopping",
                    origin, chunk_start, len(rows),
                )
                break
            logger.warning(
                "[BULK-UPSERT %s] chunk %d-%d failed (%s) — falling back to per-row",
                origin, chunk_start, chunk_start + len(chunk), bulk_exc,
            )
            # Per-row fallback for THIS chunk only. Symbols still in sorted
            # order, so deadlock invariant preserved across workers.
            row_sql = text("""
                INSERT INTO market_metadata
                    (symbol, price, price_change_24h, volume_24h,
                     spread_pct, last_updated, volume_24h_updated_at)
                VALUES (:symbol, :price, :change, :volume, :spread,
                        :updated, :updated)
                ON CONFLICT (symbol) DO UPDATE SET
                    price = EXCLUDED.price,
                    price_change_24h = EXCLUDED.price_change_24h,
                    volume_24h = EXCLUDED.volume_24h,
                    spread_pct = EXCLUDED.spread_pct,
                    last_updated = EXCLUDED.last_updated,
                    volume_24h_updated_at = EXCLUDED.volume_24h_updated_at
            """)
            for r in chunk:
                if not db.is_active:
                    logger.error(
                        "[BULK-UPSERT %s] outer tx poisoned during fallback — stopping",
                        origin,
                    )
                    return upserted
                try:
                    async with db.begin_nested():
                        await db.execute(row_sql, r)
                    upserted += 1
                except Exception as row_exc:
                    logger.debug(
                        "[BULK-UPSERT %s] per-row fallback failed for %s: %s",
                        origin, r.get("symbol", "?"), row_exc,
                    )
                    continue

    return upserted


def _run_async(coro):
    """Run async coroutine in a sync Celery task.

    Creates a dedicated event loop per task invocation. Drains all pending
    asyncpg tasks and disposes the NullPool engine before closing the loop.

    Without dispose + drain, asyncpg schedules ``_terminate_graceful_close``
    via ``loop.create_task()`` during GC of NullPool connections after
    ``loop.close()``, causing ``RuntimeError: Event loop is closed`` on the
    next invocation.

    Task #274 — robust teardown.
    Even when ``coro`` raises (``PendingRollbackError``, ``OperationalError``,
    ``CancelledError``, deadlock cascades, etc.) the finally block must NEVER
    let a teardown traceback escape to the root logger. The previous version
    drained pending tasks + ``dispose()``-ed the engine, but a connection
    whose asyncpg waiter was still pending (deadlock → poisoned outer tx)
    could be GC'd AFTER ``loop.close()``; ``__del__`` fires
    ``_cancel_current_command`` → ``loop.create_task(...)`` on the closed
    loop → ``RuntimeError: Event loop is closed`` (23 in 30 min on
    ``scalpyn-worker-structural``, May/2026).

    Sequence now:
      1. Cancel any still-pending asyncio tasks; await them with
         ``return_exceptions=True``.
      2. ``await _celery_engine.dispose()`` inside the loop (best-effort —
         this is the graceful path that releases asyncpg sockets cleanly).
      3. Hard-terminate any asyncpg ``Connection`` still attached to the
         engine pool synchronously via ``Connection.terminate()`` (does NOT
         schedule on the loop, unlike ``Connection.close()``). Belt-and-
         suspenders for the rare case where ``dispose()`` couldn't clean up
         a half-open connection from a poisoned transaction.
      4. ``shutdown_asyncgens`` to drain any leftover async generators.
      5. ``loop.close()`` inside its own try/except so a teardown failure
         never propagates back into the Celery task result.

    Each cleanup step is isolated by try/except + ``logger.debug`` so the
    Celery task return value (or original exception) is preserved verbatim.

    Runbook: ``backend/docs/runbooks/2026-05-08-pipeline-recovery.md``
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        # Step 1 — drain pending asyncio tasks.
        try:
            pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
            for t in pending:
                t.cancel()
            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
        except BaseException as exc:  # pragma: no cover — defensive
            logger.debug("[_run_async] pending-task drain failed: %s", exc)

        # Step 2 — graceful engine dispose (closes asyncpg sockets in-loop).
        try:
            from ..database import _celery_engine
            loop.run_until_complete(_celery_engine.dispose())
            # Step 2b (Task #300 review) — drain microtasks scheduled
            # during dispose() (asyncpg finalizers) before hard-terminate
            # so half-released sockets don't re-arm GC callbacks on a
            # loop we're about to close.
            loop.run_until_complete(asyncio.sleep(0))
        except BaseException as exc:  # pragma: no cover — defensive
            logger.debug("[_run_async] _celery_engine.dispose failed: %s", exc)

        # Step 3 — hard-terminate any asyncpg connection still cached on
        # the pool. ``Connection.terminate()`` is synchronous and never
        # schedules on the event loop, so it's safe even when dispose()
        # left a half-open connection behind.
        try:
            from ..database import _celery_engine as _ce
            sync_pool = _ce.sync_engine.pool
            records = list(getattr(sync_pool, "_all_conns", None) or [])
            for record in records:
                raw = (
                    getattr(record, "dbapi_connection", None)
                    or getattr(record, "connection", None)
                )
                # SQLAlchemy's AsyncAdapt_asyncpg_connection wraps the real
                # asyncpg.Connection under various attribute names depending
                # on version — check the common ones.
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
        except BaseException as exc:  # pragma: no cover — defensive
            logger.debug("[_run_async] hard-terminate sweep failed: %s", exc)

        # Step 4 — drain async generators registered on the loop.
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        except BaseException as exc:  # pragma: no cover — defensive
            logger.debug("[_run_async] shutdown_asyncgens failed: %s", exc)

        # Step 5 — close the loop. Always last; never propagate.
        try:
            loop.close()
        except BaseException as exc:  # pragma: no cover — defensive
            logger.debug("[_run_async] loop.close failed: %s", exc)
        try:
            asyncio.set_event_loop(None)
        except BaseException:
            pass


async def _collect_all_async():
    from ..services.market_data_service import market_data_service
    # Task #232 — semantic cleanup: ingestion is gated by ``is_active``,
    # not by the legacy ``is_approved`` column. Use the active helper.
    from ..services.pool_service import get_active_pool_symbols, get_pool_symbols
    from ..database import run_db_task
    from sqlalchemy import text

    logger.info("Starting market data collection (active spot symbols)...")

    async def _load_spot_syms(db):
        # Buscar ativos + total da pool no mesmo session para diferenciar
        # "pool vazia" de "pool povoada mas sem ativos" no log de WARNING.
        active = await get_active_pool_symbols(db, "spot")
        total = await get_pool_symbols(db, "spot")
        return active, len(total)

    raw_symbols, pool_count = await run_db_task(_load_spot_syms, celery=True)

    logger.info(f"[COLLECT] active_symbols_count={len(raw_symbols)} pool_count={pool_count}")
    logger.info(f"[COLLECT] symbols={raw_symbols}")

    if not raw_symbols:
        # Task #231/#232: pool sem símbolos ativos é estado operacional
        # válido (aguardando promoção pela L3 do pipeline). NÃO levantar
        # — o raise faz o Celery retry em loop, satura workers, enche a
        # fila e cascateia 4 alertas críticos no Centro Operacional.
        logger.warning(
            "[COLLECT] no active symbols — skipping cycle "
            "(pool_count=%d, active_count=0)",
            pool_count,
        )
        return 0

    # Validate and deduplicate
    valid_symbols = []
    for sym in dict.fromkeys(raw_symbols):  # deduplicate while iterating
        s = sym.strip().upper() if sym else ""
        if not s:
            logger.warning(f"[SKIP INVALID SYMBOL] raw={sym!r} reason=empty")
            continue
        if "USDT" not in s:
            logger.warning(f"[SKIP INVALID SYMBOL] raw={sym!r} reason=no_usdt")
            continue
        if len(s) < 5 or len(s) > 20:
            logger.warning(f"[SKIP INVALID SYMBOL] raw={sym!r} reason=inconsistent_size len={len(s)}")
            continue
        valid_symbols.append(s)

    logger.info(f"[COLLECT] valid_symbols={len(valid_symbols)}")

    try:
        from ..services.execution_gate_metrics import record_collect_universe
        record_collect_universe(len(valid_symbols))
    except Exception as exc:
        logger.debug("[COLLECT] universe gauge failed: %s", exc)

    if not valid_symbols:
        # Task #231: idem — todos os símbolos aprovados foram filtrados pela
        # validação. Tratar como ciclo vazio em vez de erro fatal.
        logger.warning(
            "[COLLECT] no active symbols after validation — skipping cycle "
            "(pool_count=%d, active_count=%d, valid_count=0)",
            pool_count,
            len(raw_symbols),
        )
        return 0

    # Task #251: ordenação determinística por símbolo elimina deadlocks
    # entre workers concorrentes que UPSERTam em market_metadata. Postgres
    # adquire row-locks na ordem do tuple stream — se todos os workers
    # iterarem na mesma ordem, dois workers nunca pegam locks em ordens
    # opostas (precondição do deadlock determinístico).
    symbols = sorted(valid_symbols)

    async def _inner(db, queue_mode: bool = False) -> int:
        """Ticker bulk fetch + market_metadata UPSERT only.

        Task #262 — OHLCV 1h foi migrado para ``collect_structural_30m``
        (cadence 30m, crontab(0,30) UTC). Este path agora é responsável
        EXCLUSIVAMENTE por:
            * fetch_all_tickers() — 1 chamada bulk Gate.io.
            * bulk UPSERT em ``market_metadata`` (price, change, volume,
              spread) via ``_bulk_upsert_market_metadata``.

        Sem loop por símbolo, sem fetch_ohlcv, sem savepoints por símbolo.
        Retorna o número de tickers upsertados com sucesso (0 se falhar).
        """
        import time as _time
        _cycle_t0 = _time.monotonic()

        try:
            tickers = await market_data_service.fetch_all_tickers()
            if not tickers:
                logger.warning(
                    "[COLLECT-TICKERS] fetch_all_tickers returned empty — "
                    "retrying once after 3s…"
                )
                await asyncio.sleep(3)
                tickers = await market_data_service.fetch_all_tickers()

            now_ts = datetime.now(timezone.utc)
            # Task #251: validar + ordenar ANTES do bulk batch.
            # Sort por símbolo ASC é a invariante anti-deadlock (Postgres
            # acquires row-locks na ordem do tuple stream).
            valid_rows: list[dict] = []
            for ticker in tickers:
                try:
                    pair = ticker.get("currency_pair", "")
                    if not pair.endswith("_USDT"):
                        continue
                    price = float(ticker.get("last", 0) or 0)
                    if price <= 0:
                        continue
                    change = float(ticker.get("change_percentage", 0) or 0)
                    volume = float(ticker.get("quote_volume", 0) or 0)
                    spread = market_data_service.compute_spread_from_ticker(ticker)
                    valid_rows.append({
                        "symbol":  pair,
                        "price":   price,
                        "change":  change,
                        "volume":  volume,
                        "spread":  spread,
                        "updated": now_ts,
                    })
                except Exception as te:
                    logger.debug(
                        "[COLLECT-TICKERS] validation failed for %s: %s",
                        ticker.get("currency_pair", "?"), te,
                    )
                    continue

            valid_rows.sort(key=lambda r: r["symbol"])

            if queue_mode:
                # Persistence queue path: cada enqueue é sua própria mensagem;
                # iterar sorted preserva a invariante anti-deadlock downstream.
                for r in valid_rows:
                    await _pq.enqueue_or_log(
                        producer="collect-1h-tickers",
                        msg=_pq.MarketMetadataUpsert(
                            category="ingest",
                            enqueued_at=_pq.now_monotonic(),
                            symbol=r["symbol"],
                            last_updated=r["updated"],
                            price=r["price"],
                            price_change_24h=r["change"],
                            volume_24h=r["volume"],
                            spread_pct=r["spread"],
                        ),
                    )
                ticker_ok = len(valid_rows)
            else:
                ticker_ok = await _bulk_upsert_market_metadata(
                    db, valid_rows, origin="ticker-60s",
                )

            _cycle_dt = _time.monotonic() - _cycle_t0
            logger.info(
                "[COLLECT-TICKERS] upserted=%d fetched=%d duration_s=%.2f",
                ticker_ok, len(tickers), _cycle_dt,
            )
            return ticker_ok

        except Exception as exc:
            logger.error(
                "[COLLECT-TICKERS] failed: %s", exc, exc_info=True,
            )
            return 0


    # Task #236: when the persistence queue is enabled, do not open a
    # writer transaction (run_db_task wraps the callback in
    # ``async with session.begin()``). The queue path only needs a
    # read-only session for the freshness probe; all writes go through
    # ``_pq.enqueue_or_log`` and are drained by the worker pool.
    if _pq.is_enabled():
        from ..database import CeleryAsyncSessionLocal
        async with CeleryAsyncSessionLocal() as db:
            return await _inner(db, queue_mode=True)
    return await run_db_task(_inner, celery=True)


def _record_collect_all_marker(key: str, value: str) -> None:
    """Write a tiny diagnostics marker to Redis. Never raise."""
    try:
        import redis as _redis
        from ..config import settings
        r = _redis.from_url(
            settings.REDIS_URL,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        r.set(key, value)
    except Exception as exc:
        logger.debug("[collect_all] failed to write marker %s: %s", key, exc)


def _incr_collect_all_counter(key: str) -> None:
    try:
        import redis as _redis
        from ..config import settings
        r = _redis.from_url(
            settings.REDIS_URL,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        r.incr(key)
    except Exception as exc:
        logger.debug("[collect_all] failed to incr counter %s: %s", key, exc)


@celery_app.task(name="app.tasks.collect_market_data.collect_all")
def collect_all():
    # Diagnostics markers (Task #186) — read by /api/system/celery-diagnostics
    # to prove that beat is enqueueing AND worker is consuming. Never block
    # the task on Redis marker write failures.
    started = datetime.now(timezone.utc).isoformat()
    _record_collect_all_marker("scalpyn:last_collect_all_start", started)
    _incr_collect_all_counter("scalpyn:collect_all_runs")
    try:
        count = _run_async(_collect_all_async())
    except Exception as exc:
        _incr_collect_all_counter("scalpyn:collect_all_errors")
        _record_collect_all_marker(
            "scalpyn:last_collect_all_error",
            f"{datetime.now(timezone.utc).isoformat()} {type(exc).__name__}: {exc}",
        )
        raise
    finally:
        _record_collect_all_marker(
            "scalpyn:last_collect_all_end",
            datetime.now(timezone.utc).isoformat(),
        )
    # Task #262 — chain para ``compute_indicators.compute`` (1h) REMOVIDO.
    # O pipeline estrutural agora é disparado por
    # ``collect_structural_30m`` → ``compute_30m`` → ``score`` → ``evaluate``
    # (cadence crontab(0,30) UTC). Este path ficou ticker/metadata-only.
    return f"[COLLECT-TICKERS] Updated metadata for {count} tickers"


async def _collect_5m_async():
    """Collect 5-minute OHLCV candles for pipeline scan freshness.

    Task #232 — universe is every ``is_active = true`` pool coin (spot +
    futures). The execution gate (``is_tradable``) is irrelevant here:
    indicators must keep flowing for every monitored symbol so the L2/L3
    funnel and the operator dashboards see fresh data even before a
    symbol is authorised to trade.
    """
    from ..services.pool_service import (
        get_active_pool_symbols_with_market_type,
        get_pool_symbols_with_market_type,
    )
    from ..utils.symbol_filters import filter_real_assets
    from ..database import run_db_task
    from sqlalchemy import text

    logger.info("Starting 5m market data collection (active symbols only)...")

    async def _load_active(db):
        active = await get_active_pool_symbols_with_market_type(db)
        total = await get_pool_symbols_with_market_type(db)
        return active, len(total)

    symbol_market_type: dict[str, str]
    pool_count: int
    symbol_market_type, pool_count = await run_db_task(_load_active, celery=True)

    raw_syms = filter_real_assets(list(symbol_market_type.keys()))

    logger.info(f"[COLLECT] active_symbols_count={len(raw_syms)} pool_count={pool_count}")
    logger.info(f"[COLLECT] symbols={raw_syms}")

    if not raw_syms:
        # Task #231 / #232: pool sem símbolos ativos é estado válido —
        # log loud-but-no-retry, devolve 0 e o ciclo se encerra.
        logger.warning(
            "[COLLECT] no active symbols — skipping 5m cycle "
            "(pool_count=%d, active_count=0)",
            pool_count,
        )
        return 0

    # Validate and deduplicate
    valid_symbols = []
    for sym in dict.fromkeys(raw_syms):
        s = sym.strip().upper() if sym else ""
        if not s:
            logger.warning(f"[SKIP INVALID SYMBOL] raw={sym!r} reason=empty")
            continue
        if "USDT" not in s:
            logger.warning(f"[SKIP INVALID SYMBOL] raw={sym!r} reason=no_usdt")
            continue
        if len(s) < 5 or len(s) > 20:
            logger.warning(f"[SKIP INVALID SYMBOL] raw={sym!r} reason=inconsistent_size len={len(s)}")
            continue
        valid_symbols.append(s)

    logger.info(f"[COLLECT] valid_symbols={len(valid_symbols)}")

    try:
        from ..services.execution_gate_metrics import record_collect_universe
        record_collect_universe(len(valid_symbols))
    except Exception as exc:
        logger.debug("[COLLECT] universe gauge failed: %s", exc)

    if not valid_symbols:
        # Task #231: idem — ciclo 5m vazio em vez de erro fatal.
        logger.warning(
            "[COLLECT] no active symbols after validation — skipping 5m cycle "
            "(pool_count=%d, active_count=%d, valid_count=0)",
            pool_count,
            len(raw_syms),
        )
        return 0

    # Task #251: ordenação determinística por símbolo (ver gotcha em
    # ``collect_all`` acima — mesmo motivo aplica ao path 5m).
    symbols = sorted(valid_symbols)

    async def _inner(db, queue_mode: bool = False) -> int:
        from ..services.market_data_service import market_data_service
        import sqlalchemy.exc as _sqla_exc
        # P0.0: SoftTimeLimitExceeded is a BaseException — the per-symbol
        # ``except Exception`` below never catches it, so it used to bubble
        # all the way out and prevent compute_5m from being dispatched.
        # Importing here (not at module level) avoids a hard billiard dep in
        # non-Celery contexts (tests, scripts).
        try:
            from billiard.exceptions import SoftTimeLimitExceeded as _STLE
        except ImportError:  # pragma: no cover
            from celery.exceptions import SoftTimeLimitExceeded as _STLE  # type: ignore[assignment]
        collected = 0
        failures = 0
        _soft_limit_hit = False

        # Health guard: queue-mode sessions have no outer ``session.begin()``.
        if queue_mode and db.in_transaction():
            await db.rollback()
            logger.warning("[CollectMarketData] Stale transaction on queue-mode 5m session, rolled back")

        for symbol in symbols:  # no cap — active symbols only
            sym_market_type = symbol_market_type.get(symbol, "spot")
            try:
                logger.info(f"[COLLECT][START] symbol={symbol} timeframe=5m")
                df = await market_data_service.fetch_ohlcv(symbol, "5m", limit=288)
                logger.info(f"[COLLECT][RESULT] symbol={symbol} result={type(df).__name__} rows={len(df) if df is not None else 'None'}")

                if df is None:
                    logger.error(f"[COLLECT][EMPTY] symbol={symbol} timeframe=5m reason=fetch_returned_none")
                    failures += 1
                    continue

                if df.empty:
                    logger.error(f"[COLLECT][EMPTY] symbol={symbol} timeframe=5m reason=df_empty")
                    failures += 1
                    continue

                logger.info(f"[PIPELINE] DF_ROWS symbol={symbol} rows={len(df)}")

                missing = [c for c in _REQUIRED_OHLCV_COLUMNS if c not in df.columns]
                if missing:
                    logger.error(f"[PIPELINE] INVALID_COLUMNS symbol={symbol} missing={missing} columns={list(df.columns)}")
                    continue

                ohlcv_exchange = df.attrs.get("exchange", "gate.io")
                logger.info(f"[PIPELINE] INSERT_START symbol={symbol} rows={len(df)} timeframe=5m")

                latest_5m = df.iloc[-1]
                _now_5m = datetime.now(timezone.utc)
                if queue_mode:
                    # Task #236: one OhlcvBatch + one MarketMetadataUpsert per
                    # symbol — no SAVEPOINTs, no per-row round-trips.
                    rows_payload = tuple(
                        {
                            "time":         row["time"],
                            "open":         float(row["open"]),
                            "high":         float(row["high"]),
                            "low":          float(row["low"]),
                            "close":        float(row["close"]),
                            "volume":       float(row["volume"]),
                            "quote_volume": float(row.get(
                                "quote_volume",
                                float(row["close"]) * float(row["volume"]),
                            )),
                        }
                        for _, row in df.iterrows()
                    )
                    await _pq.enqueue_or_log(
                        producer="collect-5m",
                        msg=_pq.OhlcvBatch(
                            category="ingest",
                            enqueued_at=_pq.now_monotonic(),
                            symbol=symbol,
                            exchange=ohlcv_exchange,
                            timeframe="5m",
                            market_type=sym_market_type,
                            rows=rows_payload,
                        ),
                    )
                    await _pq.enqueue_or_log(
                        producer="collect-5m",
                        msg=_pq.MarketMetadataUpsert(
                            category="scheduler",
                            enqueued_at=_pq.now_monotonic(),
                            symbol=symbol,
                            last_updated=_now_5m,
                            price=float(latest_5m["close"]),
                        ),
                    )
                else:
                    # SAVEPOINT 1: OHLCV candles + price seed.
                    # Isolated so a single symbol failure never aborts the whole
                    # collection transaction.
                    try:
                        async with db.begin_nested():
                            # Bulk-insert all returned candles (ON CONFLICT DO NOTHING is idempotent)
                            for _, row in df.iterrows():
                                await db.execute(text("""
                                    INSERT INTO ohlcv (time, symbol, exchange, timeframe, market_type, open, high, low, close, volume, quote_volume)
                                    VALUES (:time, :symbol, :exchange, :timeframe, :market_type, :open, :high, :low, :close, :volume, :quote_volume)
                                    ON CONFLICT DO NOTHING
                                """), {
                                    "time":        row["time"],
                                    "symbol":      symbol,
                                    "exchange":    ohlcv_exchange,
                                    "timeframe":   "5m",
                                    "market_type": sym_market_type,
                                    "open":        float(row["open"]),
                                    "high":        float(row["high"]),
                                    "low":         float(row["low"]),
                                    "close":       float(row["close"]),
                                    "volume":      float(row["volume"]),
                                    "quote_volume": float(row.get("quote_volume", float(row["close"]) * float(row["volume"]))),
                                })

                            # 2026-05-08 — REMOVED redundant market_metadata UPSERT.
                            # Three sources already keep market_metadata.price fresh
                            # (collect_all every 1h, Gate.io WS tickers in real time,
                            # the orderbook SAVEPOINT below for spread/depth). Doing
                            # it again HERE created cross-task row-lock contention
                            # between worker-micro (collect_5m) and worker-structural
                            # (collect_all) on the same 95 hot rows, which surfaced
                            # as ``canceling statement due to user request`` once
                            # _MICRO_GUARDS was bumped to 480 s and tasks actually
                            # held the outer transaction long enough to collide.
                            # Keep this comment as the authoritative justification —
                            # do NOT re-add the UPSERT without a new contention
                            # mitigation strategy (e.g. SKIP LOCKED, separate session).
                    except Exception as _sp_ohlcv5m_exc:
                        # SAVEPOINT auto-rolled back by begin_nested.
                        # See "Nested-savepoint rollback rule" gotcha — do NOT
                        # call db.rollback() here (would close outer tx).
                        logger.error(
                            "[CollectMarketData] SAVEPOINT (OHLCV 5m) failed for %s — savepoint rolled back: %s",
                            symbol, _sp_ohlcv5m_exc,
                        )
                        raise

                # SAVEPOINT 2: orderbook metrics (separate SAVEPOINT so that a
                # DB failure here never rolls back the OHLCV + price writes above).
                # fetch_orderbook_metrics internally retries (Gate → Binance fallback)
                # via resilient_data_service; missing depth lands as NULL in DB, which
                # the pipeline treats as UNKNOWN (not FAIL) since orderbook_depth_usdt
                # is no longer in STRICT_META.
                try:
                    ob = await market_data_service.fetch_orderbook_metrics(symbol, depth=10)
                    if ob:
                        _ob_ts = datetime.now(timezone.utc)
                        if queue_mode:
                            await _pq.enqueue_or_log(
                                producer="collect-5m-orderbook",
                                msg=_pq.MarketMetadataUpsert(
                                    category="scheduler",
                                    enqueued_at=_pq.now_monotonic(),
                                    symbol=symbol,
                                    last_updated=_ob_ts,
                                    spread_pct=ob.get("spread_pct"),
                                    orderbook_depth_usdt=ob.get("orderbook_depth_usdt"),
                                ),
                            )
                        else:
                            try:
                                async with db.begin_nested():
                                    await db.execute(text("""
                                        INSERT INTO market_metadata (symbol, spread_pct, orderbook_depth_usdt, last_updated)
                                        VALUES (:sym, :spread, :depth, :ts)
                                        ON CONFLICT (symbol) DO UPDATE SET
                                            spread_pct = :spread,
                                            orderbook_depth_usdt = :depth,
                                            last_updated = :ts
                                    """), {
                                        "sym":    symbol,
                                        "spread": ob.get("spread_pct"),
                                        "depth":  ob.get("orderbook_depth_usdt"),
                                        "ts":     _ob_ts,
                                    })
                            except Exception as _sp_ob_exc:
                                # SAVEPOINT auto-rolled back by begin_nested.
                                # See "Nested-savepoint rollback rule" gotcha — do NOT
                                # call db.rollback() here (would close outer tx).
                                logger.error(
                                    "[CollectMarketData] SAVEPOINT (orderbook 5m) failed for %s — savepoint rolled back: %s",
                                    symbol, _sp_ob_exc,
                                )
                                raise
                    else:
                        logger.warning(
                            "[DATA_UNKNOWN] orderbook metrics unavailable for %s "
                            "(Gate + Binance both failed — depth will be NULL this cycle)",
                            symbol,
                        )
                except Exception as exc:
                    logger.warning(
                        "[DATA_FAIL] orderbook upsert failed for %s: %s",
                        symbol, exc,
                    )

                collected += 1
                logger.info(f"[PERSIST] success symbol={symbol}")
                logger.info(f"[COLLECT][OK] symbol={symbol} timeframe=5m")
            except _STLE:
                # P0.0: soft_time_limit fired mid-symbol-loop. Any in-progress
                # SAVEPOINT was rolled back by begin_nested.__aexit__. The outer
                # transaction is still active. Break gracefully so the caller
                # returns ``collected`` (> 0) and compute_5m is dispatched.
                _soft_limit_hit = True
                logger.warning(
                    "[COLLECT-5m] soft_time_limit reached at symbol=%s — "
                    "collected=%d/%d; compute chain will still fire",
                    symbol, collected, len(symbols),
                )
                break
            except Exception as e:
                # PendingRollbackError: asyncpg connection is in a failed-tx
                # state. Must rollback explicitly before any further operation.
                if isinstance(e, _sqla_exc.PendingRollbackError):
                    await db.rollback()
                    logger.error(
                        "[CollectMarketData] Session in invalid state after %s (5m) — rolled back, stopping. Error: %s",
                        symbol, e,
                    )
                    failures += 1
                    break
                # 2026-05-09 — see collect_all loop for rationale.
                _err_str = str(e)
                if "InFailedSQLTransaction" in _err_str or "current transaction is aborted" in _err_str:
                    try:
                        await db.rollback()
                    except Exception as _rb_exc:
                        logger.warning("[COLLECT-5m] rollback after aborted tx failed: %s", _rb_exc)
                    remaining = len(symbols) - symbols.index(symbol) - 1
                    logger.error(
                        "[COLLECT-5m] outer tx aborted server-side after %s — "
                        "rolled back, skipping remaining %d symbols; collected=%d",
                        symbol, remaining, collected,
                    )
                    failures += 1
                    break
                logger.error(
                    f"[FAILED symbol={symbol}] timeframe=5m error={str(e)}",
                    exc_info=True,
                )
                failures += 1
                # NOTE: do NOT call ``await db.rollback()`` here. The
                # ``async with db.begin_nested()`` above already rolls back
                # the SAVEPOINT on exception. Calling ``db.rollback()`` on
                # top of that closes the OUTER transaction opened by
                # ``run_db_task`` (``async with session.begin()``) and
                # poisons every subsequent symbol with
                # "Can't operate on closed transaction inside context manager".
                if not db.is_active:
                    remaining = len(symbols) - symbols.index(symbol) - 1
                    logger.error(
                        "[COLLECT] outer transaction poisoned after %s (5m) — "
                        "skipping remaining %d symbols; collected=%d",
                        symbol, remaining, collected,
                    )
                    break
                continue

        # P0.0: if soft_time_limit fired, skip the backup ticker / stale-fallback
        # paths (we are already near the hard time_limit=480s). Return the partial
        # count so collect_5m() dispatches compute_5m and compute_structural_5m.
        if _soft_limit_hit:
            logger.info(
                "[OHLCV-COMMIT] cycle_done flow=5m success=%d fail=%d total=%d "
                "(partial — soft_limit_hit)",
                collected, failures, len(symbols),
            )
            if collected == 0:
                raise RuntimeError(
                    "zero success — all symbols failed (soft_time_limit)"
                )
            return collected

        # ── Backup metadata pathway: fetch tickers for volume_24h + spread ───
        # Ensures pool coins get volume_24h populated even when collect_all's
        # tickers block has failed.  Without volume_24h, strict profile filters
        # reject the asset even though a metadata row exists from the OHLCV seed.
        try:
            tickers = await market_data_service.fetch_all_tickers()
            if tickers:
                now_ts = datetime.now(timezone.utc)
                # Task #251: validate + sort, then bulk UPSERT (same pattern
                # as collect_all 1h — see helper docstring for rationale).
                valid_rows: list[dict] = []
                for ticker in tickers:
                    try:
                        pair = ticker.get("currency_pair", "")
                        if not pair.endswith("_USDT"):
                            continue
                        price = float(ticker.get("last", 0) or 0)
                        if price <= 0:
                            continue
                        volume = float(ticker.get("quote_volume", 0) or 0)
                        change = float(ticker.get("change_percentage", 0) or 0)
                        spread = market_data_service.compute_spread_from_ticker(ticker)
                        valid_rows.append({
                            "symbol": pair,
                            "price": price,
                            "change": change,
                            "volume": volume,
                            "spread": spread,
                            "updated": now_ts,
                        })
                    except Exception as te:
                        logger.debug("5m backup ticker: validation failed for %s: %s",
                                     ticker.get("currency_pair", "?"), te)
                        continue

                valid_rows.sort(key=lambda r: r["symbol"])

                if queue_mode:
                    for r in valid_rows:
                        await _pq.enqueue_or_log(
                            producer="collect-5m-tickers",
                            msg=_pq.MarketMetadataUpsert(
                                category="ingest",
                                enqueued_at=_pq.now_monotonic(),
                                symbol=r["symbol"],
                                last_updated=r["updated"],
                                price=r["price"],
                                price_change_24h=r["change"],
                                volume_24h=r["volume"],
                                spread_pct=r["spread"],
                            ),
                        )
                    ticker_ok = len(valid_rows)
                else:
                    ticker_ok = await _bulk_upsert_market_metadata(
                        db, valid_rows, origin="ticker-5m-backup",
                    )
                logger.info("5m: backup ticker metadata upserted for %d symbols", ticker_ok)
        except Exception as e:
            logger.debug("5m: backup ticker fetch failed (non-blocking): %s", e)

        # Per-symbol Gate ticker fallback for long-tail pairs missing from the
        # batch endpoint. Staleness is checked against `volume_24h_updated_at`
        # (written only by ticker writers) — NOT `last_updated`, which is
        # touched by price/orderbook seeds in this same task and would
        # otherwise mask stale volume figures.
        try:
            stale_rows = (await db.execute(text("""
                SELECT symbol
                FROM market_metadata
                WHERE symbol = ANY(:syms)
                  AND (
                        volume_24h IS NULL
                     OR volume_24h_updated_at IS NULL
                     OR volume_24h_updated_at < now() - interval '10 minutes'
                  )
            """), {"syms": symbols})).fetchall()
            stale_syms = [r.symbol for r in stale_rows]
            if stale_syms:
                logger.info("5m: per-symbol Gate ticker fallback for %d stale symbols", len(stale_syms))
                refreshed = 0
                # Task #251: ordenação determinística — UPSERT em market_metadata
                # na mesma ordem em todos os workers concorrentes.
                for sym in sorted(stale_syms):
                    try:
                        ticker = await market_data_service._fetch_gate_ticker(sym)
                        if not ticker:
                            continue
                        price = float(ticker.get("last", 0) or 0)
                        volume = float(ticker.get("quote_volume", 0) or 0)
                        if price <= 0 or volume <= 0:
                            continue
                        _ts_fb = datetime.now(timezone.utc)
                        if queue_mode:
                            await _pq.enqueue_or_log(
                                producer="collect-5m-fallback",
                                msg=_pq.MarketMetadataUpsert(
                                    category="ingest",
                                    enqueued_at=_pq.now_monotonic(),
                                    symbol=sym,
                                    last_updated=_ts_fb,
                                    price=price,
                                    volume_24h=volume,
                                ),
                            )
                        else:
                            try:
                                async with db.begin_nested():
                                    await db.execute(text("""
                                        UPDATE market_metadata
                                        SET price = :price,
                                            volume_24h = :volume,
                                            last_updated = :ts,
                                            volume_24h_updated_at = :ts
                                        WHERE symbol = :sym
                                    """), {
                                        "sym":    sym,
                                        "price":  price,
                                        "volume": volume,
                                        "ts":     _ts_fb,
                                    })
                            except Exception as _fbsp_exc:
                                # SAVEPOINT auto-rolled back by begin_nested.
                                # See "Nested-savepoint rollback rule" gotcha — do NOT
                                # call db.rollback() here (would close outer tx).
                                logger.error(
                                    "[CollectMarketData] SAVEPOINT (fallback 5m) failed for %s — savepoint rolled back: %s",
                                    sym, _fbsp_exc,
                                )
                                raise
                        refreshed += 1
                    except Exception as se:
                        if not db.is_active:
                            logger.error(
                                "[CollectMarketData] 5m fallback loop: session no longer active — stopping"
                            )
                            break
                        logger.debug("5m: per-symbol fallback failed for %s: %s", sym, se)
                        continue
                logger.info("5m: per-symbol Gate ticker fallback refreshed %d symbols", refreshed)
        except Exception as e:
            logger.debug("5m: per-symbol stale-check skipped (non-blocking): %s", e)
        # run_db_task auto-commits all successful writes on exit

        logger.info(f"[COLLECT] success={collected} fail={failures} total={len(symbols)}")
        # Task #234 — symmetric COMMIT marker on the 5m path.
        logger.info(
            "[OHLCV-COMMIT] cycle_done flow=5m success=%d fail=%d total=%d",
            collected, failures, len(symbols),
        )
        if collected == 0:
            raise RuntimeError("zero success — all symbols failed")
        return collected

    # Task #236: persistence-queue path (see _collect_all_async for rationale).
    if _pq.is_enabled():
        from ..database import CeleryAsyncSessionLocal
        async with CeleryAsyncSessionLocal() as db:
            return await _inner(db, queue_mode=True)
    return await run_db_task(_inner, celery=True)


@celery_app.task(name="app.tasks.collect_market_data.collect_5m")
def collect_5m():
    count = _run_async(_collect_5m_async())
    # Chain: fresh 5m candles → compute 5m indicators (microstructure queue).
    # TTL = compute_5m time_limit (180s) + 30s margin.
    # Task #231: pular o chain quando não há candles novos a computar.
    if count > 0:
        from . import task_dispatch
        task_dispatch.enqueue(
            "app.tasks.compute_indicators.compute_5m",
            dedup_key="compute_5m",
            ttl_seconds=210,
        )
        # Structural-on-5m: paralelo ao chain micro acima. Roda no
        # worker-structural (não competir com micro) e fecha o gap entre a
        # cadência 30m do compute_30m e o leitor estrutural canônico
        # (indicators_provider.get_merged_indicators). Dedup-key próprio
        # para nunca colidir com o slot do compute_5m micro.
        #
        # P0.1 — TTL bumped 210 → 1800s:
        # QUEUE_STRUCTURAL tem wait ≈ 25 min. Com TTL=210s o lock expirava
        # antes de a task começar → cada collect_5m (5 min) enfileirava outra
        # instância → pile-up progressivo → fila travada → structural-5m parou
        # de produzir indicadores. TTL=1800s (30 min) garante que só uma
        # instância vive na fila por vez; a próxima dispatch só ocorre após o
        # task_postrun liberar o lock OU o TTL expirar (safety cap).
        task_dispatch.enqueue(
            "app.tasks.compute_indicators.compute_structural_5m",
            dedup_key="compute_structural_5m",
            ttl_seconds=1800,
        )
    return f"Collected 5m data for {count} symbols"
