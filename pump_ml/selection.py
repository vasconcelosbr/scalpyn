"""Pump-only bounded payload reads; retain newest eligible decision ordering."""
from datetime import timedelta

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
 'label_coverage_complete',l.payload->'coverage_complete') AS row
 FROM pump_opportunity_observations o
 CROSS JOIN LATERAL (SELECT payload FROM pump_opportunity_labels
   WHERE observation_id=o.observation_id AND horizon_minutes=5
   AND label_spec_hash=$5 LIMIT 1) l
 WHERE o.user_id=$1 AND o.observation_id=ANY($6::uuid[])
 AND o.payload->'manifest'->>'listing_certified'='true'
 AND l.payload->>'coverage_complete'='true'
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
