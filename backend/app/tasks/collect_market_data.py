"""Celery Task — collect market data from exchanges into TimescaleDB."""

import asyncio
import logging
from datetime import datetime, timezone

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
    from ..services.persistence import (
        MarketMetadataWrite,
        OhlcvCandle,
        PersistenceJob,
        run_persistence_batch,
    )
    from ..services.pool_service import get_approved_pool_symbols
    from ..database import run_db_task

    logger.info("Starting market data collection (approved spot symbols)...")

    async def _load_spot_syms(db):
        return await get_approved_pool_symbols(db, "spot")

    raw_symbols = await run_db_task(_load_spot_syms, celery=True)

    logger.info(f"[COLLECT] approved_symbols_count={len(raw_symbols)}")
    logger.info(f"[COLLECT] symbols={raw_symbols}")

    if not raw_symbols:
        raise RuntimeError("[FATAL] No approved symbols")

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

    if not valid_symbols:
        raise RuntimeError("[FATAL] No approved symbols after validation")

    symbols = valid_symbols

    collected = 0
    failures = 0
    jobs: list[PersistenceJob] = []

    for symbol in symbols:  # process only approved symbols
        try:
            logger.info(f"[COLLECT][START] symbol={symbol}")
            df = await market_data_service.fetch_ohlcv(symbol, "1h", limit=100)
            logger.info(f"[COLLECT][RESULT] symbol={symbol} result={type(df).__name__} rows={len(df) if df is not None else 'None'}")

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

            ohlcv_exchange = df.attrs.get("exchange", "gate.io")
            latest = df.iloc[-1]
            now = datetime.now(timezone.utc)
            jobs.append(
                PersistenceJob(
                    domain="collect_all",
                    symbol=symbol,
                    market_type="spot",
                    exchange=ohlcv_exchange,
                    timeframe="1h",
                    candles=tuple(
                        OhlcvCandle(
                            time=row["time"],
                            open=float(row["open"]),
                            high=float(row["high"]),
                            low=float(row["low"]),
                            close=float(row["close"]),
                            volume=float(row["volume"]),
                            quote_volume=float(row.get("quote_volume", float(row["close"]) * float(row["volume"]))),
                        )
                        for _, row in df.iterrows()
                    ),
                    market_metadata=MarketMetadataWrite(
                        updated_at=now,
                        price=float(latest["close"]),
                    ),
                )
            )

            logger.info(f"[COLLECT][OK] symbol={symbol}")
            collected += 1
        except Exception as e:
            logger.error(
                f"[FAILED symbol={symbol}] error={str(e)}",
                exc_info=True,
            )
            failures += 1
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
        for ticker in tickers:
            try:
                pair = ticker.get("currency_pair", "")
                if not pair.endswith("_USDT"):
                    continue
                sym = pair  # keep BTC_USDT format (with underscore)
                price = float(ticker.get("last", 0) or 0)
                change = float(ticker.get("change_percentage", 0) or 0)
                volume = float(ticker.get("quote_volume", 0) or 0)
                spread = market_data_service.compute_spread_from_ticker(ticker)

                if price > 0:
                    jobs.append(
                        PersistenceJob(
                            domain="collect_all",
                            symbol=sym,
                            market_type="spot",
                            market_metadata=MarketMetadataWrite(
                                updated_at=now_ts,
                                price=price,
                                price_change_24h=change,
                                volume_24h=volume,
                                spread_pct=spread,
                                volume_24h_updated_at=now_ts,
                            ),
                        )
                    )
                    ticker_ok += 1
            except Exception as te:
                logger.debug("Ticker metadata enqueue failed for %s: %s",
                             ticker.get("currency_pair", "?"), te)
                continue

        if ticker_ok:
            logger.info("market_metadata: queued %d/%d tickers", ticker_ok, len(tickers))
        else:
            logger.error(
                "market_metadata: 0 tickers queued (fetched %d) — "
                "collect_5m backup pathway will provide fallback metadata.",
                len(tickers),
            )
    except Exception as e:
        logger.error("Failed to fetch/update metadata from tickers: %s", e)

    await run_persistence_batch(jobs, celery=True, service_name="collect-all-persistence")

    logger.info(f"[COLLECT] success={collected} fail={failures} total={len(symbols)}")
    if collected == 0:
        raise RuntimeError("zero success — all symbols failed")
    return collected


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
    from . import task_dispatch
    task_dispatch.enqueue(
        "app.tasks.compute_indicators.compute",
        dedup_key="compute",
        ttl_seconds=660,
    )
    return f"Collected {count} symbols"


