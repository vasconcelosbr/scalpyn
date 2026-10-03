"""Daily standalone Pump challenger. Existing DB binding, own ledger/artifacts."""
import asyncio,hashlib,json,os,tempfile
from datetime import datetime,timedelta,timezone
from pathlib import Path
from uuid import UUID,uuid4,uuid5,NAMESPACE_URL
from app.services.pump_contracts import FEATURE_SPEC
from app.services.pump_opportunity_engine import canonical_hash,config,utc

FEATURES=["rsi","adx","delta_norm","buy_persistence","cvd_slope","rvol_strict","price_progress_atr","spread_pct","estimated_slippage_buy_pct"]
MAX_ROWS=10000

def prepare(rows):
    """Mechanical challenger floors, never a model-validation/promotion gate."""
    if len(rows)<200:raise ValueError("insufficient_compatible_rows_min200_challenger_only")
    rows=sorted(rows,key=lambda r:(r["decision_at"],r["observation_id"]))
    times=sorted({r["decision_at"] for r in rows})
    if len(times)<8:raise ValueError("insufficient_distinct_temporal_captures")
    cuts=[times[int(len(times)*f)] for f in (.5,.7,.85)]
    first=rows[0]["manifest"]
    return {"features":FEATURES,"feature_spec_hash":canonical_hash(FEATURE_SPEC),
        "source_commit":os.environ.get("SOURCE_COMMIT","local_test"),
        "producer_config_hash":first["legacy_config_hash"],"label_spec":rows[0]["label_spec"],"label_spec_hash":first["label_spec_hash"],
        "reference_policy":"gate_best_ask_v1","cost_policy":rows[0]["label_spec"]["cost_policy"],"cost_policy_hash":first["cost_policy_hash"],
        "boundaries":cuts,"embargo_seconds":7200,"min_episodes":100,"min_days":1,"min_instruments":10,
        "max_rows":MAX_ROWS,"max_threads":1,"params":{"n_estimators":100,"max_depth":3,"random_state":20261001},
        "decision_threshold":.5,"support_criteria":{"status":"PROVISIONAL_CHALLENGER_ONLY","selection_floor":"mechanical_not_statistically_validated",
            "validation_required":["independent_days_regimes","cluster_intervals","ablations","temporal_calibration_review"],"auto_promotion":False}}

