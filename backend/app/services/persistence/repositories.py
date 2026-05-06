<<<<<<< HEAD
from __future__ import annotations

from sqlalchemy import text

from .jobs import PersistenceJob


class PersistenceRepository:
    async def persist_job(self, session, job: PersistenceJob) -> None:
        if job.candles:
            await self._persist_candles(session, job)
        if job.indicator is not None:
            await self._persist_indicator(session, job)
        if job.market_metadata is not None:
            await self._persist_market_metadata(session, job)

    async def _persist_candles(self, session, job: PersistenceJob) -> None:
        timeframe = (
            job.indicator.timeframe
            if job.indicator is not None and getattr(job.indicator, "timeframe", None)
            else job.timeframe
        )
        if not timeframe:
            raise ValueError(f"PersistenceJob missing timeframe for candle batch: {job.key}")
        for candle in job.candles:
            await session.execute(
                text("""
                    INSERT INTO ohlcv (
                        time, symbol, exchange, timeframe, market_type,
                        open, high, low, close, volume, quote_volume
                    )
                    VALUES (
                        :time, :symbol, :exchange, :timeframe, :market_type,
                        :open, :high, :low, :close, :volume, :quote_volume
                    )
                    ON CONFLICT DO NOTHING
                """),
                {
                    "time": candle.time,
                    "symbol": job.symbol,
                    "exchange": job.exchange,
                    "timeframe": timeframe,
                    "market_type": job.market_type,
                    "open": candle.open,
                    "high": candle.high,
                    "low": candle.low,
                    "close": candle.close,
                    "volume": candle.volume,
                    "quote_volume": candle.quote_volume,
                },
            )

    async def _persist_indicator(self, session, job: PersistenceJob) -> None:
        indicator = job.indicator
        await session.execute(
            text("""
                INSERT INTO indicators (
                    time, symbol, timeframe, market_type, scheduler_group, indicators_json
                )
                VALUES (
                    :time, :symbol, :timeframe, :market_type, :scheduler_group, :indicators_json
                )
                ON CONFLICT (time, symbol, timeframe)
                DO UPDATE SET
                    market_type = EXCLUDED.market_type,
                    scheduler_group = EXCLUDED.scheduler_group,
                    indicators_json = EXCLUDED.indicators_json
            """),
            {
                "time": indicator.time,
                "symbol": job.symbol,
                "timeframe": indicator.timeframe,
                "market_type": indicator.market_type,
                "scheduler_group": indicator.scheduler_group,
                "indicators_json": indicator.indicators_json,
            },
        )

    async def _persist_market_metadata(self, session, job: PersistenceJob) -> None:
        meta = job.market_metadata
        await session.execute(
            text("""
                INSERT INTO market_metadata (
                    symbol, price, price_change_24h, volume_24h, spread_pct,
                    orderbook_depth_usdt, last_updated, volume_24h_updated_at
                )
                VALUES (
                    :symbol, :price, :price_change_24h, :volume_24h, :spread_pct,
                    :orderbook_depth_usdt, :last_updated, :volume_24h_updated_at
                )
                ON CONFLICT (symbol) DO UPDATE SET
                    price = COALESCE(EXCLUDED.price, market_metadata.price),
                    price_change_24h = COALESCE(EXCLUDED.price_change_24h, market_metadata.price_change_24h),
                    volume_24h = COALESCE(EXCLUDED.volume_24h, market_metadata.volume_24h),
                    spread_pct = COALESCE(EXCLUDED.spread_pct, market_metadata.spread_pct),
                    orderbook_depth_usdt = COALESCE(EXCLUDED.orderbook_depth_usdt, market_metadata.orderbook_depth_usdt),
                    last_updated = EXCLUDED.last_updated,
                    volume_24h_updated_at = COALESCE(
                        EXCLUDED.volume_24h_updated_at,
                        market_metadata.volume_24h_updated_at
                    )
            """),
            {
                "symbol": job.symbol,
                "price": meta.price,
                "price_change_24h": meta.price_change_24h,
                "volume_24h": meta.volume_24h,
                "spread_pct": meta.spread_pct,
                "orderbook_depth_usdt": meta.orderbook_depth_usdt,
                "last_updated": meta.updated_at,
                "volume_24h_updated_at": meta.volume_24h_updated_at,
            },
        )
