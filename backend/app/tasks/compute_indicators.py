"""Celery Task — compute indicators using Feature Engine."""

import asyncio
import json
import logging
from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import text

from ..services import persistence as _pq
from ..tasks.celery_app import celery_app
from ..utils.indicator_merge import envelop_results, _ORDER_FLOW_KEYS, _ORDER_FLOW_AUDIT_KEYS

logger = logging.getLogger(__name__)
_STOCHASTIC_WARMUP_OVERLAP = 2


def _compute_score_fields(results: dict) -> dict:
    """Compute a deterministic basic score from flat indicator values.

    Returns four fields that are injected into ``indicators_json`` so that
    every indicators row has a self-contained score — independent of the
    user-configured robust engine in ``compute_scores``.

    Scoring criteria (each criterion contributes up to 20 points):
      * RSI in [40, 70]                    — momentum zone
      * EMA9 > EMA21                       — short-term trend up
      * volume_spike > 1.5                 — volume expansion
      * ATR% > 0.5                         — sufficient volatility
      * macd_signal == "positive"          — MACD bullish cross
      * price > vwap                       — price above VWAP

    score_normalized (0–100) = score_raw / score_max * 100
    score mirrors score_normalized for direct use in ranking/display.
    """
    score = 0
    max_score = 0

    rsi = results.get("rsi")
    if rsi is not None:
        max_score += 20
        try:
            if 40 <= float(rsi) <= 70:
                score += 20
        except (TypeError, ValueError):
            pass

    ema9 = results.get("ema9")
    ema21 = results.get("ema21")
    if ema9 is not None and ema21 is not None:
        max_score += 20
        try:
            if float(ema9) > float(ema21):
                score += 20
        except (TypeError, ValueError):
            pass

    volume_spike = results.get("volume_spike")
    if volume_spike is not None:
        max_score += 20
        try:
            if float(volume_spike) > 1.5:
                score += 20
        except (TypeError, ValueError):
            pass

    atr_pct = results.get("atr_pct")
    if atr_pct is not None:
        max_score += 20
        try:
            if float(atr_pct) > 0.5:
                score += 20
        except (TypeError, ValueError):
            pass

    if results.get("macd_signal") == "positive":
        max_score += 20
        score += 20

    price = results.get("price")
    vwap = results.get("vwap")
    if price is not None and vwap is not None:
        max_score += 20
        try:
            if float(price) > float(vwap):
                score += 20
        except (TypeError, ValueError):
            pass

    score_normalized = round((score / max_score) * 100, 2) if max_score > 0 else 0.0

    return {
        "score_raw": score,
        "score_max": max_score,
        "score_normalized": score_normalized,
        "score": score_normalized,
    }

# Source/confidence map for order-flow keys fetched from real trades.
# Technical indicators from FeatureEngine use the default "candle_computed"/0.80.
_COMPUTE_KEY_SOURCE_MAP: dict = {k: ("gate_trades", 1.00) for k in _ORDER_FLOW_KEYS}


def _merge_order_flow_into_results(results: dict, of_data: dict) -> None:
    """Merge order-flow payload into ``results`` without overwriting valid values with ``None``.

    Pre-Task #171 the merge was a single ``results.update(...)`` that
    silently overwrote a previously-computed (and still-valid)
    ``taker_ratio`` or ``volume_delta`` whenever the new ``of_data`` lookup
    returned ``None`` (empty 60s window).  Under high cadence + 100+
    symbols that produced an envelope storm of ``NO_DATA`` rows even when
    the previous snapshot had a perfectly good signal.

    New contract:
      * For the five order-flow value keys (``taker_ratio``,
        ``buy_pressure``, ``volume_delta``, ``taker_buy_volume``,
        ``taker_sell_volume``): only overwrite when the new value is not
        ``None`` OR the existing value is missing/``None``.
      * ``taker_source`` and ``taker_window`` are metadata — always
        updated so the envelope reflects the actual fetch attempt.
      * Any other key in ``of_data`` is merged with normal ``update`` semantics.
    """
    for key, value in of_data.items():
        # Diagnostic-only fields (post-#246) — surfaced via WARNING logs
        # in robust_indicators.compute, never persisted as indicators.
        if key in _ORDER_FLOW_AUDIT_KEYS:
            continue
        if key in _ORDER_FLOW_KEYS:
            if value is not None or results.get(key) is None:
                results[key] = value
        else:
            results[key] = value


def _calc_stochastic_warmup(stochastic_config: dict) -> int:
    return max(
        # Stochastic uses chained rolling windows (K → smooth → D), so the
        # final warm-up is k + smooth + d minus the two overlapped candles
        # shared at the window boundaries.
        stochastic_config.get("k", 0)
        + stochastic_config.get("smooth", 0)
        + stochastic_config.get("d", 0)
        - _STOCHASTIC_WARMUP_OVERLAP,
        0,
    )


def _calc_volume_lookback(indicators_config: dict) -> int:
    volume_spike = indicators_config.get("volume_spike", {})
    taker_ratio = indicators_config.get("taker_ratio", {})
    return max(
        int(volume_spike.get("lookback", 20)),
        int(taker_ratio.get("lookback", 20)),
    )


