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
    from ..services.pool_service import get_approved_pool_symbols, get_pool_symbols
    from ..database import run_db_task
    from sqlalchemy import text

    logger.info("Starting market data collection (approved spot symbols)...")

    async def _load_spot_syms(db):
        # Buscar aprovados + total da pool no mesmo session para diferenciar
        # "pool vazia" de "pool povoada mas sem aprovados" no log de WARNING.
        approved = await get_approved_pool_symbols(db, "spot")
        total = await get_pool_symbols(db, "spot")
        return approved, len(total)

    raw_symbols, pool_count = await run_db_task(_load_spot_syms, celery=True)

    logger.info(f"[COLLECT] approved_symbols_count={len(raw_symbols)} pool_count={pool_count}")
    logger.info(f"[COLLECT] symbols={raw_symbols}")

    if not raw_symbols:
        # Task #231: pool sem aprovados é estado operacional válido (criptos no
        # pool aguardando promoção pela L3 do pipeline watchlist/profile).
        # NÃO levantar — o raise faz o Celery retry em loop, satura workers,
        # enche a fila e cascateia 4 alertas críticos no Centro Operacional.
        logger.warning(
            "[COLLECT] no approved symbols — skipping cycle "
            "(pool_count=%d, approved_count=0)",
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

    if not valid_symbols:
        # Task #231: idem — todos os símbolos aprovados foram filtrados pela
        # validação. Tratar como ciclo vazio em vez de erro fatal.
        logger.warning(
            "[COLLECT] no approved symbols after validation — skipping cycle "
            "(pool_count=%d, approved_count=%d, valid_count=0)",
            pool_count,
            len(raw_symbols),
        )
        return 0

    symbols = valid_symbols

    async def _inner(db) -> int:
        collected = 0
        failures = 0

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
                    continue

                ohlcv_exchange = df.attrs.get("exchange", "gate.io")
                logger.info(f"[PIPELINE] INSERT_START symbol={symbol} rows={len(df)}")
                # Each symbol's writes are isolated in a SAVEPOINT so that a
                # single failure never aborts the whole collection transaction.
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

                    latest = df.iloc[-1]

                    # Update market_metadata
                    await db.execute(text("""
                        INSERT INTO market_metadata (symbol, price, price_change_24h, last_updated)
                        VALUES (:symbol, :price, 0, :updated)
                        ON CONFLICT (symbol) DO UPDATE SET
                            price = :price, last_updated = :updated
                    """), {
                        "symbol": symbol,
                        "price": float(latest["close"]),
                        "updated": datetime.now(timezone.utc),
                    })

                logger.info(f"[PERSIST] success symbol={symbol}")
                logger.info(f"[COLLECT][OK] symbol={symbol}")
                collected += 1
            except Exception as e:
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
            for ticker in tickers:
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
                        ticker_ok += 1
                except Exception as te:
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
        if collected == 0:
            raise RuntimeError("zero success — all symbols failed")
        return collected

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

    if not valid_symbols:
        # Task #231: idem — ciclo 5m vazio em vez de erro fatal.
        logger.warning(
            "[COLLECT] no approved symbols after validation — skipping 5m cycle "
            "(pool_count=%d, approved_count=%d, valid_count=0)",
            pool_count,
            len(raw_syms),
        )
        return 0

    symbols = valid_symbols

    async def _inner(db) -> int:
        from ..services.market_data_service import market_data_service
        collected = 0
        failures = 0

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
                    continue

                ohlcv_exchange = df.attrs.get("exchange", "gate.io")
                logger.info(f"[PIPELINE] INSERT_START symbol={symbol} rows={len(df)} timeframe=5m")

                # SAVEPOINT 1: OHLCV candles + price seed.
                # Isolated so a single symbol failure never aborts the whole
                # collection transaction.
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

                    # Seed market_metadata with price from latest OHLCV close.
                    # Ensures every pool coin has a metadata row even when the
                    # tickers-based pathway in collect_all has failed.
                    latest_5m = df.iloc[-1]
                    await db.execute(text("""
                        INSERT INTO market_metadata (symbol, price, last_updated)
                        VALUES (:sym, :price, :ts)
                        ON CONFLICT (symbol) DO UPDATE SET
                            price = :price,
                            last_updated = :ts
                    """), {
                        "sym":   symbol,
                        "price": float(latest_5m["close"]),
                        "ts":    datetime.now(timezone.utc),
                    })

                # SAVEPOINT 2: orderbook metrics (separate SAVEPOINT so that a
                # DB failure here never rolls back the OHLCV + price writes above).
                # fetch_orderbook_metrics internally retries (Gate → Binance fallback)
                # via resilient_data_service; missing depth lands as NULL in DB, which
                # the pipeline treats as UNKNOWN (not FAIL) since orderbook_depth_usdt
                # is no longer in STRICT_META.
                try:
                    ob = await market_data_service.fetch_orderbook_metrics(symbol, depth=10)
                    if ob:
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
                                "ts":     datetime.now(timezone.utc),
                            })
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
                        ticker_ok += 1
                    except Exception as te:
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
                for sym in stale_syms:
                    try:
                        ticker = await market_data_service._fetch_gate_ticker(sym)
                        if not ticker:
                            continue
                        price = float(ticker.get("last", 0) or 0)
                        volume = float(ticker.get("quote_volume", 0) or 0)
                        if price <= 0 or volume <= 0:
                            continue
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
                                "ts":     datetime.now(timezone.utc),
                            })
                        refreshed += 1
                    except Exception as se:
                        logger.debug("5m: per-symbol fallback failed for %s: %s", sym, se)
                        continue
                logger.info("5m: per-symbol Gate ticker fallback refreshed %d symbols", refreshed)
        except Exception as e:
            logger.debug("5m: per-symbol stale-check skipped (non-blocking): %s", e)
        # run_db_task auto-commits all successful writes on exit

        logger.info(f"[COLLECT] success={collected} fail={failures} total={len(symbols)}")
        if collected == 0:
            raise RuntimeError("zero success — all symbols failed")
        return collected

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
