"""Read-only Pump v2 JSONL export preserving immutable payloads and label contracts."""
import argparse
import asyncio
import json
import os
from uuid import UUID


async def export(user_id,start,end,output):
    import asyncpg
    from app.services.pump_opportunity_engine import utc
    url=os.environ.get("DATABASE_PUBLIC_URL") or os.environ["DATABASE_URL"]
    conn=await asyncpg.connect(url.replace("postgresql+asyncpg://","postgresql://"),timeout=10)
    await conn.set_type_codec("jsonb",encoder=json.dumps,decoder=json.loads,schema="pg_catalog")
    count=0
    try:
        async with conn.transaction(readonly=True):
            await conn.execute("SET LOCAL statement_timeout = '30000ms'")
            cursor=conn.cursor("""SELECT o.payload,COALESCE(jsonb_agg(l.payload) FILTER(WHERE l.payload IS NOT NULL),'[]'::jsonb) labels
                FROM pump_opportunity_observations o LEFT JOIN pump_opportunity_labels l ON l.observation_id=o.observation_id
                WHERE o.user_id=$1 AND o.decision_at >= $2 AND o.decision_at < $3
                GROUP BY o.observation_id ORDER BY o.decision_at,o.observation_id""",UUID(user_id),utc(start),utc(end),prefetch=100)
            with open(output,"x",encoding="utf-8") as target:
                async for row in cursor:
                    target.write(json.dumps({"observation":row["payload"],"labels":row["labels"]},sort_keys=True)+"\n")
                    count+=1
    finally:
        await conn.close()
    print(json.dumps({"observations":count,"output":output,"format":"pump_opportunity_v2_jsonl"}))


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    for key in ("user-id","start","end","output"):p.add_argument(f"--{key}",required=True)
    a=p.parse_args();asyncio.run(export(a.user_id,a.start,a.end,a.output))
