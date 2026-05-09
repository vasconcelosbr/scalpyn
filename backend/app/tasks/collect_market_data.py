"""Celery Task — collect market data from exchanges into TimescaleDB."""

import asyncio
import logging
from datetime import datetime, timezone

from ..services import persistence as _pq
from ..tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

_REQUIRED_OHLCV_COLUMNS = ["time", "open", "high", "low", "close", "volume"]


def _run_async(coro):
    """Run async code in sync Celery task."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


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
        import time as _time
        import sqlalchemy.exc as _sqla_exc
        _cycle_t0 = _time.monotonic()
        collected = 0
        failures = 0

        # Health guard: in queue-mode the session has no outer ``session.begin()``
        # so any stale transaction must be cleared before touching the DB.
        # In run_db_task mode (non-queue) the outer begin() is fresh — skip.
        if queue_mode and db.in_transaction():
            await db.rollback()
            logger.warning("[CollectMarketData] Stale transaction on queue-mode session, rolled back")

        # Task #234 — OHLCV ingestion instrumentation. Five structured
        # log lines per symbol/cycle ([OHLCV-RX|PERSIST|LATEST|STALE|COMMIT])
        # plus three Prometheus metrics. Operators correlate against the
        # `ingestion_stale` alert via `scalpyn_ohlcv_latest_age_seconds`.
        try:
            from ..services import ohlcv_metrics as _ohlcv_metrics
        except Exception:  # pragma: no cover — defensive
            _ohlcv_metrics = None  # type: ignore[assignment]

        for symbol in symbols:  # process only active symbols
            try:
                logger.info(f"[COLLECT][START] symbol={symbol}")
                df = await market_data_service.fetch_ohlcv(symbol, "1h", limit=100)
                rx_rows = len(df) if df is not None and not getattr(df, "empty", True) else 0
                logger.info(
                    "[OHLCV-RX] symbol=%s timeframe=1h rows=%d type=%s",
                    symbol, rx_rows, type(df).__name__,
                )
                if _ohlcv_metrics is not None and rx_rows:
                    _ohlcv_metrics.record_received(symbol, "1h", rx_rows)

                if df is None:
                    logger.error(f"[COLLECT][EMPTY] symbol={symbol} reason=fetch_returned_none")
                    failures += 1
                    continue

                if df.empty:
                    logger.error(f"[COLLECT][EMPTY] symbol={symbol} reason=df_empty")
                    failures += 1
                    continue

                logger.info(f"[PIPELINE] DF_ROWS symbol={symbol} rows={len(df)}")
                logger.debug(f"[PIPELINE] DF_HEAD symbol={symbol} data={df.head(2).to_dict()}")

                missing = [c for c in _REQUIRED_OHLCV_COLUMNS if c not in df.columns]
                if missing:
                    logger.error(f"[PIPELINE] INVALID_COLUMNS symbol={symbol} missing={missing} columns={list(df.columns)}")
                    continue

                ohlcv_exchange = df.attrs.get("exchange", "gate.io")
                logger.info(f"[PIPELINE] INSERT_START symbol={symbol} rows={len(df)}")
                logger.info(
                    "[OHLCV-PERSIST] symbol=%s rows=%d exchange=%s",
                    symbol, len(df), ohlcv_exchange,
                )
                # Task #236: persistence-queue path. One OhlcvBatch per
                # symbol replaces N per-row INSERTs and removes the
                # outer write-tx hold time that contended with the
                # microstructure scheduler in May-2026 prod.
                if queue_mode:
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
                        producer="collect-1h",
                        msg=_pq.OhlcvBatch(
                            category="ingest",
                            enqueued_at=_pq.now_monotonic(),
                            symbol=symbol,
                            exchange=ohlcv_exchange,
                            timeframe="1h",
                            market_type="spot",
                            rows=rows_payload,
                        ),
                    )
                else:
                    # Each symbol's writes are isolated in a SAVEPOINT so that a
                    # single failure never aborts the whole collection transaction.
                    try:
                        async with db.begin_nested():
                            for _, row in df.iterrows():
                                await db.execute(text("""
                                    INSERT INTO ohlcv (time, symbol, exchange, timeframe, market_type, open, high, low, close, volume, quote_volume)
                                    VALUES (:time, :symbol, :exchange, :timeframe, :market_type, :open, :high, :low, :close, :volume, :quote_volume)
                                    ON CONFLICT DO NOTHING
                                """), {
                                    "time": row["time"],
                                    "symbol": symbol,
                                    "exchange": ohlcv_exchange,
                                    "timeframe": "1h",
                                    "market_type": "spot",
                                    "open": float(row["open"]),
                                    "high": float(row["high"]),
                                    "low": float(row["low"]),
                                    "close": float(row["close"]),
                                    "volume": float(row["volume"]),
                                    "quote_volume": float(row.get("quote_volume", float(row["close"]) * float(row["volume"]))),
                                })
                    except Exception as _sp_ohlcv_exc:
                        # SAVEPOINT auto-rolled back by ``async with db.begin_nested()`` —
                        # do NOT call ``db.rollback()`` here. Per the documented
                        # "Nested-savepoint rollback rule" gotcha (Task #222), rolling
                        # back the OUTER transaction proactively on EVERY savepoint
                        # failure (not just true connection death) closes the outer
                        # tx, poisons the next iteration with PendingRollbackError,
                        # and triggers the recovery break — losing the rest of the
                        # collection cycle for benign savepoint errors.
                        logger.error(
                            "[CollectMarketData] SAVEPOINT (OHLCV 1h) failed for %s — savepoint rolled back: %s",
                            symbol, _sp_ohlcv_exc,
                        )
                        raise
                if _ohlcv_metrics is not None:
                    _ohlcv_metrics.record_persisted(symbol, "1h", len(df))

                # Post-persist freshness probe — single round-trip, used by
                # the `ingestion_stale` alert.
                #
                # 2026-05-09 — wrapped in its own SAVEPOINT. The previous
                # implementation only had a Python try/except, but a Postgres
                # query error (LockNotAvailableError, deadlock, statement
                # cancel, etc.) aborts the OUTER transaction server-side. The
                # try/except silenced the Python exception but the next
                # ``begin_nested()`` for the next symbol would then raise
                # ``InFailedSQLTransactionError`` because asyncpg cannot run
                # ``SAVEPOINT sa_X`` on an aborted outer tx — cascading the
                # whole remaining cycle. SAVEPOINT here makes any probe
                # failure self-contained.
                try:
                    async with db.begin_nested():
                        latest_row = (await db.execute(text("""
                            SELECT EXTRACT(EPOCH FROM (NOW() - MAX(time))) AS age_seconds,
                                   MAX(time) AS latest_time
                            FROM ohlcv
                            WHERE symbol = :symbol
                              AND timeframe = '1h'
                              AND exchange = :exchange
                        """), {"symbol": symbol, "exchange": ohlcv_exchange})).first()
                    age = float(latest_row.age_seconds) if latest_row and latest_row.age_seconds is not None else None
                    latest_t = latest_row.latest_time if latest_row else None
                    logger.info(
                        "[OHLCV-LATEST] symbol=%s latest=%s age_s=%s",
                        symbol, latest_t, f"{age:.0f}" if age is not None else "None",
                    )
                    if age is not None:
                        if _ohlcv_metrics is not None:
                            _ohlcv_metrics.record_latest_age(symbol, "1h", age)
                        # 1h candles: anything > 30 min stale is operator-actionable.
                        if age > 1800:
                            logger.warning(
                                "[OHLCV-STALE] symbol=%s age_s=%.0f threshold_s=1800 "
                                "— check exchange feed / collector cadence",
                                symbol, age,
                            )
                except Exception as probe_exc:
                    logger.debug(
                        "[OHLCV-LATEST] symbol=%s probe failed (non-blocking): %s",
                        symbol, probe_exc,
                    )

                # ── per-symbol market_metadata UPSERT (NOT inside the
                # probe except — Task #234 review fix). Runs on every
                # successful OHLCV ingest, regardless of the freshness
                # probe outcome.
                #
                # 2026-05-09 — also wrapped in its own SAVEPOINT for the
                # same reason as the freshness probe above. ``market_metadata``
                # is the hottest contended row in the schema (WS Gate.io
                # ticker + collect_5m + collect_all + tickers loop all UPSERT
                # the same row). Any transient lock/timeout error here was
                # poisoning the whole outer cycle.
                latest = df.iloc[-1]
                _now_mm = datetime.now(timezone.utc)
                if queue_mode:
                    await _pq.enqueue_or_log(
                        producer="collect-1h",
                        msg=_pq.MarketMetadataUpsert(
                            category="scheduler",
                            enqueued_at=_pq.now_monotonic(),
                            symbol=symbol,
                            last_updated=_now_mm,
                            price=float(latest["close"]),
                            # NOTE: do NOT seed price_change_24h=0 here — the
                            # tickers loop below carries the real value, and a
                            # zero seed would clobber it under sparse-COALESCE
                            # if its message arrives first. Legacy direct-write
                            # path keeps the zero seed only because the same
                            # transaction immediately overwrites it.
                        ),
                    )
                else:
                    try:
                        async with db.begin_nested():
                            await db.execute(text("""
                                INSERT INTO market_metadata (symbol, price, price_change_24h, last_updated)
                                VALUES (:symbol, :price, 0, :updated)
                                ON CONFLICT (symbol) DO UPDATE SET
                                    price = :price, last_updated = :updated
                            """), {
                                "symbol": symbol,
                                "price": float(latest["close"]),
                                "updated": _now_mm,
                            })
                    except Exception as _sp_mm_exc:
                        # SAVEPOINT auto-rolled back. Log + continue: the
                        # OHLCV write above already succeeded, and the
                        # tickers loop further down also UPSERTs price.
                        logger.warning(
                            "[CollectMarketData] market_metadata UPSERT failed for %s "
                            "— savepoint rolled back, OHLCV preserved: %s",
                            symbol, _sp_mm_exc,
                        )

                logger.info(f"[PERSIST] success symbol={symbol}")
                logger.info(f"[COLLECT][OK] symbol={symbol}")
                collected += 1
            except Exception as e:
                # PendingRollbackError means the asyncpg connection is in a
                # failed-transaction state. The savepoint rollback is not
                # enough — the outer transaction is poisoned and must be
                # explicitly rolled back before any further DB operation.
                if isinstance(e, _sqla_exc.PendingRollbackError):
                    await db.rollback()
                    logger.error(
                        "[CollectMarketData] Session in invalid state after %s — rolled back, stopping collection. Error: %s",
                        symbol, e,
                    )
                    failures += 1
                    break
                # 2026-05-09 — InFailedSQLTransactionError means the outer
                # tx is aborted server-side (asyncpg refuses any further
                # query, including SAVEPOINT) but ``db.is_active`` may
                # still be True client-side. Force rollback + break so the
                # next symbol does not cascade the same error 95 times.
                _err_str = str(e)
                if "InFailedSQLTransaction" in _err_str or "current transaction is aborted" in _err_str:
                    try:
                        await db.rollback()
                    except Exception as _rb_exc:
                        logger.warning("[COLLECT] rollback after aborted tx failed: %s", _rb_exc)
                    remaining = len(symbols) - symbols.index(symbol) - 1
                    logger.error(
                        "[COLLECT] outer tx aborted server-side after %s — "
                        "rolled back, skipping remaining %d symbols; collected=%d",
                        symbol, remaining, collected,
                    )
                    failures += 1
                    break
                logger.error(
                    f"[FAILED symbol={symbol}] error={str(e)}",
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
                #
                # However, some transaction-aborting errors (e.g. deadlock)
                # cause PostgreSQL to abort the *outer* transaction as well,
                # leaving the asyncpg connection in a failed state that the
                # savepoint rollback cannot recover from. Detect this so we
                # stop early instead of cascading InFailedSQLTransaction
                # errors through every remaining symbol.
                if not db.is_active:
                    remaining = len(symbols) - symbols.index(symbol) - 1
                    logger.error(
                        "[COLLECT] outer transaction poisoned after %s — "
                        "skipping remaining %d symbols; collected=%d",
                        symbol, remaining, collected,
                    )
                    break
                continue

        # Also fetch tickers for metadata (price, volume, change + spread_pct from bid/ask)
        # Process ALL tickers (not capped) so every pool coin gets market data,
        # even niche coins beyond the top-500.
        try:
            tickers = await market_data_service.fetch_all_tickers()
            if not tickers:
                logger.warning("fetch_all_tickers returned empty — retrying once after 3 s…")
                await asyncio.sleep(3)
                tickers = await market_data_service.fetch_all_tickers()

            now_ts = datetime.now(timezone.utc)
            ticker_ok = 0
            # Task #251: ordenar tickers por currency_pair garante que todos os
            # workers UPSERTem em market_metadata na mesma ordem → elimina
            # deadlock determinístico por aquisição cruzada de row-locks.
            for ticker in sorted(tickers, key=lambda t: t.get("currency_pair", "")):
                try:
                    pair = ticker.get("currency_pair", "")
                    if not pair.endswith("_USDT"):
                        continue
                    sym = pair  # keep BTC_USDT format (with underscore)
                    price = float(ticker.get("last", 0) or 0)
                    change = float(ticker.get("change_percentage", 0) or 0)
                    volume = float(ticker.get("quote_volume", 0) or 0)
                    # Compute spread from tickers bid/ask (no extra API call needed)
                    spread = market_data_service.compute_spread_from_ticker(ticker)

                    if price > 0:
                        if queue_mode:
                            await _pq.enqueue_or_log(
                                producer="collect-1h-tickers",
                                msg=_pq.MarketMetadataUpsert(
                                    category="ingest",
                                    enqueued_at=_pq.now_monotonic(),
                                    symbol=sym,
                                    last_updated=now_ts,
                                    price=price,
                                    price_change_24h=change,
                                    volume_24h=volume,
                                    spread_pct=spread,
                                ),
                            )
                        else:
                            try:
                                async with db.begin_nested():
                                    await db.execute(text("""
                                        INSERT INTO market_metadata
                                            (symbol, price, price_change_24h, volume_24h,
                                             spread_pct, last_updated, volume_24h_updated_at)
                                        VALUES (:symbol, :price, :change, :volume, :spread,
                                                :updated, :updated)
                                        ON CONFLICT (symbol) DO UPDATE SET
                                            price = :price, price_change_24h = :change,
                                            volume_24h = :volume, spread_pct = :spread,
                                            last_updated = :updated,
                                            volume_24h_updated_at = :updated
                                    """), {
                                        "symbol": sym,
                                        "price":  price,
                                        "change": change,
                                        "volume": volume,
                                        "spread": spread,
                                        "updated": now_ts,
                                    })
                            except Exception as _tsp_exc:
                                # SAVEPOINT auto-rolled back by begin_nested.
                                # See "Nested-savepoint rollback rule" gotcha — do NOT
                                # call db.rollback() here (would close outer tx).
                                logger.error(
                                    "[CollectMarketData] SAVEPOINT (ticker 1h) failed for %s — savepoint rolled back: %s",
                                    ticker.get("currency_pair", "?"), _tsp_exc,
                                )
                                raise
                        ticker_ok += 1
                except Exception as te:
                    if not db.is_active:
                        logger.error(
                            "[CollectMarketData] Ticker 1h loop: session no longer active — stopping ticker updates"
                        )
                        break
                    logger.debug("Ticker metadata upsert failed for %s: %s",
                                 ticker.get("currency_pair", "?"), te)
                    continue

            if ticker_ok:
                logger.info("market_metadata: upserted %d/%d tickers", ticker_ok, len(tickers))
            else:
                logger.error(
                    "market_metadata: 0 tickers upserted (fetched %d) — "
                    "collect_5m backup pathway will provide fallback metadata.",
                    len(tickers),
                )
        except Exception as e:
            logger.error("Failed to fetch/update metadata from tickers: %s", e)
        # run_db_task auto-commits all successful writes on exit

        logger.info(f"[COLLECT] success={collected} fail={failures} total={len(symbols)}")
        # Task #234 — symmetric COMMIT marker on the 1h path. Logged with
        # outcome (ok|fail) and cycle duration so operators can correlate
        # ingest health against the [OHLCV-RX|PERSIST|LATEST|STALE]
        # markers above.
        _cycle_dt = _time.monotonic() - _cycle_t0
        _outcome = "ok" if collected > 0 else "fail"
        logger.info(
            "[OHLCV-COMMIT] cycle_done flow=1h outcome=%s success=%d fail=%d "
            "total=%d duration_s=%.2f",
            _outcome, collected, failures, len(symbols), _cycle_dt,
        )
        if collected == 0:
            raise RuntimeError("zero success — all symbols failed")
        return collected

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
    # Chain to compute indicators (Task #216: dedup wrapper, structural queue).
    # TTL = compute time_limit (600s) + 60s safety margin.
    # Task #231: pular o chain quando o pool está sem aprovados — não há
    # candles novos para computar indicadores e enfileirar só geraria ruído.
    if count > 0:
        from . import task_dispatch
        task_dispatch.enqueue(
            "app.tasks.compute_indicators.compute",
            dedup_key="compute",
            ttl_seconds=660,
        )
    return f"Collected {count} symbols"


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
        collected = 0
        failures = 0

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

        # ── Backup metadata pathway: fetch tickers for volume_24h + spread ───
        # Ensures pool coins get volume_24h populated even when collect_all's
        # tickers block has failed.  Without volume_24h, strict profile filters
        # reject the asset even though a metadata row exists from the OHLCV seed.
        try:
            tickers = await market_data_service.fetch_all_tickers()
            if tickers:
                now_ts = datetime.now(timezone.utc)
                ticker_ok = 0
                # Task #251: ordenar por currency_pair (mesma motivação do
                # collect_all 1h — evita deadlock entre worker-structural e
                # worker-micro UPSERTando em market_metadata).
                for ticker in sorted(tickers, key=lambda t: t.get("currency_pair", "")):
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
                        if queue_mode:
                            await _pq.enqueue_or_log(
                                producer="collect-5m-tickers",
                                msg=_pq.MarketMetadataUpsert(
                                    category="ingest",
                                    enqueued_at=_pq.now_monotonic(),
                                    symbol=pair,
                                    last_updated=now_ts,
                                    price=price,
                                    price_change_24h=change,
                                    volume_24h=volume,
                                    spread_pct=spread,
                                ),
                            )
                        else:
                            try:
                                async with db.begin_nested():
                                    await db.execute(text("""
                                        INSERT INTO market_metadata
                                            (symbol, price, price_change_24h, volume_24h,
                                             spread_pct, last_updated, volume_24h_updated_at)
                                        VALUES (:symbol, :price, :change, :volume, :spread,
                                                :updated, :updated)
                                        ON CONFLICT (symbol) DO UPDATE SET
                                            price = :price,
                                            price_change_24h = :change,
                                            volume_24h = :volume,
                                            spread_pct = :spread,
                                            last_updated = :updated,
                                            volume_24h_updated_at = :updated
                                    """), {
                                        "symbol": pair,
                                        "price":  price,
                                        "change": change,
                                        "volume": volume,
                                        "spread": spread,
                                        "updated": now_ts,
                                    })
                            except Exception as _btsp_exc:
                                # SAVEPOINT auto-rolled back by begin_nested.
                                # See "Nested-savepoint rollback rule" gotcha — do NOT
                                # call db.rollback() here (would close outer tx).
                                logger.error(
                                    "[CollectMarketData] SAVEPOINT (backup ticker 5m) failed for %s — savepoint rolled back: %s",
                                    ticker.get("currency_pair", "?"), _btsp_exc,
                                )
                                raise
                        ticker_ok += 1
                    except Exception as te:
                        if not db.is_active:
                            logger.error(
                                "[CollectMarketData] 5m backup ticker loop: session no longer active — stopping"
                            )
                            break
                        logger.debug("5m: backup ticker upsert failed for %s: %s",
                                     ticker.get("currency_pair", "?"), te)
                        continue
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
    return f"Collected 5m data for {count} symbols"
