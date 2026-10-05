"""Read-only indexed recent sample, bounded per-process single-flight cache."""
import asyncio
import json
import time
from datetime import datetime,timedelta,timezone
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from . import pump_opportunity_engine as eng
from .pump_intelligence import summarize

_cache={}
_locks={}
_failures={}

SAMPLE_SQL="""WITH bucket_picks AS MATERIALIZED (
 SELECT b.bucket,r.* FROM generate_series(0,:buckets-1) b(bucket)
 CROSS JOIN LATERAL (SELECT observation_id,user_id,slot_at FROM pump_opportunity_observations
 WHERE user_id=:u AND slot_at>=CAST(:since AS timestamptz)+make_interval(secs=>CAST(:span AS double precision)*b.bucket/:buckets)
 AND slot_at<CAST(:since AS timestamptz)+make_interval(secs=>CAST(:span AS double precision)*(b.bucket+1)/:buckets)
 ORDER BY slot_at DESC,observation_id ASC LIMIT :per_bucket_plus_one) r
), recent AS MATERIALIZED (
 SELECT *,row_number() OVER(PARTITION BY bucket ORDER BY slot_at DESC,observation_id ASC) AS ordinal FROM bucket_picks
), raw AS MATERIALIZED (
 SELECT o.payload::text AS source_text,l.payload::text AS label_text,
 l.observation_id IS NOT NULL AS has_label,l.labeled_at,r.slot_at,r.observation_id
 FROM recent r JOIN pump_opportunity_observations o ON o.observation_id=r.observation_id AND o.user_id=r.user_id
 LEFT JOIN LATERAL (SELECT observation_id,payload,labeled_at FROM pump_opportunity_labels
 WHERE observation_id=o.observation_id AND horizon_minutes=:h
 AND label_spec_hash=o.payload->'manifest'->>'label_spec_hash' LIMIT 1) l ON true
 WHERE r.ordinal<=:per_bucket
), bounded AS (
 SELECT *,sum(octet_length(source_text)+COALESCE(octet_length(label_text),0))
 OVER(ORDER BY slot_at DESC,observation_id ASC) AS cumulative_bytes,count(*) OVER() AS requested_rows
 FROM raw
)
SELECT * FROM bounded WHERE cumulative_bytes<=:bytes
UNION ALL SELECT NULL::text,NULL::text,false,NULL::timestamptz,NULL::timestamptz,NULL::uuid,
 (SELECT count(*) FROM raw),(SELECT count(*) FROM bucket_picks)
ORDER BY slot_at DESC NULLS FIRST,observation_id ASC"""