=======
"""Idempotent UPSERT helpers consumed by PersistenceWorkers.

Every helper accepts an open ``AsyncSession`` and a single message dataclass
from ``persistence.messages``.  They MUST NOT perform any external I/O and
MUST NOT call ``commit()``/``rollback()``: the surrounding UnitOfWork owns
the transaction.

All UPSERTs use ``ON CONFLICT`` keyed on a natural unique constraint so
repeated delivery of the same message is a no-op (or idempotent merge).
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .messages import (
    IndicatorsUpsert,
    MarketMetadataUpsert,
    OhlcvBatch,
    OhlcvCandle,
    ReconciledTradeUpsert,
)

logger = logging.getLogger(__name__)


_INSERT_OHLCV_SQL = text("""
    INSERT INTO ohlcv
        (time, symbol, exchange, timeframe, market_type,
         open, high, low, close, volume, quote_volume)
    VALUES
        (:time, :symbol, :exchange, :timeframe, :market_type,
         :open, :high, :low, :close, :volume, :quote_volume)
    ON CONFLICT DO NOTHING
""")


async def persist_ohlcv_candle(db: AsyncSession, msg: OhlcvCandle) -> None:
    await db.execute(_INSERT_OHLCV_SQL, {
        "time": msg.time,
        "symbol": msg.symbol,
        "exchange": msg.exchange,
        "timeframe": msg.timeframe,
        "market_type": msg.market_type,
        "open": float(msg.open),
        "high": float(msg.high),
        "low": float(msg.low),
        "close": float(msg.close),
        "volume": float(msg.volume),
        "quote_volume": float(msg.quote_volume),
    })


async def persist_ohlcv_batch(db: AsyncSession, msg: OhlcvBatch) -> int:
    """Insert all rows of a batch in ONE multi-row INSERT.

    A single statement with N value tuples is one network round-trip + one
    plan compile, vs. N round-trips for the per-row loop. ``ON CONFLICT
    DO NOTHING`` keeps it idempotent.
    """
    if not msg.rows:
        return 0

    placeholders: list[str] = []
    params: dict[str, object] = {}
    for i, row in enumerate(msg.rows):
        placeholders.append(
            f"(:t{i}, :sym{i}, :ex{i}, :tf{i}, :mt{i}, "
            f":o{i}, :h{i}, :l{i}, :c{i}, :v{i}, :qv{i})"
        )
        params[f"t{i}"]   = row["time"]
        params[f"sym{i}"] = msg.symbol
        params[f"ex{i}"]  = msg.exchange
        params[f"tf{i}"]  = msg.timeframe
        params[f"mt{i}"]  = msg.market_type
        params[f"o{i}"]   = float(row["open"])
        params[f"h{i}"]   = float(row["high"])
        params[f"l{i}"]   = float(row["low"])
        params[f"c{i}"]   = float(row["close"])
        params[f"v{i}"]   = float(row["volume"])
        params[f"qv{i}"]  = float(row.get(
            "quote_volume",
            float(row["close"]) * float(row["volume"]),
        ))

    sql = text(
        "INSERT INTO ohlcv (time, symbol, exchange, timeframe, market_type, "
        "open, high, low, close, volume, quote_volume) VALUES "
        + ", ".join(placeholders)
        + " ON CONFLICT DO NOTHING"
    )
    await db.execute(sql, params)
    return len(msg.rows)


async def persist_market_metadata(db: AsyncSession, msg: MarketMetadataUpsert) -> None:
    """Sparse UPSERT — only non-NULL columns overwrite existing values.

    This lets two producers update disjoint fields (e.g. collect_market_data
    writes price+volume+change, scheduler writes spread+depth) without
    clobbering each other.
    """
    # Note on the explicit casts: asyncpg refuses to infer parameter types
    # when the same ``$N`` is reused in mismatched contexts (e.g. inside a
    # CASE expression where the surrounding column types diverge), raising
    # ``AmbiguousParameterError: inconsistent types deduced for parameter``.
    # Casting on the right side of the SET pins the type so reuse is safe.
    await db.execute(text("""
        INSERT INTO market_metadata
            (symbol, price, price_change_24h, volume_24h,
             spread_pct, orderbook_depth_usdt, last_updated, volume_24h_updated_at)
        VALUES
            (:symbol, CAST(:price AS double precision),
             CAST(:price_change_24h AS double precision),
             CAST(:volume_24h AS double precision),
             CAST(:spread_pct AS double precision),
             CAST(:orderbook_depth_usdt AS double precision),
             CAST(:last_updated AS timestamptz),
             CASE WHEN CAST(:volume_24h AS double precision) IS NOT NULL
                  THEN CAST(:last_updated AS timestamptz) ELSE NULL END)
        ON CONFLICT (symbol) DO UPDATE SET
            price = COALESCE(EXCLUDED.price, market_metadata.price),
            price_change_24h = COALESCE(EXCLUDED.price_change_24h, market_metadata.price_change_24h),
            volume_24h = COALESCE(EXCLUDED.volume_24h, market_metadata.volume_24h),
            spread_pct = COALESCE(EXCLUDED.spread_pct, market_metadata.spread_pct),
            orderbook_depth_usdt = COALESCE(EXCLUDED.orderbook_depth_usdt, market_metadata.orderbook_depth_usdt),
            last_updated = EXCLUDED.last_updated,
            volume_24h_updated_at = COALESCE(EXCLUDED.volume_24h_updated_at,
                                             market_metadata.volume_24h_updated_at)
    """), {
        "symbol": msg.symbol,
        "price": msg.price,
        "price_change_24h": msg.price_change_24h,
        "volume_24h": msg.volume_24h,
        "spread_pct": msg.spread_pct,
        "orderbook_depth_usdt": msg.orderbook_depth_usdt,
        "last_updated": msg.last_updated,
    })


async def persist_indicators(db: AsyncSession, msg: IndicatorsUpsert) -> None:
    """Write an indicators row.  See ``IndicatorsUpsert.mode`` for conflict policy."""
    if msg.mode == "insert_only":
        sql = text("""
            INSERT INTO indicators
                (time, symbol, timeframe, market_type,
                 scheduler_group, indicators_json)
            VALUES
                (:time, :symbol, :timeframe, :market_type,
                 :scheduler_group, :payload)
            ON CONFLICT DO NOTHING
        """)
    else:
        sql = text("""
            INSERT INTO indicators
                (time, symbol, timeframe, market_type,
                 scheduler_group, indicators_json)
            VALUES
                (:time, :symbol, :timeframe, :market_type,
                 :scheduler_group, :payload)
            ON CONFLICT (time, symbol, timeframe)
                DO UPDATE SET
                    indicators_json = EXCLUDED.indicators_json,
                    scheduler_group = EXCLUDED.scheduler_group
        """)
    await db.execute(sql, {
        "time": msg.time,
        "symbol": msg.symbol,
        "timeframe": msg.timeframe,
        "market_type": msg.market_type,
        "scheduler_group": msg.scheduler_group,
        "payload": msg.payload_json,
    })


async def persist_reconciled_trade(db: AsyncSession, msg: ReconciledTradeUpsert) -> None:
    """Insert dedup row in ``reconciled_gate_trades`` + apply side effects.

    The dedup row's UNIQUE (connection_id, gate_trade_id) guarantees a fill is
    processed at most once.  The ``side_effects`` payload is intentionally
    typed as Any — the producer (TradeReconciliationService) builds it.
    """
    inserted = await db.execute(text("""
        INSERT INTO reconciled_gate_trades
            (connection_id, gate_trade_id, symbol, market_type, reconciled_at)
        VALUES
            (:conn_id, :trade_id, :symbol, :market_type, NOW())
        ON CONFLICT (connection_id, gate_trade_id) DO NOTHING
        RETURNING id
    """), {
        "conn_id": msg.connection_id,
        "trade_id": msg.gate_trade_id,
        "symbol": msg.symbol,
        "market_type": msg.market_type,
    })
    if inserted.first() is None:
        # Already reconciled — skip side effects.
        return

    # Side effects are simple SQL fragments produced by the reconciler.
    # Each entry is {"sql": "...", "params": {...}}; failures inside one
    # side-effect bubble up and abort the transaction (intentional: a
    # partial reconciliation must not be committed).
    #
    # IMPORTANT — non-transient failures (e.g. a malformed side-effect SQL
    # for a single fill) raise out of run_uow with NO retry.  That means
    # the dedup row is also rolled back so the next reconciler run will
    # re-attempt the same fill — fine for transient bugs, but a permanent
    # data bug here would loop forever.  When the trade reconciler is
    # migrated to this path (follow-up task), it MUST attach a poison-
    # pill counter (e.g. an attempts column) and quarantine fills that
    # exhaust it instead of re-enqueuing them indefinitely.
    for effect in msg.side_effects.get("statements", []):
        await db.execute(text(effect["sql"]), effect.get("params", {}))


# ── Dispatch table ───────────────────────────────────────────────────────────

DISPATCH: dict[type, Any] = {
    OhlcvCandle: persist_ohlcv_candle,
    OhlcvBatch: persist_ohlcv_batch,
    MarketMetadataUpsert: persist_market_metadata,
    IndicatorsUpsert: persist_indicators,
    ReconciledTradeUpsert: persist_reconciled_trade,
}
>>>>>>> f0bcd5b (Task #226: Persistence Architecture Refactor — foundation + scheduler migration)
