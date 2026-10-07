"""Pump-only bounded payload reads; retain newest eligible decision ordering.

Backend copy of ``pump_ml/selection.py`` (2026-10-07) so the daily trainer can run
in the Celery worker image, which only ships ``backend/``. Since 2026-10-07 this copy
is the source of truth (optional context projection, configurable lookback); the
Railway ``pump_ml`` service is superseded by the Celery runner."""
from datetime import timedelta
from app.services.pump_opportunity_engine import number,utc

IDS_SQL = """SELECT observation_id FROM pump_opportunity_observations
 WHERE user_id=$1 AND ($2::timestamptz IS NULL OR decision_at >= $2)
 ORDER BY decision_at DESC,observation_id"""
CONTRACT_SQL = """SELECT payload->'manifest' FROM pump_opportunity_observations
 WHERE user_id=$1 AND observation_id=ANY($2::uuid[])
 AND payload->'manifest'->>'listing_certified'='true'
 ORDER BY decision_at DESC,observation_id LIMIT 1"""
ROWS_SQL = """SELECT jsonb_build_object('observation_id',o.observation_id,'decision_at',o.decision_at,
 'episode_id',o.episode_id,'instrument_id',o.instrument_id,'manifest',o.payload->'manifest',
 'label_spec',o.payload->'label_spec',
 'values',(SELECT jsonb_object_agg(k,v) FROM jsonb_each(o.payload->'values') AS f(k,v) WHERE k=ANY($2::text[])),
 'simulation',o.payload->'simulation','target',l.payload->'targets'->'0.8'->'hit',
 'label_status',l.payload->>'status','label_coverage_complete',l.payload->'coverage_complete') AS row
 FROM pump_opportunity_observations o
 CROSS JOIN LATERAL (SELECT payload FROM pump_opportunity_labels
   WHERE observation_id=o.observation_id AND horizon_minutes=5
   AND label_spec_hash=$5 LIMIT 1) l
 WHERE o.user_id=$1 AND o.observation_id=ANY($6::uuid[])
 AND o.payload->'manifest'->>'listing_certified'='true'
 AND l.payload->>'coverage_complete'='true'
 AND l.payload->>'status'='known'
 AND l.payload->'targets'->'0.8'->>'hit' IN('true','false')
 AND o.payload->'manifest'->>'feature_spec_hash'=$3
 AND o.payload->'manifest'->>'legacy_config_hash'=$4
 AND o.payload->'manifest'->>'label_spec_hash'=$5
 ORDER BY o.decision_at DESC,o.observation_id LIMIT $7"""


async def select_training_rows(conn,owner,features,feature_hash,max_rows,diagnostics,*,batch_size=500):
    """One consistent snapshot. Batch size changes I/O only, never eligibility.

    Iterate all candidates needed to find max_rows eligible observations, rather
    than taking max_rows raw observations first. Unknown labels remain excluded;
    feature completeness and independent temporal gates remain in the trainer.
    """
    if max_rows<=0 or batch_size<=0:raise ValueError('Invalid Pump selection read bound')
    rows=[];contract=None
    diagnostics.update(phase='latest_contract',candidate_rows=0,batches=0)
    async with conn.transaction(isolation='repeatable_read',readonly=True):
        cutoff=await conn.fetchval('SELECT now()')
        cursor=await conn.cursor(IDS_SQL,owner,None)
        while True:
            ids=[r['observation_id'] for r in await cursor.fetch(batch_size)]
            if not ids:break
            contract=await conn.fetchval(CONTRACT_SQL,owner,ids)
            if contract:break
        if not contract:return None,[]
        diagnostics['phase']='dataset_candidate_ids'
        cursor=await conn.cursor(IDS_SQL,owner,cutoff-timedelta(days=30))
        while len(rows)<max_rows:
            diagnostics['phase']='dataset_candidate_ids'
            ids=[r['observation_id'] for r in await cursor.fetch(batch_size)]
            if not ids:break
            diagnostics['candidate_rows']+=len(ids);diagnostics['batches']+=1
            diagnostics['phase']='dataset_rows'
            selected=await conn.fetch(ROWS_SQL,owner,features,feature_hash,
                contract['legacy_config_hash'],contract['label_spec_hash'],ids,max_rows-len(rows))
            rows.extend(r['row'] for r in selected)
        diagnostics.update(phase='selection_complete',selected_rows=len(rows),selection_as_of=cutoff.isoformat())
    return contract,rows


