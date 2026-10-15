"""Persisted export only: no pipeline, scoring, cache, HTTP or task imports."""
from contextlib import asynccontextmanager
from datetime import timezone
from uuid import UUID

from sqlalchemy import text

from ..database import AsyncSessionLocal
from ..schemas.radar_readonly import IndicatorReading, RadarAsset, RadarWatchlistResponse


@asynccontextmanager
async def readonly_session():
    # A dedicated session, not the request's writable get_db dependency.
    async with AsyncSessionLocal(autoflush=False) as db:
        try:
            await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            yield db
        finally:
            await db.rollback()


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


async def read_watchlist(db, watchlist_id: UUID, owner: UUID):
    wl = (await db.execute(text("""
        SELECT id, name, level, market_mode, source_pool_id, source_watchlist_id,
               last_scanned_at, transaction_timestamp() AS queried_at
        FROM pipeline_watchlists WHERE id = :id AND user_id = :owner
    """), {"id": watchlist_id, "owner": owner})).mappings().first()
    if wl is None:
        return None
    rows = (await db.execute(text("""
        SELECT id, symbol, current_price, price_change_24h, volume_24h, market_cap,
               alpha_score, entered_at, refreshed_at, analysis_snapshot
        FROM pipeline_watchlist_assets
        WHERE watchlist_id = :id AND (level_direction IS NULL OR level_direction = 'up')
        ORDER BY symbol, id
    """), {"id": watchlist_id})).mappings().all()
    readings = {}
    if rows:
        # Export every persisted group/timeframe separately. Never use the
        # execution merge: that can derive hybrid indicators on demand.
        # Loose index scans over ix_indicators_identity_latest enumerate identities
        # without scanning/sorting their complete Timescale history or payloads.
        result = await db.execute(text("""
            WITH RECURSIVE requested AS (
                SELECT unnest(CAST(:symbols AS text[])) AS symbol
            ), frames AS (
                SELECT r.symbol, first_frame.timeframe
                FROM requested r CROSS JOIN LATERAL (
                    SELECT timeframe FROM indicators
                    WHERE symbol = r.symbol AND market_type = :market_type
                    ORDER BY timeframe LIMIT 1
                ) first_frame
                UNION ALL
                SELECT f.symbol, next_frame.timeframe
                FROM frames f CROSS JOIN LATERAL (
                    SELECT timeframe FROM indicators
                    WHERE symbol = f.symbol AND market_type = :market_type
                      AND timeframe > f.timeframe
                    ORDER BY timeframe LIMIT 1
                ) next_frame
            ), groups AS (
                SELECT f.symbol, f.timeframe, first_group.scheduler_group
                FROM frames f CROSS JOIN LATERAL (
                    SELECT scheduler_group FROM indicators
                    WHERE symbol = f.symbol AND market_type = :market_type
                      AND timeframe = f.timeframe AND scheduler_group IS NOT NULL
                    ORDER BY scheduler_group LIMIT 1
                ) first_group
                UNION ALL
                SELECT g.symbol, g.timeframe, next_group.scheduler_group
                FROM groups g CROSS JOIN LATERAL (
                    SELECT scheduler_group FROM indicators
                    WHERE symbol = g.symbol AND market_type = :market_type
                      AND timeframe = g.timeframe AND scheduler_group > g.scheduler_group
                    ORDER BY scheduler_group LIMIT 1
                ) next_group
            )
            SELECT g.symbol, g.scheduler_group, g.timeframe, latest.time,
                   latest.indicators_json
            FROM groups g CROSS JOIN LATERAL (
                SELECT time, indicators_json FROM indicators
                WHERE symbol = g.symbol AND timeframe = g.timeframe
                  AND scheduler_group = g.scheduler_group AND market_type = :market_type
                ORDER BY time DESC LIMIT 1
            ) latest
            UNION ALL
            SELECT f.symbol, NULL AS scheduler_group, f.timeframe, latest.time,
                   latest.indicators_json
            FROM frames f CROSS JOIN LATERAL (
                SELECT time, indicators_json FROM indicators
                WHERE symbol = f.symbol AND timeframe = f.timeframe
                  AND scheduler_group IS NULL AND market_type = :market_type
                ORDER BY time DESC LIMIT 1
            ) latest
            ORDER BY symbol, scheduler_group, timeframe
        """), {"symbols": [r["symbol"] for r in rows], "market_type": wl["market_mode"]})
        for item in result.mappings().all():
            timestamp = _utc(item["time"])
            age = (_utc(wl["queried_at"]) - timestamp).total_seconds() if timestamp else None
            readings.setdefault(item["symbol"], []).append(IndicatorReading(
                scheduler_group=item["scheduler_group"], timeframe=item["timeframe"],
                timestamp=timestamp, age_seconds=age, values=item["indicators_json"],
            ))
    assets = [RadarAsset(**dict(row), indicator_readings=readings.get(row["symbol"], []))
              for row in rows]
    return RadarWatchlistResponse(
        queried_at=wl["queried_at"], watchlist_id=wl["id"], watchlist_name=wl["name"],
        level=wl["level"], market_mode=wl["market_mode"], source_pool_id=wl["source_pool_id"],
        source_watchlist_id=wl["source_watchlist_id"], last_scanned_at=wl["last_scanned_at"],
        asset_count=len(assets), assets=assets,
    )
