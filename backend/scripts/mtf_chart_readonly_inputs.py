"""Capture non-secret profile/indicator inputs for chart parity verification.

Run in the API service environment. SELECT only; no decisions or evaluations.
"""
import asyncio
import json
import os
import asyncpg


async def main():
    url = os.environ['DATABASE_URL'].replace('postgresql+asyncpg://', 'postgresql://')
    db = await asyncpg.connect(url)
    try:
        async with db.transaction(readonly=True):
            row = await db.fetchrow("""SELECT c.user_id,c.config_json FROM config_profiles c
                WHERE c.config_type='indicators' AND c.is_active=true AND c.pool_id IS NULL
                AND EXISTS (SELECT 1 FROM pipeline_watchlists w WHERE w.user_id=c.user_id AND w.auto_refresh=true)
                ORDER BY (SELECT count(*) FROM pipeline_watchlists w WHERE w.user_id=c.user_id) DESC,c.updated_at DESC LIMIT 1""")
            uid = row['user_id']
            profiles = await db.fetch('SELECT id,name,config FROM profiles WHERE user_id=$1 AND is_active=true', uid)
            lists = await db.fetch("SELECT id,name,profile_id,level FROM pipeline_watchlists WHERE user_id=$1 AND market_mode='spot'", uid)
            symbols = await db.fetch("SELECT DISTINCT symbol FROM pool_coins WHERE market_type='spot' AND is_active=true ORDER BY symbol LIMIT 10")
            payload = {'user_id':str(uid),'indicator_config':json.loads(row['config_json']),
                       'profiles':[dict(p) for p in profiles], 'watchlists':[dict(w) for w in lists],
                       'symbols':[s['symbol'] for s in symbols]}
            print('MTF_INPUTS_JSON='+json.dumps(payload,default=str))
    finally:
        await db.close()


if __name__ == '__main__':
    asyncio.run(main())
