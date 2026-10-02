"""Bounded read-only context; no model/training/strategy imports or Pump joins."""
import json
from datetime import timedelta
from sqlalchemy import text
from .pump_executive_insights import summarize_shadow

SHADOW_SQL="""WITH picks AS MATERIALIZED (
 SELECT s.* FROM generate_series(0,:buckets-1) b(bucket)
 CROSS JOIN LATERAL (SELECT id FROM shadow_trades WHERE user_id=:u
  AND created_at>=CAST(:since AS timestamptz)+make_interval(secs=>CAST(:span AS double precision)*b.bucket/:buckets)
  AND created_at<CAST(:since AS timestamptz)+make_interval(secs=>CAST(:span AS double precision)*(b.bucket+1)/:buckets)
  ORDER BY created_at DESC,id LIMIT :per_bucket) s
), projected AS MATERIALIZED (
SELECT s.created_at,s.id,s.event_id,s.symbol,s.source,s.direction,s.exchange,s.timeframe,s.profile_version_id,
 s.profile_config_hash,s.score_engine_version_id,s.score_engine_config_hash,s.feature_schema_version,
 s.capture_contract_version,s.label_contract_version,s.barrier_contract_version,s.lineage_status,
 s.entry_timestamp,s.entry_price,s.sl_price,s.sl_pct,s.tp_pct,s.timeout_candles,
 s.status,s.outcome,s.exit_timestamp,s.label_resolved_at,s.exit_price_semantics,
 s.barrier_touched,s.barrier_touched_at,
 s.config_snapshot->>'entry_price_contract_version' AS entry_price_contract_version,
 md5(jsonb_build_object('trailing',s.config_snapshot->'trailing',
  'mode',s.config_snapshot->'barrier_mode','geometry',s.config_snapshot->'barrier_geometry_policy',
  'exit_policy',s.config_snapshot->'shadow_l3_exit_policy')::text) AS exit_policy_hash
FROM picks p JOIN shadow_trades s ON s.id=p.id AND s.user_id=:u),
docs AS MATERIALIZED (SELECT created_at,id,to_jsonb(p)::text AS source_text FROM projected p),
bounded AS (SELECT *,sum(octet_length(source_text)) OVER(ORDER BY created_at DESC,id) AS read_bytes FROM docs)
SELECT source_text,(SELECT count(*) FROM docs) AS requested_rows FROM bounded WHERE read_bytes<=:bytes
UNION ALL SELECT NULL::text,(SELECT count(*) FROM docs)"""

async def read_shadow_context(db,user_id,cfg,now,remaining_bytes):
    if remaining_bytes<=0:return {'status':'unavailable','reason':'shared_read_byte_budget','read_bytes':0,'cohorts':[]}
    try:
        async with db.begin_nested():
            previous=await db.scalar(text('SHOW statement_timeout'))
            await db.execute(text("SELECT set_config('statement_timeout',:v,true)"),{'v':f"{cfg['read_timeout_ms']}ms"})
            # Same existing temporal/sample bound as Pump; no extra raw points.
            result_rows=[dict(r) for r in (await db.execute(text(SHADOW_SQL),{'u':user_id,'bytes':remaining_bytes,
                'since':now-timedelta(hours=cfg['window_hours']),
                'span':cfg['window_hours']*3600,'buckets':cfg['temporal_buckets'],
                'per_bucket':cfg['sample_limit']//cfg['temporal_buckets']})).mappings()]
            await db.execute(text("SELECT set_config('statement_timeout',:v,true)"),{'v':previous})
        header=next(r for r in result_rows if r['source_text'] is None)
        documents=[r['source_text'] for r in result_rows if r['source_text'] is not None]
        if header['requested_rows'] and not documents:
            return {'status':'unavailable','reason':'shared_read_byte_budget','read_bytes':0,'cohorts':[]}
        size=sum(len(r.encode()) for r in documents)
        if size>remaining_bytes:raise ValueError('Shadow context exceeds shared byte bound')
        rows=[json.loads(r) for r in documents]
        result=summarize_shadow(rows)
        result.update(read_bytes=size,sample_limit=cfg['sample_limit'],temporal_buckets=cfg['temporal_buckets'],
            sample_policy='temporal_created_at_all_statuses',window_hours=cfg['window_hours'],sample_truncated=True,
            byte_limited=len(rows)<header['requested_rows'])
        return result
    except Exception:
        # Savepoint protects Pump data. A missing/invalid Shadow context is not
        # zero SLs and is never forwarded as a training or inference input.
        return {'status':'unavailable','reason':'shadow_read_unavailable','read_bytes':0,'cohorts':[]}