BOUNDED_IDS_SQL = """SELECT observation_id FROM pump_opportunity_observations
 WHERE user_id=$1 AND decision_at >= $2
 AND ($3::timestamptz IS NULL OR decision_at < $3 OR ($4 AND decision_at=$3))
 ORDER BY decision_at DESC,observation_id"""
EARLIEST_IDS_SQL = BOUNDED_IDS_SQL.replace('decision_at DESC','decision_at ASC')
TEMPORAL_SELECTION_VERSION='pump_temporal_equal_duration_v1'

# Direction is endpoint return relative to the captured reference, never touch.
DIRECTIONAL_ROWS_SQL = ROWS_SQL.replace(
    "'simulation',o.payload->'simulation','target',l.payload->'targets'->'0.8'->'hit',",
    "'simulation',o.payload->'simulation',"
    "'reference',o.payload->'reference','endpoint_return_pct',l.payload->'endpoint_return_pct','horizon_minutes',$8::int,"
    "'target',(l.payload->>'endpoint_return_pct')::numeric>0,")
DIRECTIONAL_ROWS_SQL = DIRECTIONAL_ROWS_SQL.replace('horizon_minutes=5', 'horizon_minutes=$8 AND labeled_at<=$9')
DIRECTIONAL_ROWS_SQL = DIRECTIONAL_ROWS_SQL.replace(
    "AND l.payload->'targets'->'0.8'->>'hit' IN('true','false')",
    "AND jsonb_typeof(l.payload->'endpoint_return_pct')='number' "
    "AND (l.payload->>'endpoint_return_pct')::numeric<>0 AND o.published_at<=$9 "
    "AND jsonb_typeof(o.payload->'reference'->'price')='number' "
    "AND (o.payload->'reference'->>'price')::numeric>0 "
    "AND o.payload->'reference'->>'policy'='gate_best_ask_v1'")

def complete_features(row,features):
    return all(number((row['values'] or {}).get(f)) for f in features)

def temporal_windows(first,last,max_rows,bins):
    """Equal elapsed-time quotas; neither class values nor scores select cuts."""
    if max_rows<=0 or bins<=0:raise ValueError('Invalid Pump temporal selection bound')
    first,last=utc(first),utc(last)
    if last<first:raise ValueError('Invalid Pump selection extent')
    count=1 if first==last else min(bins,max_rows)
    quota,remainder=divmod(max_rows,count)
    return [(first+(last-first)*i/count,first+(last-first)*(i+1)/count,
        quota+(i<remainder),i==count-1) for i in range(count)]

