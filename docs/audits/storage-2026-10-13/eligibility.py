"""Read-only candidate counts. Proposed windows are not production settings."""
import json
import os
from pathlib import Path
import psycopg

queries = {
 'telemetry_age': "SELECT min(observed_at) oldest,max(observed_at) newest,count(*) rows,count(*) FILTER(WHERE observed_at < now()-interval '7 days') older_than_proposed_7d FROM ohlcv_state_ingestion_observations",
 'indicators_age': "SELECT min(time) oldest,max(time) newest,count(*) rows,count(*) FILTER(WHERE time<now()-interval '90 days') older_than_current_90d FROM indicators",
 'queue_completed': "SELECT count(*) rows,count(*) FILTER(WHERE completed_at IS NOT NULL) completed,count(*) FILTER(WHERE completed_at IS NULL) pending,count(*) FILTER(WHERE resource_block IS NOT NULL) blocked FROM pump_opportunity_label_queue",
 'queue_candidates': "SELECT count(*) eligible_rows FROM pump_opportunity_label_queue q WHERE q.completed_at < now()-interval '7 days' AND q.resource_block IS NULL AND EXISTS (SELECT 1 FROM pump_opportunity_labels l WHERE l.observation_id=q.observation_id AND l.label_spec_hash=q.label_spec_hash AND l.horizon_minutes=q.horizon_minutes)",
 'snapshots_age': "SELECT min(timestamp) oldest,max(timestamp) newest,count(*) rows,count(*) FILTER(WHERE timestamp<now()-interval '48 hours') older_than_current_48h FROM indicator_snapshots",
 'pump_expired': "SELECT min(slot_at) oldest,max(slot_at) newest,count(*) rows,count(*) FILTER(WHERE slot_at<now()-interval '30 days') older_than_configured_30d FROM pump_opportunity_observations",
 'config_extracts': "SELECT config_type, CASE WHEN config_type='pump_opportunity' THEN jsonb_build_object('budget',config_json->'budget','research_lookback_days',config_json#>'{research,lookback_days}','candle_lookback_days',config_json#>'{research,candle,lookback_days}') WHEN config_type='shadow_l3_exit_policy' THEN jsonb_build_object('retention_days',config_json->'retention_days','capture_batch_size',config_json->'capture_batch_size') ELSE jsonb_build_object('flow',config_json->'flow','snapshots',config_json->'snapshots','flow_history',config_json->'flow_history','research',config_json->'research') END config FROM config_profiles WHERE pool_id IS NULL AND is_active IS NOT FALSE AND config_type IN ('pump_opportunity','pump_monitor','shadow_l3_exit_policy')",
}
out={}
url=json.loads(os.environ['PUMP_AUDIT_VARS'])['DATABASE_PUBLIC_URL']
with psycopg.connect(url,options='-c default_transaction_read_only=on -c statement_timeout=20000') as c:
 for name,q in queries.items():
  try:
   with c.transaction():
    cur=c.execute(q);out[name]={'query':q,'rows':[dict(zip([col.name for col in cur.description],r)) for r in cur.fetchall()]}
  except Exception as exc:out[name]={'query':q,'error':type(exc).__name__}
  print(json.dumps({name:out[name]},default=str),flush=True)
Path(__file__).with_name('eligibility.json').write_text(json.dumps(out,default=str,indent=2),encoding='utf-8')
