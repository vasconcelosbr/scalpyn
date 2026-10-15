"""Additive Pool export. Same authorized watchlist, owner and read-only transaction."""
from sqlalchemy import text
from .radar_readonly import read_watchlist, _utc
from ..schemas.radar_readonly import IndicatorReading

INDICATOR_SQL = """
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
        """

async def read_pool_context(db, watchlist_id, owner):
    watchlist = await read_watchlist(db, watchlist_id, owner)
    if watchlist is None or watchlist.source_pool_id is None:
        return None
    pool = (await db.execute(text("""
        SELECT id,name,market_type,is_active,updated_at FROM pools
        WHERE id=:pool AND user_id=:owner
    """), {'pool':watchlist.source_pool_id,'owner':owner})).mappings().first()
    if pool is None: return None
    coins = [dict(r) for r in (await db.execute(text("""
        SELECT id,symbol,market_type,is_active,is_approved,is_tradable,
               held_for_open_position,added_at,origin,discovered_at,radar_last_seen_at
        FROM pool_coins WHERE pool_id=:pool ORDER BY symbol,id
    """), {'pool':pool['id']})).mappings().all()]
    # Different markets are queried separately; no spot/futures mixing.
    readings={}
    for market in sorted({c['market_type'] for c in coins if c['is_active']}):
        symbols=sorted({c['symbol'] for c in coins if c['is_active'] and c['market_type']==market})
        rows=await db.execute(text(INDICATOR_SQL),{'symbols':symbols,'market_type':market})
        for r in rows.mappings().all():
            stamp=_utc(r['time'])
            reading=IndicatorReading(scheduler_group=r['scheduler_group'],timeframe=r['timeframe'],
                timestamp=stamp,age_seconds=(_utc(watchlist.queried_at)-stamp).total_seconds() if stamp else None,
                values=r['indicators_json']).model_dump(mode='json')
            readings.setdefault((market,r['symbol']),[]).append(reading)
    for coin in coins: coin['indicator_readings']=readings.get((coin['market_type'],coin['symbol']),[])
    return {'contract_version':'radar_pool_context_v1','served_by':'service_readonly',
      'read_mode':'persisted-only','queried_at':watchlist.queried_at,'pool':dict(pool),
      'membership_basis':'all_persisted_pool_rows','membership_complete':True,
      'row_count':len(coins),'active_count':sum(c['is_active'] is True for c in coins),
      'coins':coins,'watchlist':watchlist.model_dump(mode='json')}
