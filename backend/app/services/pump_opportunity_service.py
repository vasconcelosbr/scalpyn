"""Bounded Pump-only persistence/read API; legacy Pool/Shadow/exit stay untouched."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime,timedelta,timezone
from sqlalchemy import text
from . import pump_opportunity_engine as eng

logger=logging.getLogger(__name__)
CONFIG_TYPE="pump_opportunity"


async def storage_bytes(db):
    return int((await db.execute(text("""SELECT pg_total_relation_size('pump_opportunity_observations')
        +pg_total_relation_size('pump_opportunity_labels')+pg_total_relation_size('pump_ml_experiments')
        +pg_total_relation_size('pump_ml_predictions')
        +COALESCE(pg_total_relation_size(to_regclass('pump_opportunity_price_paths')),0)"""))).scalar())


async def pending_support_symbols(db,user_id,eligible,minute):
    c=await get_config(db,user_id)
    if not c["enabled"]: return []
    return list((await db.execute(text("""SELECT DISTINCT symbol FROM pump_opportunity_observations o
        WHERE user_id=:u AND decision_at>=:lo AND decision_at<=:minute
          AND NOT(symbol=ANY(CAST(:eligible AS text[])))
          AND EXISTS(SELECT 1 FROM jsonb_array_elements_text(o.payload->'label_spec'->'horizons_minutes') h(value)
            WHERE decision_at + make_interval(mins=>h.value::integer)>=:minute
              AND NOT EXISTS(SELECT 1 FROM pump_opportunity_labels l WHERE l.observation_id=o.observation_id
                AND l.horizon_minutes=h.value::integer AND l.label_spec_hash=o.payload->'manifest'->>'label_spec_hash'))
        ORDER BY symbol LIMIT :limit"""),{"u":user_id,"eligible":eligible,"minute":minute,
        "lo":minute-timedelta(minutes=max(c["labels"]["horizons_minutes"])),"limit":c["budget"]["max_assets"]})).scalars().all())


async def get_config(db,user_id):
    stored=(await db.execute(text("""SELECT config_json FROM config_profiles
        WHERE user_id=:u AND config_type=:t AND pool_id IS NULL AND is_active IS NOT FALSE
        ORDER BY updated_at DESC NULLS LAST LIMIT 1"""),{"u":user_id,"t":CONFIG_TYPE})).scalar()
    return eng.config(dict(stored) if stored else None)


async def put_config(db,user_id,requested):
    from .config_service import config_service
    c=eng.config(eng.merge(await get_config(db,user_id),requested))
    await config_service.update_config(db,CONFIG_TYPE,user_id,c,changed_by=user_id,
        change_description=f"Pump-only observation contract {eng.canonical_hash(c)}; delta=0; disconnected")
    return c


async def latest(db,user_id,c=None):
    c=c or await get_config(db,user_id)
    used=await storage_bytes(db)
    now=datetime.now(timezone.utc)
    rows=(await db.execute(text("""SELECT DISTINCT ON(instrument_id) payload,published_at
        FROM pump_opportunity_observations WHERE user_id=:u
          AND decision_at>=:since ORDER BY instrument_id,decision_at DESC"""),
        {"u":user_id,"since":now-timedelta(seconds=c["episode_gap_seconds"]+c["freshness_seconds"])})).mappings().all()
    out=[]
    for r in rows:
        p=dict(r["payload"]); p["published_at"]=r["published_at"].isoformat()
        age=(now-eng.utc(p["decision_at"])).total_seconds()
        p["freshness"]={"age_seconds":round(age,2),"status":"atrasado" if age>c["freshness_seconds"] else "atualizado"}
        if age>c["freshness_seconds"]:
            p["simulation"]={**p["simulation"],"eligible":False}
            p["vetos"]=[*p["vetos"],"feed_stale"]
        out.append(p)
    out.sort(key=lambda r:(-(r["score_final"] if r["score_final"] is not None else -1),r["symbol"],r["observation_id"]))
    return {"contract_version":eng.CONTRACT_VERSION,"as_of":now.isoformat(),"produced_at":max((p["published_at"] for p in out),default=None),
            "status":"disabled" if not c["ui_enabled"] else "storage_budget_exhausted" if used>=c["budget"]["max_storage_bytes"] else "ready" if out else "collecting",
            "cadence_seconds":60,"horizon_minutes":5,"rows":out if c["ui_enabled"] else [],
            "storage":{"used_bytes":used,"limit_bytes":c["budget"]["max_storage_bytes"]},
            "total_eligible":len(out),"total_blocked":sum(bool(r["vetos"]) for r in out),
            "total_stale":sum(r["freshness"]["status"]=="atrasado" for r in out),"config":c,
            "notice":"Observação; score de confirmações não é probabilidade nem ordem"}


async def ingest(user_id,rows,collected,source_meta,legacy_config):
    from ..database import run_db_task
    c=await run_db_task(lambda db:get_config(db,user_id),celery=True)
    if not c["enabled"]: return {"status":"disabled"}
    decision=datetime.now(timezone.utc)
    slot=decision.replace(second=0,microsecond=0)
    # First immutable capture wins. Repeated 30s legacy cycles cannot move references.
    async def _write(db):
        await db.execute(text("SET LOCAL statement_timeout = '3000ms'"))
        used=await storage_bytes(db)
        if used>=c["budget"]["max_storage_bytes"]:
            return {"status":"storage_budget_exhausted","used_bytes":used,"limit_bytes":c["budget"]["max_storage_bytes"]}
        previous=(await db.execute(text("""SELECT DISTINCT ON(instrument_id) payload FROM pump_opportunity_observations
            WHERE user_id=:u AND decision_at>=:since ORDER BY instrument_id,decision_at DESC"""),
            {"u":user_id,"since":decision-timedelta(seconds=c["episode_gap_seconds"])})).scalars().all()
        previous={p["symbol"]:p for p in previous}
        price_paths_written=0
        if c["price_paths_enabled"]:
            remaining=c["budget"]["max_price_points_per_cycle"]
            for symbol,data in sorted(collected.items())[:c["budget"]["max_assets"]]:
                if not data or not data.get("raw_price_input") or not data.get("buckets"):continue
                bucket=max(data["buckets"],key=lambda b:b["bucket_start_ms"])
                path=eng.price_path(data["raw_price_input"],bucket,min(remaining,c["budget"]["max_price_points_per_minute"]))
                remaining-=len(path["points"])
                instrument=eng.identity("gate_spot",symbol,c["listing_ids"].get(symbol) or "listing_unverified")
                result=await db.execute(text("""INSERT INTO pump_opportunity_price_paths
                    (user_id,instrument_id,symbol,bucket_start,content_hash,complete,payload)
                    VALUES(:u,CAST(:i AS uuid),:s,:b,:h,:complete,CAST(:p AS jsonb)) ON CONFLICT DO NOTHING"""),
                    {"u":user_id,"i":instrument,"s":symbol,"b":datetime.fromtimestamp(bucket["bucket_start_ms"]/1000,timezone.utc),
                     "h":eng.canonical_hash(path),"complete":path["complete"],"p":json.dumps(path,allow_nan=False)})
                price_paths_written+=result.rowcount
        written=0; duplicates=0
        for row in sorted(rows,key=lambda r:r["symbol"])[:c["budget"]["max_assets"]]:
            old=previous.get(row["symbol"])
            if old and eng.utc(old["slot_at"])>=slot:
                duplicates+=1; continue
            p=eng.build_observation(user_id=user_id,row=row,book=(collected.get(row["symbol"]) or {}).get("book"),
                source_meta=source_meta.get(row["symbol"],{}),legacy_config=legacy_config,c=c,decision_at=decision,previous=old)
            p["published_at"]=datetime.now(timezone.utc).isoformat()
            result=await db.execute(text("""INSERT INTO pump_opportunity_observations
                (observation_id,user_id,instrument_id,episode_id,symbol,slot_at,decision_at,contract_hash,payload)
                VALUES(CAST(:id AS uuid),:u,CAST(:i AS uuid),CAST(:e AS uuid),:s,:slot,:d,:h,CAST(:p AS jsonb))
                ON CONFLICT(user_id,instrument_id,slot_at) DO NOTHING"""),
                {"id":p["observation_id"],"u":user_id,"i":p["instrument_id"],"e":p["episode_id"],"s":p["symbol"],
                 "slot":slot,"d":decision,"h":eng.canonical_hash(c),"p":json.dumps(p,allow_nan=False)})
            written+=result.rowcount
        return {"written":written,"duplicates":duplicates,"price_paths_written":price_paths_written,"slot_at":slot.isoformat(),
                "sampled_assets":min(len(rows),c["budget"]["max_assets"]),"excluded_by_budget":max(0,len(rows)-c["budget"]["max_assets"])}
    result=await asyncio.wait_for(run_db_task(_write,celery=True),c["budget"]["write_timeout_seconds"])
    logger.info("[PUMP-OPPORTUNITY] user=%s result=%s delta=0 connected=false",user_id,result)
    return result


async def label_batch(user_id):
    from ..database import run_db_task
    c=await run_db_task(lambda db:get_config(db,user_id),celery=True)
    if not c["labels_enabled"]: return {"status":"disabled"}
    now=datetime.now(timezone.utc)
    async def _label(db):
        await db.execute(text("SET LOCAL statement_timeout = '3000ms'"))
        if await storage_bytes(db)>=c["budget"]["max_storage_bytes"]:
            return {"status":"storage_budget_exhausted"}
        due=(await db.execute(text("""SELECT o.payload,h.horizon FROM pump_opportunity_observations o
            CROSS JOIN LATERAL jsonb_array_elements_text(o.payload->'label_spec'->'horizons_minutes') h(horizon)
            LEFT JOIN pump_opportunity_labels l ON l.observation_id=o.observation_id
              AND l.horizon_minutes=h.horizon::integer AND l.label_spec_hash=o.payload->'manifest'->>'label_spec_hash'
            WHERE o.user_id=:u AND l.observation_id IS NULL
              AND o.decision_at + make_interval(mins=>h.horizon::integer)
                  + make_interval(secs=>(o.payload->'label_spec'->>'settle_seconds')::double precision)<=:now
            ORDER BY (o.payload->'manifest'->>'label_spec_hash'=:current_spec) DESC,
                (h.horizon::integer=5) DESC,o.decision_at,h.horizon::integer LIMIT :batch"""),
            {"u":user_id,"now":now,"current_spec":eng.canonical_hash(c["labels"]),"batch":c["budget"]["batch_labels"]})).mappings().all()
        written=0
        for item in due:
            p=item["payload"]; h=int(item["horizon"]); start=eng.utc(p["decision_at"])
            if p["label_spec"]["resolution"]=="trades_exact_window_v1":
                paths=(await db.execute(text("""SELECT DISTINCT ON(bucket_start) payload FROM pump_opportunity_price_paths
                    WHERE user_id=:u AND instrument_id=CAST(:i AS uuid) AND bucket_start>=:start AND bucket_start<=:end
                    ORDER BY bucket_start,complete DESC,captured_at DESC"""),{"u":user_id,"i":p["instrument_id"],
                    "start":start.replace(second=0,microsecond=0),"end":start+timedelta(minutes=h)})).scalars().all()
                label=eng.exact_gross_label(p,list(paths),h,now)
            else:
                bars=(await db.execute(text("""SELECT bucket_start,high_price::double precision,low_price::double precision,
                    close_price::double precision,partial FROM flow_buckets_1m
                    WHERE symbol=:s AND bucket_start>=:start AND bucket_start<=:end ORDER BY bucket_start"""),
                    {"s":p["symbol"],"start":start.replace(second=0,microsecond=0),"end":start+timedelta(minutes=h)})).mappings().all()
                label=eng.gross_label(p,[dict(b) for b in bars],h,now)
            await db.execute(text("""INSERT INTO pump_opportunity_labels(observation_id,label_spec_hash,horizon_minutes,payload)
                VALUES(CAST(:id AS uuid),:hash,:h,CAST(:p AS jsonb)) ON CONFLICT DO NOTHING"""),
                {"id":p["observation_id"],"hash":label["label_spec_hash"],"h":h,"p":json.dumps(label,allow_nan=False)})
            written+=1
        return {"written":written,"batch_limit":c["budget"]["batch_labels"]}
    return await asyncio.wait_for(run_db_task(_label,celery=True),c["budget"]["label_timeout_seconds"])


async def observation(db,user_id,observation_id):
    row=(await db.execute(text("""SELECT payload,published_at FROM pump_opportunity_observations
        WHERE user_id=:u AND observation_id=:id"""),{"u":user_id,"id":observation_id})).mappings().first()
    if not row: return None
    p=dict(row["payload"]);p["published_at"]=row["published_at"].isoformat()
    labels=(await db.execute(text("""SELECT payload FROM pump_opportunity_labels WHERE observation_id=:id
        AND label_spec_hash=:hash ORDER BY horizon_minutes"""),{"id":observation_id,"hash":p["manifest"]["label_spec_hash"]})).scalars().all()
    events=(await db.execute(text("""SELECT observation_id,decision_at,payload->>'state' AS state,
        payload->'reference' AS reference,payload->'score_final' AS score,payload->'vetos' AS vetos
        FROM pump_opportunity_observations WHERE user_id=:u AND episode_id=CAST(:e AS uuid)
        ORDER BY decision_at LIMIT 500"""),{"u":user_id,"e":p["episode_id"]})).mappings().all()
    return {"contract_version":eng.CONTRACT_VERSION,"observation":p,"labels":list(labels),"timeline":[dict(e) for e in events]}


async def history(db,user_id,cursor=None,limit=50):
    rows=(await db.execute(text("""SELECT observation_id,decision_at,payload FROM pump_opportunity_observations
        WHERE user_id=:u AND (CAST(:cursor AS text) IS NULL OR observation_id::text<CAST(:cursor AS text))
        ORDER BY observation_id::text DESC LIMIT :limit"""),{"u":user_id,"cursor":cursor,"limit":limit+1})).mappings().all()
    return {"contract_version":eng.CONTRACT_VERSION,"rows":[r["payload"] for r in rows[:limit]],
            "next_cursor":str(rows[limit-1]["observation_id"]) if len(rows)>limit else None}


async def intelligence(db,user_id,conditions=None):
    # Bounded recent sample, all candidates rather than score-selected winners.
    rows=(await db.execute(text("""SELECT o.payload,l.payload AS label FROM pump_opportunity_observations o
        LEFT JOIN pump_opportunity_labels l ON l.observation_id=o.observation_id
          AND l.horizon_minutes=5 AND l.label_spec_hash=o.payload->'manifest'->>'label_spec_hash'
        WHERE o.user_id=:u ORDER BY o.decision_at DESC LIMIT 5000"""),{"u":user_id})).mappings().all()
    def describe(selected):
        known=[r for r in selected if r["label"] and r["label"].get("targets",{}).get("0.8",{}).get("hit") is not None]
        hits=sum(r["label"]["targets"]["0.8"]["hit"] is True for r in known)
        rate=hits/len(known) if known else None
        return {"observations":len(selected),"episodes":len({r["payload"]["episode_id"] for r in selected}),
                "instruments":len({r["payload"]["instrument_id"] for r in selected}),
                "days":len({r["payload"]["decision_at"][:10] for r in selected}),"known":len(known),
                "unknown_or_pending":len(selected)-len(known),"hits":hits,"descriptive_hit_rate":rate,
                "from":min((r["payload"]["decision_at"] for r in selected),default=None),
                "to":max((r["payload"]["decision_at"] for r in selected),default=None),
                "confidence_interval":None,"uncertainty_reason":"clustered_validation_not_available",
                "out_of_sample":False,"probability_validated":False}
    baseline=describe(rows);groups={}
    for r in rows:
        pattern=" + ".join(sorted(e["group"] for e in r["payload"]["ledger"] if e["result"] is True)) or "sem confirmação"
        groups.setdefault(pattern,[]).append(r)
    patterns=[{"pattern":name,**describe(sample)} for name,sample in sorted(groups.items())]
    exploration=describe([r for r in rows if all(eng.condition(r["payload"]["values"],c) is True for c in conditions)]) if conditions else None
    experiments=(await db.execute(text("SELECT experiment_id,created_at,status,manifest,metrics FROM pump_ml_experiments WHERE user_id=:u ORDER BY created_at DESC LIMIT 20"),{"u":user_id})).mappings().all()
    return {"contract_version":eng.CONTRACT_VERSION,"as_of":datetime.now(timezone.utc).isoformat(),
            "model":{"status":"coletando","algorithm":"XGBoost Pump","delta":0,"probability":None,"auto_promotion":False,
                     "reason":"insufficient_validated_point_in_time_data","shadow_isolation":True},
            "baseline":baseline,"patterns":patterns,"exploration":exploration,"experiments":[dict(e) for e in experiments],
            "sample_limit":5000,"sample_policy":"most_recent_all_candidates","target":{"gross_pct":0.8,"horizon_minutes":5}}
