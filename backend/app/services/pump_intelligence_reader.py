"""Read-only indexed recent sample, bounded per-process single-flight cache."""
import asyncio
import json
import time
from datetime import datetime,timedelta,timezone
from sqlalchemy import text
from . import pump_opportunity_engine as eng
from .pump_intelligence import summarize

_cache={}
_locks={}
_failures={}

SAMPLE_SQL="""WITH recent AS MATERIALIZED (
 SELECT observation_id,user_id,decision_at,slot_at FROM pump_opportunity_observations
 WHERE user_id=:u AND slot_at>=:since ORDER BY slot_at DESC,observation_id DESC LIMIT :limit
), projected AS (
 SELECT jsonb_build_object('episode_id',o.episode_id,'instrument_id',o.instrument_id,'decision_at',o.decision_at,
 'score_final',o.payload->'score_final','values',o.payload->'values','ledger',
 (SELECT jsonb_agg(jsonb_build_object('group',e->'group','result',e->'result')) FROM jsonb_array_elements(o.payload->'ledger') e),
 'manifest',(o.payload->'manifest')-ARRAY['listing_evidence','feature_spec','eligibility_policy','liquidity_reference'],
 'label_version',o.payload->'label_spec'->'version',
 'reference_policy',o.payload->'reference'->'policy') AS payload,
 jsonb_build_object('status',l.payload->'status','coverage_complete',l.payload->'coverage_complete',
 'missing_minutes',l.payload->'missing_minutes','boundary_ambiguous',l.payload->'boundary_ambiguous',
 'order_ambiguous',l.payload->'order_ambiguous','targets',l.payload->'targets') AS label,
 l.observation_id IS NOT NULL AS has_label,l.labeled_at,r.slot_at,r.observation_id
 FROM recent r JOIN pump_opportunity_observations o ON o.observation_id=r.observation_id AND o.user_id=r.user_id
 LEFT JOIN LATERAL (SELECT observation_id,payload,labeled_at FROM pump_opportunity_labels
 WHERE observation_id=o.observation_id AND horizon_minutes=:h
 AND label_spec_hash=o.payload->'manifest'->>'label_spec_hash' LIMIT 1) l ON true
)
SELECT * FROM projected ORDER BY slot_at DESC,observation_id DESC"""


async def read_intelligence(db,user_id,conditions,horizon):
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
        needs_refresh=not cached or time.monotonic()-cached['tick']>=cfg['cache_seconds']
        retry_allowed=not failure or time.monotonic()-failure['tick']>=cfg['cache_seconds']
        if needs_refresh and retry_allowed:
            try:
                # Savepoint permits stale-cache fallback after a statement cancellation.
                async with db.begin_nested():
                    previous_timeout=await db.scalar(text('SHOW statement_timeout'))
                    await db.execute(text("SELECT set_config('statement_timeout',:v,true)"),{'v':f"{cfg['read_timeout_ms']}ms"})
                    rows=[dict(r) for r in (await db.execute(text(SAMPLE_SQL),{'u':user_id,'h':horizon,
                        'since':now-timedelta(hours=cfg['window_hours']),'limit':cfg['sample_limit']+1})).mappings()]
                    await db.execute(text("SELECT set_config('statement_timeout',:v,true)"),{'v':previous_timeout})
                truncated=len(rows)>cfg['sample_limit'];rows=rows[:cfg['sample_limit']]
                size=len(json.dumps(rows,default=str).encode())
                if size>cfg['max_read_bytes']:raise ValueError('Intelligence projected sample exceeds byte budget')
                for r in rows:
                    if not r.pop('has_label'):r['label']=None
                    # JSONB timestamp text is normalized before date grouping.
                    r['payload']['decision_at']=eng.utc(r['payload']['decision_at']).isoformat()
                cached={'rows':rows,'at':datetime.now(timezone.utc).isoformat(),'tick':time.monotonic(),
                        'read_bytes':size,'truncated':truncated,'window_from':(now-timedelta(hours=cfg['window_hours'])).isoformat()}
                _cache[key]=cached;_failures.pop(key,None)
            except Exception:
                _failures[key]={'tick':time.monotonic()}
                if not cached:raise
        if not cached:raise ValueError('Intelligence refresh temporarily unavailable')
        age=max(0,(datetime.now(timezone.utc)-eng.utc(cached['at'])).total_seconds())
        rows=cached['rows'];cohorts=summarize(rows,horizon,cfg['score_edges'],conditions)
        training=list((await db.execute(text("""SELECT run_id,started_at,finished_at,
          CASE WHEN status='running' AND deadline_at<now() THEN 'deadline_exceeded' ELSE status END AS status,payload
          FROM pump_ml_job_runs WHERE user_id=:u ORDER BY started_at DESC LIMIT 20"""),{'u':user_id})).mappings())
        return {'contract_version':eng.CONTRACT_VERSION,'as_of':datetime.now(timezone.utc).isoformat(),
            'computed_at':cached['at'],'data_through':max((r['payload']['decision_at'] for r in rows),default=None),
            'labels_through':max((r['labeled_at'].isoformat() for r in rows if r['labeled_at']),default=None),
            'freshness':{'status':'stale' if key in _failures or age>cfg['cache_seconds']*2 else 'current',
                         'age_seconds':round(age,2),'refresh_failed':key in _failures},
            'scope':{'policy':'bounded_recent_all_candidates','whole_history':False,'sample_limit':cfg['sample_limit'],
                     'sampled_observations':len(rows),'window_hours':cfg['window_hours'],'window_from':cached['window_from'],
                     'sample_truncated':cached['truncated'],'read_bytes':cached['read_bytes'],'cache_seconds':cfg['cache_seconds'],
                     'horizon_minutes':horizon,'score_edges':cfg['score_edges'],'available_horizons':c['labels']['horizons_minutes'],
                     'capture_freshness_seconds':c['freshness_seconds']},
            'model':{'status':'observational','algorithm':'XGBoost Pump','delta':0,'probability':None,
                     'auto_promotion':False,'reason':'no_validated_model_activation','shadow_isolation':True},
            'gates':{k:c[k] for k in ('training_job_enabled','training_enabled','inference_enabled','ml_delta_enabled','pool_connection_enabled')},
            'training_runs':[dict(r) for r in training],'cohorts':cohorts,
            'notice':'Frequências descritivas correlacionadas por episódio; não são probabilidades preditivas ou ordens.'}