async def select_temporal_training_rows(conn,owner,features,feature_hash,max_rows,diagnostics,*,batch_size=500,bins=20,horizon_minutes=None,
                                        extra_features=(),lookback_days=30):
    """Sample at most max_rows across the complete-feature compatible extent.

    Empty/underfilled windows remain explicit, never filled with newest-only
    observations or label-aware picks. Original trainer gates still apply.
    All endpoint and bucket reads share the same snapshot and timeout settings.
    """
    if max_rows<=0 or batch_size<=0 or bins<=0:raise ValueError('Invalid Pump temporal selection bound')
    if horizon_minutes is not None and (type(horizon_minutes) is not int or horizon_minutes<=0):
        raise ValueError('Invalid directional horizon')
    diagnostics.update(phase='latest_contract',candidate_rows=0,endpoint_candidate_rows=0,batches=0)
    async with conn.transaction(isolation='repeatable_read',readonly=True):
        cutoff=await conn.fetchval('SELECT now()')
        cursor=await conn.cursor(IDS_SQL,owner,None);contract=None
        while True:
            ids=[r['observation_id'] for r in await cursor.fetch(batch_size)]
            if not ids:break
            contract=await conn.fetchval(CONTRACT_SQL,owner,ids)
            if contract:break
        if not contract:return None,[]
        since=cutoff-timedelta(days=lookback_days)
        # Optional context keys are projected but never required (missing → NaN in the trainer).
        projection=list(features)+[f for f in extra_features if f not in features]
        async def projected(ids,limit):
            args=(owner,projection,feature_hash,contract['legacy_config_hash'],contract['label_spec_hash'],ids,limit)
            sql=ROWS_SQL
            if horizon_minutes is not None:
                sql=DIRECTIONAL_ROWS_SQL;args=(*args,horizon_minutes,cutoff)
            return [r['row'] for r in await conn.fetch(sql,*args)]
        async def endpoint(ascending):
            diagnostics['phase']='earliest_eligible' if ascending else 'latest_eligible'
            cur=await conn.cursor(EARLIEST_IDS_SQL if ascending else BOUNDED_IDS_SQL,owner,since,None,False)
            while True:
                ids=[r['observation_id'] for r in await cur.fetch(batch_size)]
                if not ids:return None
                diagnostics['endpoint_candidate_rows']+=len(ids)
                valid=[r for r in await projected(ids,len(ids)) if complete_features(r,features)]
                if valid:
                    times=[utc(r['decision_at']) for r in valid]
                    return min(times) if ascending else max(times)
        first=await endpoint(True)
        if first is None:
            diagnostics.update(phase='selection_complete',selected_rows=0)
            return contract,[]
        last=await endpoint(False)
        windows=temporal_windows(first,last,max_rows,bins);rows=[];window_evidence=[]
        for lo,hi,quota,inclusive in windows:
            diagnostics['phase']='temporal_candidate_ids'
            cur=await conn.cursor(BOUNDED_IDS_SQL,owner,lo,hi,inclusive)
            selected=[];candidate_count=0
            while len(selected)<quota:
                diagnostics['phase']='temporal_candidate_ids'
                ids=[r['observation_id'] for r in await cur.fetch(batch_size)]
                if not ids:break
                candidate_count+=len(ids);diagnostics['candidate_rows']+=len(ids);diagnostics['batches']+=1
                diagnostics['phase']='temporal_rows'
                # Do not apply the remaining quota before feature validation:
                # invalid numeric features must not displace older eligible rows.
                valid=[r for r in await projected(ids,len(ids)) if complete_features(r,features)]
                selected.extend(valid[:quota-len(selected)])
            rows.extend(selected)
            window_evidence.append({'from':lo.isoformat(),'to':hi.isoformat(),'last_inclusive':inclusive,
                'quota':quota,'selected':len(selected),'candidate_rows':candidate_count})
        rows.sort(key=lambda r:(-utc(r['decision_at']).timestamp(),r['observation_id']))
        diagnostics.update(phase='selection_complete',selected_rows=len(rows),selection_as_of=cutoff.isoformat(),
            policy={'version':TEMPORAL_SELECTION_VERSION,'requested_bins':bins,'actual_bins':len(windows),
                'max_rows':max_rows,'first':first.isoformat(),'last':last.isoformat(),'lookback_days':lookback_days,
                'optional_context_features':len(projection)-len(features),
                'feature_eligibility_before_sampling':True,'outcome_balancing':False,
                'objective':'endpoint_direction_v1' if horizon_minutes is not None else 'legacy_touch',
                'horizon_minutes':horizon_minutes,
                'empty_window_policy':'leave_underfilled_no_newest_backfill','windows':window_evidence})
    return contract,rows
