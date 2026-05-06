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
        timeframe = job.indicator.timeframe if job.indicator else job.timeframe
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