def _run_async(coro):
    """Run async coroutine in a sync Celery task.

    Creates a dedicated event loop per task invocation. Drains all pending
    asyncpg tasks and disposes the NullPool engine before closing the loop.

    Without dispose + drain, asyncpg schedules _terminate_graceful_close
    via loop.create_task() during GC of NullPool connections after loop.close(),
    causing RuntimeError: Event loop is closed on the next invocation.
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
        except BaseException:
            pass
        finally:
            try:
                from ..database import _celery_engine
                loop.run_until_complete(_celery_engine.dispose())
            except Exception:
                pass
            loop.close()


async def _load_market_metadata_map(db) -> dict:
    metadata_result = await db.execute(text("""
        SELECT symbol, price, volume_24h, spread_pct, orderbook_depth_usdt
        FROM market_metadata
    """))
    return {
        row.symbol: {
            "price": float(row.price) if row.price is not None else None,
            "volume_24h": float(row.volume_24h) if row.volume_24h is not None else None,
            "spread_pct": float(row.spread_pct) if row.spread_pct is not None else None,
            "orderbook_depth_usdt": float(row.orderbook_depth_usdt) if row.orderbook_depth_usdt is not None else None,
        }
        for row in metadata_result.fetchall()
    }


async def _upsert_market_metadata_snapshot(db, symbol: str, results: dict, updated_at: datetime) -> None:
    # `volume_24h` is owned by collect_market_data (Gate.io ticker). We write
    # only price/spread/depth here; the candle-aggregated figure is kept in
    # indicators_json as `volume_24h_usdt_aggregated` for diagnostics.
    await db.execute(text("""
        INSERT INTO market_metadata (
            symbol, price, spread_pct, orderbook_depth_usdt, last_updated
        )
        VALUES (:symbol, :price, :spread_pct, :orderbook_depth_usdt, :updated)
        ON CONFLICT (symbol) DO UPDATE SET
            price = COALESCE(:price, market_metadata.price),
            spread_pct = COALESCE(:spread_pct, market_metadata.spread_pct),
            orderbook_depth_usdt = COALESCE(:orderbook_depth_usdt, market_metadata.orderbook_depth_usdt),
            last_updated = :updated
    """), {
        "symbol": symbol,
        "price": results.get("price"),
        "spread_pct": results.get("spread_pct"),
        "orderbook_depth_usdt": results.get("orderbook_depth_usdt"),
        "updated": updated_at,
    })


def _derive_min_candles(indicators_config: dict, timeframe: str) -> int:
    # EMA periods are intentionally excluded from the min-candle gate.
    # pandas ewm() produces values for any series length (converging with more
    # data); including max(ema_periods)=200 would gate the entire 5m pipeline
    # behind 24h of data, preventing ATR/RSI/ADX from being computed for coins
    # that have been tracked for only a few hours.
    # We still query up to 288 candles (see query_limit_5m) when available so
    # EMA200 accuracy is preserved for coins with a longer history.
    stochastic = indicators_config.get("stochastic", {})

    required = [
        2,
        indicators_config.get("adx", {}).get("period", 0) * 2,
        indicators_config.get("rsi", {}).get("period", 0) + 1,
        indicators_config.get("macd", {}).get("slow", 0),
        indicators_config.get("atr", {}).get("period", 0),
        indicators_config.get("bollinger", {}).get("period", 0),
        indicators_config.get("zscore", {}).get("lookback", 0),
        _calc_stochastic_warmup(stochastic),
        _calc_volume_lookback(indicators_config),
        48 if timeframe in ("5m", "30m") else 24,
    ]
    return max(required)


async def _compute_async():
    from ..database import CeleryAsyncSessionLocal as AsyncSessionLocal
    from ..services.feature_engine import FeatureEngine
    from ..services.market_data_service import market_data_service
    from ..services.seed_service import DEFAULT_INDICATORS
    from ..services.order_flow_service import get_order_flow_data

    import sqlalchemy.exc as _sqla_exc
    logger.info("Starting indicator computation...")

    indicators_config = DEFAULT_INDICATORS  # System defaults for centralized computation
    engine = FeatureEngine(indicators_config)
    min_candles_1h = _derive_min_candles(indicators_config, "1h")
    query_limit_1h = max(200, min_candles_1h)
    computed = 0

    async with AsyncSessionLocal() as db:
        try:
            # Task #232: ingestion-side gate is ``is_active`` only.
            # ``is_approved`` was overloaded; the execution gate moved
            # to ``is_tradable`` and lives in evaluate_signals/execute_buy.
            symbols_result = await db.execute(text("""
                SELECT DISTINCT o.symbol
                FROM ohlcv o
                JOIN pool_coins p ON o.symbol = p.symbol
                WHERE p.is_active = true
                  AND p.market_type = 'spot'
                  AND o.time > now() - interval '7 days'
            """))
            # Task #273: deterministic sort — deadlock-prevention invariant.
            # ``SELECT DISTINCT`` returns rows in non-deterministic order,
            # so two concurrent workers iterating ``symbols`` would acquire
            # row-locks on ``market_metadata`` / ``indicators`` in
            # different orders and deadlock (same root cause as #251).
            symbols = sorted(row.symbol for row in symbols_result.fetchall())
            metadata_map = await _load_market_metadata_map(db)

            for symbol in symbols:
                try:
                    # Fetch OHLCV data for this symbol
                    ohlcv_result = await db.execute(text("""
                        SELECT time, open, high, low, close, volume, quote_volume
                        FROM ohlcv
                        WHERE symbol = :symbol AND timeframe = '1h'
                        ORDER BY time DESC
                        LIMIT :limit
                    """), {"symbol": symbol, "limit": query_limit_1h})
                    rows = ohlcv_result.fetchall()

                    if len(rows) < min_candles_1h:
                        logger.debug(
                            "Skipping 1h indicator computation for %s: only %d candles (need ≥%d)",
                            symbol, len(rows), min_candles_1h,
                        )
                        continue

                    df = pd.DataFrame([{
                        "time": r.time, "open": float(r.open), "high": float(r.high),
                        "low": float(r.low), "close": float(r.close), "volume": float(r.volume),
                        "quote_volume": float(r.quote_volume) if r.quote_volume is not None else None,
                    } for r in reversed(rows)])

                    market_data = await market_data_service.fetch_indicator_fallbacks(
                        symbol,
                        existing_data=metadata_map.get(symbol),
                    )
                    # Calculate OHLCV-based indicators
                    results = engine.calculate(df, market_data=market_data)
                    if not results:
                        continue

                    logger.debug(
                        "Indicator volume audit %s[1h]: last_base=%s last_usdt=%s agg24h_usdt=%s ticker24h_usdt=%s coverage_h=%s candles_24h=%s",
                        symbol,
                        results.get("volume_last_candle_base"),
                        results.get("volume_last_candle_usdt"),
                        results.get("volume_24h_usdt_aggregated"),
                        results.get("volume_24h_usdt"),
                        results.get("volume_24h_coverage_hours"),
                        results.get("volume_24h_candles"),
                    )

                    # Merge real order flow data (taker_ratio, buy_pressure).
                    # Window aligned to Redis buffer TTL (Task #171: TRADE_BUFFER_TTL_SECONDS=360,
                    # max consumed window 300s) so the buffer covers the entire lookback.
                    of_data = await get_order_flow_data(
                        symbol, window_seconds=300, market_type="spot"
                    )
                    _merge_order_flow_into_results(results, of_data)

                    # Compute and persist score fields inside indicators_json so
                    # every row is self-contained.  Must happen BEFORE envelop_results.
                    results.update(_compute_score_fields(results))

                    now = datetime.now(timezone.utc)

                    payload_json = json.dumps(envelop_results(
                        results,
                        default_source="candle_computed",
                        default_confidence=0.80,
                        key_source_map=_COMPUTE_KEY_SOURCE_MAP,
                    ))

                    # Task #236: persistence-queue path. When enabled, never
                    # open a savepoint here — enqueue idempotent UPSERT messages
                    # so the dedicated worker pool drains them in short
                    # transactions. Removes the structural ↔ microstructure
                    # ↔ collect_market_data lock contention on indicators /
                    # market_metadata observed in May-2026 prod.
                    if _pq.is_enabled():
                        await _pq.enqueue_or_log(
                            producer="compute-1h",
                            msg=_pq.IndicatorsUpsert(
                                category="scheduler",
                                enqueued_at=_pq.now_monotonic(),
                                symbol=symbol,
                                timeframe="1h",
                                market_type="spot",
                                scheduler_group="structural",
                                time=now,
                                payload_json=payload_json,
                                mode="upsert",
                            ),
                        )
                        # market_metadata: only price/spread/depth here.
                        # volume_24h is owned by collect_market_data (Gate ticker).
                        if (
                            results.get("price") is not None
                            or results.get("spread_pct") is not None
                            or results.get("orderbook_depth_usdt") is not None
                        ):
                            await _pq.enqueue_or_log(
                                producer="compute-1h",
                                msg=_pq.MarketMetadataUpsert(
                                    category="scheduler",
                                    enqueued_at=_pq.now_monotonic(),
                                    symbol=symbol,
                                    last_updated=now,
                                    price=results.get("price"),
                                    spread_pct=results.get("spread_pct"),
                                    orderbook_depth_usdt=results.get("orderbook_depth_usdt"),
                                ),
                            )
                    else:
                        # SAVEPOINT: isolates this symbol's writes so that a
                        # failure here does not roll back other symbols' data.
                        try:
                            async with db.begin_nested():
                                await _upsert_market_metadata_snapshot(db, symbol, results, now)

                                # Store in TimescaleDB (envelope format — value + source + confidence + status).
                                # Task #216: write ``scheduler_group`` explicitly so the read
                                # path (indicators_provider) can merge structural + microstructure
                                # rows by group rather than guessing from legacy NULL/'combined'.
                                await db.execute(text("""
                                    INSERT INTO indicators
                                        (time, symbol, timeframe, market_type, scheduler_group, indicators_json)
                                    VALUES
                                        (:time, :symbol, :timeframe, :market_type, :scheduler_group, :indicators)
                                """), {
                                    "time": now,
                                    "symbol": symbol,
                                    "timeframe": "1h",
                                    "market_type": "spot",  # 1h collector is spot-only (pool query above filters p.market_type='spot')
                                    "scheduler_group": "structural",
                                    "indicators": payload_json,
                                })
                        except Exception as _sp_exc:
                            # SAVEPOINT auto-rolled back by begin_nested context manager.
                            # See "Nested-savepoint rollback rule" gotcha — do NOT call
                            # db.rollback() here. Re-raise; outer except handles
                            # PendingRollbackError vs benign-failure paths.
                            logger.error(
                                "[ComputeIndicators] SAVEPOINT (1h) failed for %s — savepoint rolled back: %s",
                                symbol, _sp_exc,
                            )
                            raise

                    computed += 1

                except Exception as e:
                    if isinstance(e, _sqla_exc.PendingRollbackError):
                        await db.rollback()
                        logger.error(
                            "[ComputeIndicators] PendingRollbackError on 1h loop for %s — session rolled back, stopping symbol loop: %s",
                            symbol, e,
                        )
                        break
                    logger.warning(f"Failed to compute indicators for {symbol}: {e}")
                    if not db.is_active:
                        logger.error("[ComputeIndicators] Session inactive after 1h error for %s — stopping symbol loop", symbol)
                        break
                    continue

            await db.commit()
        except Exception as e:
            logger.error("Indicator computation failed: %s", e)
            await db.rollback()
            raise

    logger.info(f"Indicator computation complete: {computed} symbols")
    return computed


@celery_app.task(name="app.tasks.compute_indicators.compute")
def compute():
    """DEPRECATED — Task #262 (structural-30m refactor).

    The 1h structural pipeline was replaced by ``compute_30m`` (chained
    from ``collect_structural_30m`` @ crontab(0,30)). This stub is kept
    only to preserve the registered task name so the
    ``test_every_registered_task_is_routed`` invariant doesn't fail
    until the next code-cleanup deploy removes both the route and the
    task. The wrapper does nothing — no compute, no score chain.
    """
    logger.warning(
        "[COMPUTE-1h] DEPRECATED — replaced by compute_30m in the "
        "structural-30m refactor (Task #262). This task is a no-op."
    )
    return "DEPRECATED — use compute_30m"


async def _compute_30m_async():
    """Compute structural indicators on 30m OHLCV candles (Task #262).

    Mirrors ``_compute_async`` (1h) byte-for-byte except:
        * reads ``ohlcv`` WHERE timeframe = '30m'
        * writes ``indicators`` with timeframe = '30m'
        * scheduler_group remains ``"structural"`` (Option A — reuse the
          existing structural tag so ``indicator_merge`` keeps merging
          structural+microstructure rows by group with zero changes).
    """
    from ..database import CeleryAsyncSessionLocal as AsyncSessionLocal
    from ..services.feature_engine import FeatureEngine
    from ..services.market_data_service import market_data_service
    from ..services.seed_service import DEFAULT_INDICATORS
    from ..services.order_flow_service import get_order_flow_data

    import sqlalchemy.exc as _sqla_exc
    logger.info("[COMPUTE-30m] Starting 30m indicator computation…")

    indicators_config = DEFAULT_INDICATORS
    engine = FeatureEngine(indicators_config)
    min_candles_30m = _derive_min_candles(indicators_config, "30m")
    query_limit_30m = max(200, min_candles_30m)
    computed = 0

    async with AsyncSessionLocal() as db:
        try:
            symbols_result = await db.execute(text("""
                SELECT DISTINCT o.symbol
                FROM ohlcv o
                JOIN pool_coins p ON o.symbol = p.symbol
                WHERE p.is_active = true
                  AND p.market_type = 'spot'
                  AND o.timeframe = '30m'
                  AND o.time > now() - interval '7 days'
            """))
            # Task #273: deterministic sort — see ``_compute_async`` above.
            symbols = sorted(row.symbol for row in symbols_result.fetchall())
            metadata_map = await _load_market_metadata_map(db)

            for symbol in symbols:
                try:
                    ohlcv_result = await db.execute(text("""
                        SELECT time, open, high, low, close, volume, quote_volume
                        FROM ohlcv
                        WHERE symbol = :symbol AND timeframe = '30m'
                        ORDER BY time DESC
                        LIMIT :limit
                    """), {"symbol": symbol, "limit": query_limit_30m})
                    rows = ohlcv_result.fetchall()

                    if len(rows) < min_candles_30m:
                        logger.debug(
                            "[COMPUTE-30m] Skipping %s: only %d candles (need ≥%d)",
                            symbol, len(rows), min_candles_30m,
                        )
                        continue

                    df = pd.DataFrame([{
                        "time": r.time, "open": float(r.open), "high": float(r.high),
                        "low": float(r.low), "close": float(r.close), "volume": float(r.volume),
                        "quote_volume": float(r.quote_volume) if r.quote_volume is not None else None,
                    } for r in reversed(rows)])

                    market_data = await market_data_service.fetch_indicator_fallbacks(
                        symbol,
                        existing_data=metadata_map.get(symbol),
                    )
                    results = engine.calculate(df, market_data=market_data)
                    if not results:
                        continue

                    of_data = await get_order_flow_data(
                        symbol, window_seconds=300, market_type="spot"
                    )
                    _merge_order_flow_into_results(results, of_data)
                    results.update(_compute_score_fields(results))

                    now = datetime.now(timezone.utc)
                    payload_json = json.dumps(envelop_results(
                        results,
                        default_source="candle_computed",
                        default_confidence=0.80,
                        key_source_map=_COMPUTE_KEY_SOURCE_MAP,
                    ))

                    if _pq.is_enabled():
                        await _pq.enqueue_or_log(
                            producer="compute-30m",
                            msg=_pq.IndicatorsUpsert(
                                category="scheduler",
                                enqueued_at=_pq.now_monotonic(),
                                symbol=symbol,
                                timeframe="30m",
                                market_type="spot",
                                scheduler_group="structural",
                                time=now,
                                payload_json=payload_json,
                                mode="upsert",
                            ),
                        )
                        if (
                            results.get("price") is not None
                            or results.get("spread_pct") is not None
                            or results.get("orderbook_depth_usdt") is not None
                        ):
                            await _pq.enqueue_or_log(
                                producer="compute-30m",
                                msg=_pq.MarketMetadataUpsert(
                                    category="scheduler",
                                    enqueued_at=_pq.now_monotonic(),
                                    symbol=symbol,
                                    last_updated=now,
                                    price=results.get("price"),
                                    spread_pct=results.get("spread_pct"),
                                    orderbook_depth_usdt=results.get("orderbook_depth_usdt"),
                                ),
                            )
                    else:
                        try:
                            async with db.begin_nested():
                                await _upsert_market_metadata_snapshot(db, symbol, results, now)
                                await db.execute(text("""
                                    INSERT INTO indicators
                                        (time, symbol, timeframe, market_type, scheduler_group, indicators_json)
                                    VALUES
                                        (:time, :symbol, :timeframe, :market_type, :scheduler_group, :indicators)
                                """), {
                                    "time": now,
                                    "symbol": symbol,
                                    "timeframe": "30m",
                                    "market_type": "spot",
                                    "scheduler_group": "structural",
                                    "indicators": payload_json,
                                })
                        except Exception as _sp_exc:
                            logger.error(
                                "[COMPUTE-30m] SAVEPOINT failed for %s — savepoint rolled back: %s",
                                symbol, _sp_exc,
                            )
                            raise

                    computed += 1

                except Exception as e:
                    if isinstance(e, _sqla_exc.PendingRollbackError):
                        await db.rollback()
                        logger.error(
                            "[COMPUTE-30m] PendingRollbackError for %s — session rolled back, stopping: %s",
                            symbol, e,
                        )
                        break
                    logger.warning("[COMPUTE-30m] Failed to compute for %s: %s", symbol, e)
                    if not db.is_active:
                        logger.error("[COMPUTE-30m] Session inactive after %s — stopping", symbol)
                        break
                    continue

            await db.commit()
        except Exception as e:
            logger.error("[COMPUTE-30m] computation failed: %s", e)
            await db.rollback()
            raise

    logger.info("[COMPUTE-30m] complete: %d symbols", computed)
    return computed


@celery_app.task(name="app.tasks.compute_indicators.compute_30m")
def compute_30m():
    """Celery entry point — compute structural indicators on 30m candles.

    Enqueued by: ``collect_structural_30m.run`` (via ``task_dispatch.enqueue``).
    Chains to:   ``compute_scores.score`` (dedup_key='score', ttl=660s).
    """
    count = _run_async(_compute_30m_async())
    from . import task_dispatch
    task_dispatch.enqueue(
        "app.tasks.compute_scores.score",
        dedup_key="score",
        ttl_seconds=660,
    )
    return f"[COMPUTE-30m] Computed indicators for {count} symbols"


async def _compute_5m_async():
    """Compute technical indicators from 5-minute OHLCV candles."""
    from ..database import CeleryAsyncSessionLocal as AsyncSessionLocal
    from ..services.feature_engine import FeatureEngine
    from ..services.market_data_service import market_data_service
    from ..services.seed_service import DEFAULT_INDICATORS
    from ..services.order_flow_service import get_order_flow_data

    import sqlalchemy.exc as _sqla_exc
    logger.info("Starting 5m indicator computation...")

    indicators_config = DEFAULT_INDICATORS
    engine = FeatureEngine(indicators_config)
    computed = 0
    min_candles_5m = _derive_min_candles(indicators_config, "5m")
    query_limit_5m = max(288, min_candles_5m)

    async with AsyncSessionLocal() as db:
        try:
            # Task #232: ingestion gate is ``is_active`` only — see
            # ``compute_indicators._compute_async`` above for rationale.
            symbols_result = await db.execute(text("""
                SELECT DISTINCT o.symbol, p.market_type
                FROM ohlcv o
                JOIN pool_coins p ON o.symbol = p.symbol
                WHERE p.is_active = true
                  AND o.timeframe = '5m'
                  AND o.time > now() - interval '2 hours'
            """))
            symbol_rows = symbols_result.fetchall()
            # Task #273: deterministic sort — see ``_compute_async`` above.
            symbols = sorted(row.symbol for row in symbol_rows)
            symbol_market_type = {row.symbol: row.market_type for row in symbol_rows}
            metadata_map = await _load_market_metadata_map(db)

            for symbol in symbols:
                try:
                    ohlcv_result = await db.execute(text("""
                        SELECT time, open, high, low, close, volume, quote_volume
                        FROM ohlcv
                        WHERE symbol = :symbol AND timeframe = '5m'
                        ORDER BY time DESC
                        LIMIT :limit
                    """), {"symbol": symbol, "limit": query_limit_5m})
                    rows = ohlcv_result.fetchall()

                    if len(rows) < min_candles_5m:
                        logger.debug(
                            "Skipping 5m indicator computation for %s: only %d candles (need ≥%d)",
                            symbol, len(rows), min_candles_5m,
                        )
                        continue

                    df = pd.DataFrame([{
                        "time": r.time, "open": float(r.open), "high": float(r.high),
                        "low": float(r.low), "close": float(r.close), "volume": float(r.volume),
                        "quote_volume": float(r.quote_volume) if r.quote_volume is not None else None,
                    } for r in reversed(rows)])

                    market_data = await market_data_service.fetch_indicator_fallbacks(
                        symbol,
                        existing_data=metadata_map.get(symbol),
                    )
                    results = engine.calculate(df, market_data=market_data)
                    if not results:
                        continue

                    logger.debug(
                        "Indicator volume audit %s[5m]: last_base=%s last_usdt=%s agg24h_usdt=%s ticker24h_usdt=%s coverage_h=%s candles_24h=%s",
                        symbol,
                        results.get("volume_last_candle_base"),
                        results.get("volume_last_candle_usdt"),
                        results.get("volume_24h_usdt_aggregated"),
                        results.get("volume_24h_usdt"),
                        results.get("volume_24h_coverage_hours"),
                        results.get("volume_24h_candles"),
                    )

                    # Merge real order flow data (taker_ratio, buy_pressure).
                    # Window aligned to Redis buffer TTL (Task #171: 300s consumed, 360s TTL).
                    of_data = await get_order_flow_data(
                        symbol, window_seconds=300, market_type="spot"
                    )
                    _merge_order_flow_into_results(results, of_data)

                    # Compute and persist score fields inside indicators_json so
                    # every row is self-contained.  Must happen BEFORE envelop_results.
                    results.update(_compute_score_fields(results))

                    now = datetime.now(timezone.utc)

                    payload_json = json.dumps(envelop_results(
                        results,
                        default_source="candle_computed",
                        default_confidence=0.80,
                        key_source_map=_COMPUTE_KEY_SOURCE_MAP,
                    ))

                    # Task #236: persistence-queue path. See _compute_async for rationale.
                    if _pq.is_enabled():
                        await _pq.enqueue_or_log(
                            producer="compute-5m",
                            msg=_pq.IndicatorsUpsert(
                                category="scheduler",
                                enqueued_at=_pq.now_monotonic(),
                                symbol=symbol,
                                timeframe="5m",
                                market_type=symbol_market_type.get(symbol, "spot"),
                                scheduler_group="microstructure",
                                time=now,
                                payload_json=payload_json,
                                mode="upsert",
                            ),
                        )
                        if (
                            results.get("price") is not None
                            or results.get("spread_pct") is not None
                            or results.get("orderbook_depth_usdt") is not None
                        ):
                            await _pq.enqueue_or_log(
                                producer="compute-5m",
                                msg=_pq.MarketMetadataUpsert(
                                    category="scheduler",
                                    enqueued_at=_pq.now_monotonic(),
                                    symbol=symbol,
                                    last_updated=now,
                                    price=results.get("price"),
                                    spread_pct=results.get("spread_pct"),
                                    orderbook_depth_usdt=results.get("orderbook_depth_usdt"),
                                ),
                            )
                    else:
                        # SAVEPOINT: isolates this symbol's writes so that a
                        # failure here does not roll back other symbols' data.
                        try:
                            async with db.begin_nested():
                                await _upsert_market_metadata_snapshot(db, symbol, results, now)
                                # Store in TimescaleDB (envelope format — value + source + confidence + status).
                                # Task #216: explicit ``scheduler_group='microstructure'`` so the
                                # read path can identify which cadence wrote each row.
                                await db.execute(text("""
                                    INSERT INTO indicators
                                        (time, symbol, timeframe, market_type, scheduler_group, indicators_json)
                                    VALUES
                                        (:time, :symbol, :timeframe, :market_type, :scheduler_group, :indicators)
                                """), {
                                    "time":            now,
                                    "symbol":          symbol,
                                    "timeframe":       "5m",
                                    "market_type":     symbol_market_type.get(symbol, "spot"),
                                    "scheduler_group": "microstructure",
                                    "indicators":      payload_json,
                                })
                        except Exception as _sp_exc:
                            # SAVEPOINT auto-rolled back by begin_nested context manager.
                            # See "Nested-savepoint rollback rule" gotcha — do NOT call
                            # db.rollback() here.
                            logger.error(
                                "[ComputeIndicators] SAVEPOINT (5m) failed for %s — savepoint rolled back: %s",
                                symbol, _sp_exc,
                            )
                            raise

                    computed += 1

                except Exception as e:
                    if isinstance(e, _sqla_exc.PendingRollbackError):
                        await db.rollback()
                        logger.error(
                            "[ComputeIndicators] PendingRollbackError on 5m loop for %s — session rolled back, stopping symbol loop: %s",
                            symbol, e,
                        )
                        break
                    logger.warning(f"Failed to compute 5m indicators for {symbol}: {e}")
                    if not db.is_active:
                        logger.error("[ComputeIndicators] Session inactive after 5m error for %s — stopping symbol loop", symbol)
                        break
                    continue

            await db.commit()
        except Exception as e:
            logger.error("5m indicator computation failed: %s", e)
            await db.rollback()
            raise

    logger.info(f"5m indicator computation complete: {computed} symbols")
    return computed


@celery_app.task(name="app.tasks.compute_indicators.compute_5m")
def compute_5m():
    count = _run_async(_compute_5m_async())
    # Chain: fresh 5m indicators → pipeline scan (microstructure queue).
    # TTL = pipeline_scan time_limit (180s) + 30s margin.
    from . import task_dispatch
    task_dispatch.enqueue(
        "app.tasks.pipeline_scan.scan",
        dedup_key="pipeline_scan",
        ttl_seconds=210,
    )
    return f"Computed 5m indicators for {count} symbols"


# ─────────────────────────────────────────────────────────────────────────────
# Structural-on-5m pipeline (close gap entre cadência 30m e leitor estrutural)
# ─────────────────────────────────────────────────────────────────────────────
# Antes desta task: ``_compute_5m_async`` já calculava rsi/macd/adx/bollinger
# sobre candles 5m, mas persistia com ``scheduler_group='microstructure'``.
# O leitor canônico ``indicators_provider.get_merged_indicators`` filtra por
# grupo, então o consumidor estrutural (score, watchlist L3, ML) só enxergava
# a cadência 30m de ``compute_30m`` (idade p99 ≈ 1770 s).
#
# ``_compute_structural_5m_async`` reusa exatamente os mesmos candles 5m e o
# mesmo ``FeatureEngine`` — única diferença é ``group="structural"`` (filtro
# pelas chaves canônicas em ``indicator_classifier.STRUCTURAL_CALC_KEYS``,
# zero hardcode aqui) e a tag ``scheduler_group='structural'`` na escrita.
# Resultado: idade do bloco estrutural lido pelo merged provider cai para
# ~330 s (5 min de cadence + chain TTL), sem alterar ``compute_5m`` micro
# nem ``compute_30m`` nem o read-side.
#
# Não escreve em ``market_metadata`` (responsabilidade do compute_5m micro
# — escrever de novo aqui geraria contention de UPSERT em hot rows; ver
# gotchas Tasks #245/#251/#273) e não puxa order-flow nem ``_compute_score_fields``
# (esses são consumidos pelo robust score, alimentado pelo merged provider).
async def _compute_structural_5m_async():
    """Compute STRUCTURAL indicators (rsi/adx/macd/bollinger) sobre candles 5m.

    Roteada para ``QUEUE_STRUCTURAL`` (não competir com o worker-micro).
    Convive com ``compute_5m`` (microstructure) e ``compute_30m`` (structural@30m):
    a UNIQUE real em ``indicators`` é ``(time, symbol, timeframe)`` — coexistência
    é viabilizada por ``time=now()`` distinto por execução (linhas separadas no
    índice ``(symbol, scheduler_group, time DESC)``) E por ``mode="insert_only"``
    aqui (DO NOTHING em colisão de microsegundo, ver bloco abaixo). NÃO usar
    ``mode="upsert"`` sem antes migrar a UNIQUE para incluir ``scheduler_group``.
    """
    from ..database import CeleryAsyncSessionLocal as AsyncSessionLocal
    from ..services.feature_engine import FeatureEngine
    from ..services.seed_service import DEFAULT_INDICATORS

    import sqlalchemy.exc as _sqla_exc
    logger.info("[COMPUTE-S5m] Starting structural-on-5m indicator computation…")

    indicators_config = DEFAULT_INDICATORS
    engine = FeatureEngine(indicators_config)
    min_candles_5m = _derive_min_candles(indicators_config, "5m")
    query_limit_5m = max(288, min_candles_5m)
    computed = 0

    async with AsyncSessionLocal() as db:
        try:
            # Task #232: ingestion gate é ``is_active`` only.
            symbols_result = await db.execute(text("""
                SELECT DISTINCT o.symbol, p.market_type
                FROM ohlcv o
                JOIN pool_coins p ON o.symbol = p.symbol
                WHERE p.is_active = true
                  AND o.timeframe = '5m'
                  AND o.time > now() - interval '2 hours'
            """))
            symbol_rows = symbols_result.fetchall()
            # Task #273: deterministic sort — invariante anti-deadlock 40P01
            # (lint test ``test_pipeline_symbol_ordering_invariants``).
            symbols = sorted(row.symbol for row in symbol_rows)
            symbol_market_type = {row.symbol: row.market_type for row in symbol_rows}

            for symbol in symbols:
                try:
                    ohlcv_result = await db.execute(text("""
                        SELECT time, open, high, low, close, volume, quote_volume
                        FROM ohlcv
                        WHERE symbol = :symbol AND timeframe = '5m'
                        ORDER BY time DESC
                        LIMIT :limit
                    """), {"symbol": symbol, "limit": query_limit_5m})
                    rows = ohlcv_result.fetchall()

                    if len(rows) < min_candles_5m:
                        logger.debug(
                            "[COMPUTE-S5m] Skipping %s: only %d candles (need ≥%d)",
                            symbol, len(rows), min_candles_5m,
                        )
                        continue

                    df = pd.DataFrame([{
                        "time": r.time, "open": float(r.open), "high": float(r.high),
                        "low": float(r.low), "close": float(r.close), "volume": float(r.volume),
                        "quote_volume": float(r.quote_volume) if r.quote_volume is not None else None,
                    } for r in reversed(rows)])

                    # ``group="structural"`` aplica filtro por
                    # ``indicator_classifier.STRUCTURAL_CALC_KEYS`` — zero hardcode
                    # de lista de indicadores aqui. ``market_data=None`` porque
                    # spread/depth/volume_24h são micro (escritos pelo compute_5m).
                    results = engine.calculate(df, market_data=None, group="structural")
                    if not results:
                        continue

                    now = datetime.now(timezone.utc)
                    payload_json = json.dumps(envelop_results(
                        results,
                        default_source="candle_computed",
                        default_confidence=0.80,
                        key_source_map=_COMPUTE_KEY_SOURCE_MAP,
                    ))

                    if _pq.is_enabled():
                        # ``mode="insert_only"`` (ON CONFLICT DO NOTHING) — a UNIQUE
                        # constraint real em ``indicators`` é ``(time, symbol, timeframe)``
                        # SEM ``scheduler_group``. ``compute_5m`` micro grava com o
                        # mesmo ``(symbol, timeframe='5m')`` e ``time=now()`` em
                        # microsegundos diferentes (linhas separadas no índice
                        # ``(symbol, scheduler_group, time DESC)``). Em uma colisão
                        # de microsegundo improvável, ``insert_only`` garante que
                        # esta task NUNCA sobrescreva a linha do micro — o próximo
                        # ciclo de 5min reescreve. Trocar para ``upsert`` aqui
                        # abriria a porta pra perder ``volume_delta``/``taker_ratio``
                        # do micro num race. Migrar a UNIQUE para incluir
                        # ``scheduler_group`` resolveria de vez, mas exige migration
                        # (fora do escopo aditivo desta task).
                        await _pq.enqueue_or_log(
                            producer="compute-structural-5m",
                            msg=_pq.IndicatorsUpsert(
                                category="scheduler",
                                enqueued_at=_pq.now_monotonic(),
                                symbol=symbol,
                                timeframe="5m",
                                market_type=symbol_market_type.get(symbol, "spot"),
                                scheduler_group="structural",
                                time=now,
                                payload_json=payload_json,
                                mode="insert_only",
                            ),
                        )
                    else:
                        # SAVEPOINT por símbolo — falha isolada não derruba o lote.
                        # Nested-savepoint rule (Task #222): re-raise sem db.rollback().
                        # ON CONFLICT DO NOTHING — mesma justificativa do branch
                        # persistence_queue acima.
                        try:
                            async with db.begin_nested():
                                await db.execute(text("""
                                    INSERT INTO indicators
                                        (time, symbol, timeframe, market_type, scheduler_group, indicators_json)
                                    VALUES
                                        (:time, :symbol, :timeframe, :market_type, :scheduler_group, :indicators)
                                    ON CONFLICT (time, symbol, timeframe) DO NOTHING
                                """), {
                                    "time":            now,
                                    "symbol":          symbol,
                                    "timeframe":       "5m",
                                    "market_type":     symbol_market_type.get(symbol, "spot"),
                                    "scheduler_group": "structural",
                                    "indicators":      payload_json,
                                })
                        except Exception as _sp_exc:
                            logger.error(
                                "[COMPUTE-S5m] SAVEPOINT failed for %s — savepoint rolled back: %s",
                                symbol, _sp_exc,
                            )
                            raise

                    computed += 1

                except Exception as e:
                    if isinstance(e, _sqla_exc.PendingRollbackError):
                        await db.rollback()
                        logger.error(
                            "[COMPUTE-S5m] PendingRollbackError for %s — session rolled back, stopping: %s",
                            symbol, e,
                        )
                        break
                    logger.warning("[COMPUTE-S5m] Failed to compute for %s: %s", symbol, e)
                    if not db.is_active:
                        logger.error("[COMPUTE-S5m] Session inactive after %s — stopping", symbol)
                        break
                    continue

            await db.commit()
        except Exception as e:
            logger.error("[COMPUTE-S5m] computation failed: %s", e)
            await db.rollback()
            raise

    logger.info("[COMPUTE-S5m] complete: %d symbols", computed)
    return computed


@celery_app.task(name="app.tasks.compute_indicators.compute_structural_5m")
def compute_structural_5m():
    """Celery entry point — structural indicators on 5m candles.

    Enqueued by: ``collect_market_data.collect_5m`` (chain paralelo ao compute_5m
    micro). Não encadeia para ``score`` nem ``pipeline_scan`` — esses já são
    acionados pelo ``compute_30m`` (estrutural canônico) e pelo ``compute_5m``
    (micro). Encadear aqui geraria storms.
    """
    count = _run_async(_compute_structural_5m_async())
    return f"[COMPUTE-S5m] Computed structural indicators for {count} symbols"