async def _collect_5m_async():
    """Collect 5-minute OHLCV candles for pipeline scan freshness.

    Universe = all approved symbols (pool_coins.is_approved = true) across spot + futures.
    """
    from ..services.pool_service import get_approved_pool_symbols_with_market_type
    from ..services.persistence import (
        MarketMetadataWrite,
        OhlcvCandle,
        PersistenceJob,
        run_persistence_batch,
    )
    from ..utils.symbol_filters import filter_real_assets
    from ..database import run_db_task

    logger.info("Starting 5m market data collection (approved symbols only)...")

    async def _load_approved(db):
        return await get_approved_pool_symbols_with_market_type(db)

    symbol_market_type: dict[str, str] = await run_db_task(_load_approved, celery=True)

    raw_syms = filter_real_assets(list(symbol_market_type.keys()))

    logger.info(f"[COLLECT] approved_symbols_count={len(raw_syms)}")
    logger.info(f"[COLLECT] symbols={raw_syms}")

    if not raw_syms:
        raise RuntimeError("[FATAL] No approved symbols")

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

    if not valid_symbols:
        raise RuntimeError("[FATAL] No approved symbols after validation")

    symbols = valid_symbols

    from ..services.market_data_service import market_data_service
    from sqlalchemy import text

    collected = 0
    failures = 0
    jobs: list[PersistenceJob] = []

    for symbol in symbols:  # no cap — approved symbols only
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
                failures += 1
                continue

            ohlcv_exchange = df.attrs.get("exchange", "gate.io")
            now = datetime.now(timezone.utc)
            metadata = MarketMetadataWrite(
                updated_at=now,
                price=float(df.iloc[-1]["close"]),
            )
            try:
                ob = await market_data_service.fetch_orderbook_metrics(symbol, depth=10)
                if ob:
                    metadata = MarketMetadataWrite(
                        updated_at=now,
                        price=float(df.iloc[-1]["close"]),
                        spread_pct=ob.get("spread_pct"),
                        orderbook_depth_usdt=ob.get("orderbook_depth_usdt"),
                    )
                else:
                    logger.warning(
                        "[DATA_UNKNOWN] orderbook metrics unavailable for %s "
                        "(Gate + Binance both failed — depth will be NULL this cycle)",
                        symbol,
                    )
            except Exception as exc:
                logger.warning(
                    "[DATA_FAIL] orderbook fetch failed for %s: %s",
                    symbol, exc,
                )

            jobs.append(
                PersistenceJob(
                    domain="collect_5m",
                    symbol=symbol,
                    market_type=sym_market_type,
                    exchange=ohlcv_exchange,
                    timeframe="5m",
                    candles=tuple(
                        OhlcvCandle(
                            time=row["time"],
                            open=float(row["open"]),
                            high=float(row["high"]),
                            low=float(row["low"]),
                            close=float(row["close"]),
                            volume=float(row["volume"]),
                            quote_volume=float(row.get("quote_volume", float(row["close"]) * float(row["volume"]))),
                        )
                        for _, row in df.iterrows()
                    ),
                    market_metadata=metadata,
                )
            )

            collected += 1
            logger.info(f"[COLLECT][OK] symbol={symbol} timeframe=5m")
        except Exception as e:
            logger.error(
                f"[FAILED symbol={symbol}] timeframe=5m error={str(e)}",
                exc_info=True,
            )
            failures += 1
            continue

    # ── Backup metadata pathway: fetch tickers for volume_24h + spread ───
    try:
        tickers = await market_data_service.fetch_all_tickers()
        if tickers:
            now_ts = datetime.now(timezone.utc)
            ticker_ok = 0
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
                    jobs.append(
                        PersistenceJob(
                            domain="collect_5m",
                            symbol=pair,
                            market_metadata=MarketMetadataWrite(
                                updated_at=now_ts,
                                price=price,
                                price_change_24h=change,
                                volume_24h=volume,
                                spread_pct=spread,
                                volume_24h_updated_at=now_ts,
                            ),
                        )
                    )
                    ticker_ok += 1
                except Exception as te:
                    logger.debug("5m: backup ticker enqueue failed for %s: %s",
                                 ticker.get("currency_pair", "?"), te)
                    continue
            logger.info("5m: backup ticker metadata queued for %d symbols", ticker_ok)
    except Exception as e:
        logger.debug("5m: backup ticker fetch failed (non-blocking): %s", e)

    # Per-symbol Gate ticker fallback for long-tail pairs missing from the batch endpoint.
    try:
        async def _load_stale_symbols(db):
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
            return [r.symbol for r in stale_rows]

        stale_syms = await run_db_task(_load_stale_symbols, celery=True)
        if stale_syms:
            logger.info("5m: per-symbol Gate ticker fallback for %d stale symbols", len(stale_syms))
            refreshed = 0
            for sym in stale_syms:
                try:
                    ticker = await market_data_service._fetch_gate_ticker(sym)
                    if not ticker:
                        continue
                    price = float(ticker.get("last", 0) or 0)
                    volume = float(ticker.get("quote_volume", 0) or 0)
                    if price <= 0 or volume <= 0:
                        continue
                    ts = datetime.now(timezone.utc)
                    jobs.append(
                        PersistenceJob(
                            domain="collect_5m",
                            symbol=sym,
                            market_metadata=MarketMetadataWrite(
                                updated_at=ts,
                                price=price,
                                volume_24h=volume,
                                volume_24h_updated_at=ts,
                            ),
                        )
                    )
                    refreshed += 1
                except Exception as se:
                    logger.debug("5m: per-symbol fallback failed for %s: %s", sym, se)
                    continue
            logger.info("5m: per-symbol Gate ticker fallback refreshed %d symbols", refreshed)
    except Exception as e:
        logger.debug("5m: per-symbol stale-check skipped (non-blocking): %s", e)

    await run_persistence_batch(jobs, celery=True, service_name="collect-5m-persistence")

    logger.info(f"[COLLECT] success={collected} fail={failures} total={len(symbols)}")
    if collected == 0:
        raise RuntimeError("zero success — all symbols failed")
    return collected


@celery_app.task(name="app.tasks.collect_market_data.collect_5m")
def collect_5m():
    count = _run_async(_collect_5m_async())
    # Chain: fresh 5m candles → compute 5m indicators (microstructure queue).
    # TTL = compute_5m time_limit (180s) + 30s margin.
    from . import task_dispatch
    task_dispatch.enqueue(
        "app.tasks.compute_indicators.compute_5m",
        dedup_key="compute_5m",
        ttl_seconds=210,
    )
    return f"Collected 5m data for {count} symbols"