async def run_owner(conn,owner):
    if not await conn.fetchval("SELECT pg_try_advisory_lock(hashtextextended($1,0))",f"pump_ml:{owner}"):
        return {"owner":str(owner),"status":"singleton_busy"}
    prior=await conn.fetchval("SELECT run_id FROM pump_ml_job_runs WHERE user_id=$1 AND started_at>=date_trunc('day',now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC' LIMIT 1",owner)
    if prior:
        await conn.execute("SELECT pg_advisory_unlock(hashtextextended($1,0))",f"pump_ml:{owner}")
        return {"owner":str(owner),"status":"daily_already_recorded","run_id":str(prior)}
    run_id=uuid4();start=datetime.now(timezone.utc);selection={}
    await conn.execute("INSERT INTO pump_ml_job_runs(run_id,user_id,deadline_at,status,payload) VALUES($1,$2,$3,'running',$4)",
        run_id,owner,start+timedelta(seconds=900),{"cpu":1,"ram_bytes":2000000000,"max_runtime_seconds":900,"threads":1,"applied_delta":0})
    try:
        raw=await conn.fetchval("SELECT config_json FROM config_profiles WHERE user_id=$1 AND config_type='pump_opportunity' AND pool_id IS NULL AND is_active IS NOT FALSE ORDER BY updated_at DESC LIMIT 1",owner)
        c=config(raw)
        if not c["enabled"] or not c.get("training_job_enabled"):raise ValueError("training_job_disabled")
        used=await conn.fetchval("""SELECT sum(pg_total_relation_size(name::regclass)) FROM unnest(ARRAY[
            'pump_opportunity_observations','pump_opportunity_labels','pump_opportunity_price_paths',
            'pump_opportunity_label_queue','pump_opportunity_job_runs','pump_ml_experiments','pump_ml_predictions',
            'pump_ml_job_runs','pump_ml_artifacts']) name""")
        if used+5000000>c["budget"]["max_storage_bytes"]:raise ValueError("pump_storage_budget_exhausted")
        from pump_ml.selection import select_training_rows
        contract,rows=await select_training_rows(conn,owner,FEATURES,canonical_hash(FEATURE_SPEC),MAX_ROWS,selection)
        if not contract:raise ValueError("no_certified_point_in_time_listing_cohort")
        spec=prepare(rows)
        from app.services.pump_ml_research import train_challenger
        with tempfile.TemporaryDirectory(prefix="pump_ml_") as staging:
            result=train_challenger(rows,spec=spec,output_root=staging)
            manifest=result["manifest"];experiment=uuid5(NAMESPACE_URL,f"pump_registry:{owner}:{manifest['experiment_id']}")
            folder=Path(staging)/manifest["artifact_namespace"]
            artifacts=[(str(p.name),p.read_bytes()) for p in folder.iterdir()]
            if sum(len(content) for _,content in artifacts)>5000000:raise ValueError("artifact_budget_exceeded")
            async with conn.transaction():
                await conn.execute("""INSERT INTO pump_ml_experiments(experiment_id,user_id,manifest,metrics,artifact_namespace,status)
                    VALUES($1,$2,$3,$4,$5,'challenger') ON CONFLICT DO NOTHING""",experiment,owner,manifest,result["metrics"],manifest["artifact_namespace"])
                for name,content in artifacts:
                    await conn.execute("INSERT INTO pump_ml_artifacts(experiment_id,path,sha256,content) VALUES($1,$2,$3,$4) ON CONFLICT DO NOTHING",experiment,
                        f"{manifest['artifact_namespace']}/{name}",hashlib.sha256(content).hexdigest(),content)
            outcome={"status":"challenger","experiment_id":str(experiment),"artifact_namespace":manifest["artifact_namespace"],
                "artifacts":len(artifacts),"artifact_bytes":sum(len(v) for _,v in artifacts),"cohort_rows":result["metrics"]["cohort_rows"],"applied_delta":0,"auto_promotion":False}
    except ValueError as exc:
        outcome={"status":"blocked","reason":str(exc),"applied_delta":0,"production_model_validated":False}
    except Exception as exc:
        outcome={"status":"failed","reason":type(exc).__name__,"applied_delta":0}
    finally:
        await conn.execute("SELECT pg_advisory_unlock(hashtextextended($1,0))",f"pump_ml:{owner}")
    outcome.update(run_id=str(run_id),owner=str(owner),source_commit=os.environ.get("SOURCE_COMMIT","local_test"),
        duration_seconds=round((datetime.now(timezone.utc)-start).total_seconds(),3),selection=selection)
    await conn.execute("UPDATE pump_ml_job_runs SET finished_at=now(),status=$2,payload=$3 WHERE run_id=$1",run_id,outcome["status"],outcome)
    return outcome

async def main():
    import asyncpg
    url=os.environ["DATABASE_URL"].replace("postgresql+asyncpg://","postgresql://")
    conn=await asyncpg.connect(url,timeout=10,server_settings={"application_name":"pump_ml_job","statement_timeout":"30000"})
    for kind in ("json","jsonb"):await conn.set_type_codec(kind,encoder=json.dumps,decoder=json.loads,schema="pg_catalog")
    try:
        owner=UUID(os.environ["PUMP_OWNER_ID"])
        result=await run_owner(conn,owner)
        print(json.dumps({"pump_ml_job":result,"pid":os.getpid(),"threads":1,
            "source_commit":os.environ.get("SOURCE_COMMIT","local_test"),
            "selection_reader":"chronological_id_batches_v1"},sort_keys=True))
        if result["status"]=="failed":raise SystemExit(1)
    finally:await conn.close()

if __name__=="__main__":asyncio.run(main())