async def read_intelligence(db,user_id,conditions,horizon,refresh=False):
    from .pump_opportunity_service import get_config
    c=await get_config(db,user_id);cfg=c['intelligence']
    if horizon not in c['labels']['horizons_minutes']:raise ValueError('Unsupported configured horizon')
    if conditions:
        if len(conditions)>20:raise ValueError('Expected bounded AND conditions')
        for rule in conditions:
            if not isinstance(rule,dict) or rule.get('op') not in ('gt','gte','lt','lte','eq','between') or not rule.get('field'):
                raise ValueError('Invalid exploration rule')
    key=(str(user_id),horizon,eng.canonical_hash(cfg))
    # Fixed process bound; no database cache tables or additional resources.
    if key not in _locks:
        if len(_locks)>=60:
            victim=next(k for k,v in _locks.items() if not v.locked())
            _locks.pop(victim);_cache.pop(victim,None);_failures.pop(victim,None)
        _locks[key]=asyncio.Lock()
    async with _locks[key]:
        cached=_cache.get(key);failure=_failures.get(key)
        now=datetime.now(timezone.utc)
        recalculated=False
        needs_refresh=not cached or (refresh and time.monotonic()-cached['tick']>=cfg['cache_seconds'])
        retry_allowed=not failure or time.monotonic()-failure['tick']>=cfg['cache_seconds']
        if needs_refresh and retry_allowed:
            try:
                # Savepoint permits stale-cache fallback after a statement cancellation.
                async with db.begin_nested():
                    previous_timeout=await db.scalar(text('SHOW statement_timeout'))
                    await db.execute(text("SELECT set_config('statement_timeout',:v,true)"),{'v':f"{cfg['read_timeout_ms']}ms"})
                    rows=[dict(r) for r in (await db.execute(text(SAMPLE_SQL),{'u':user_id,'h':horizon,
                        'since':now-timedelta(hours=cfg['window_hours']),'span':cfg['window_hours']*3600,
                        'buckets':cfg['temporal_buckets'],'per_bucket':cfg['sample_limit']//cfg['temporal_buckets'],
                        'per_bucket_plus_one':cfg['sample_limit']//cfg['temporal_buckets']+1,'bytes':cfg['max_read_bytes']})).mappings()]
                    await db.execute(text("SELECT set_config('statement_timeout',:v,true)"),{'v':previous_timeout})
                header=next(r for r in rows if r['source_text'] is None)
                requested_rows=int(header['requested_rows']);available_rows=int(header['cumulative_bytes'])
                rows=[r for r in rows if r['source_text'] is not None]
                if requested_rows and not rows:raise ValueError('Individual intelligence record exceeds byte budget')
                truncated=requested_rows>cfg['sample_limit'] or len(rows)<requested_rows
                byte_limited=len(rows)<available_rows
                rows=rows[:cfg['sample_limit']]
                size=sum(len(r['source_text'].encode())+len((r['label_text'] or '').encode()) for r in rows)
                if size>cfg['max_read_bytes']:raise ValueError('Intelligence sample exceeds byte budget')
                compact=[]
                for r in rows:
                    p=json.loads(r.pop('source_text'));label=json.loads(r.pop('label_text')) if r['has_label'] else None
                    manifest={k:p['manifest'].get(k) for k in ('score_config_hash','label_spec_hash','feature_spec_hash','legacy_config_hash','cost_policy_hash')}
                    payload={k:p[k] for k in ('episode_id','instrument_id','decision_at','score_final','values')}
                    payload.update(manifest=manifest,label_version=p.get('label_version') or p.get('label_spec',{}).get('version'),
                        reference_policy=p.get('reference_policy') or p.get('reference',{}).get('policy'),
                        ledger=[{'group':e['group'],'result':e['result']} for e in p['ledger']])
                    payload['decision_at']=eng.utc(payload['decision_at']).isoformat()
                    compact.append({'payload':payload,'label':label,'labeled_at':r['labeled_at']})
                from .pump_shadow_context import read_shadow_context
                shadow=await read_shadow_context(db,user_id,cfg,now,cfg['max_read_bytes']-size)
                cached={'rows':compact,'shadow':shadow,'at':datetime.now(timezone.utc).isoformat(),'tick':time.monotonic(),
                        'read_bytes':size,'truncated':truncated,'byte_limited':byte_limited,
                        'window_from':(now-timedelta(hours=cfg['window_hours'])).isoformat()}
                _cache[key]=cached;_failures.pop(key,None);recalculated=True
            except Exception as exc:
                _failures[key]={'tick':time.monotonic()}
                if not cached:
                    if isinstance(exc,DBAPIError) and getattr(exc.orig,'sqlstate',None)=='57014':
                        raise ValueError('Intelligence refresh temporarily unavailable within read budget') from exc
                    raise
        if not cached:raise ValueError('Intelligence refresh temporarily unavailable')
        age=max(0,(datetime.now(timezone.utc)-eng.utc(cached['at'])).total_seconds())
        rows=cached['rows'];cohorts=summarize(rows,horizon,cfg['score_edges'],conditions)
        training=list((await db.execute(text("""SELECT run_id,started_at,finished_at,
          CASE WHEN status='running' AND deadline_at<now() THEN 'deadline_exceeded' ELSE status END AS status,payload
          FROM pump_ml_job_runs WHERE user_id=:u ORDER BY started_at DESC LIMIT 20"""),{'u':user_id})).mappings())
        return {'contract_version':eng.CONTRACT_VERSION,'as_of':datetime.now(timezone.utc).isoformat(),
            'computed_at':cached['at'],'data_through':max((r['payload']['decision_at'] for r in rows),default=None),
            'labels_through':max((r['labeled_at'].isoformat() for r in rows if r['labeled_at']),default=None),
            'recalculated':recalculated,'refresh_requested':refresh,'refresh_policy':'manual',
            'freshness':{'status':'stale' if key in _failures else 'snapshot',
                         'age_seconds':round(age,2),'refresh_failed':key in _failures},
            'scope':{'policy':'temporal_buckets_all_candidates','whole_history':False,'sample_limit':cfg['sample_limit'],
                     'sampled_observations':len(rows),'temporal_buckets':cfg['temporal_buckets'],'window_hours':cfg['window_hours'],'window_from':cached['window_from'],
                     'sample_truncated':cached['truncated'],'byte_limited':cached['byte_limited'],'read_bytes':cached['read_bytes'],
                     'additional_context_read_bytes':cached['shadow']['read_bytes'],
                     'combined_read_bytes':cached['read_bytes']+cached['shadow']['read_bytes'],'cache_seconds':cfg['cache_seconds'],
                     'horizon_minutes':horizon,'score_edges':cfg['score_edges'],'available_horizons':c['labels']['horizons_minutes'],
                     'capture_freshness_seconds':c['freshness_seconds']},
            'model':{'status':'observational','algorithm':'XGBoost Pump','delta':0,'probability':None,
                     'objective':c['research']['objective'],
                     'directional_horizons':c['labels']['horizons_minutes'],
                     'directional_status':'awaiting_independent_directional_validation',
                     'score_semantics':'directional ordinal score requires separate validation; not touch probability',
                     'auto_promotion':False,'reason':'no_validated_model_activation','shadow_isolation':True},
            'gates':{k:c[k] for k in ('training_job_enabled','training_enabled','inference_enabled','ml_delta_enabled','pool_connection_enabled')},
            'training_runs':[dict(r) for r in training],'cohorts':cohorts,'shadow_context':cached['shadow'],
            'notice':'Frequências descritivas correlacionadas por episódio; não são probabilidades preditivas ou ordens.'}
