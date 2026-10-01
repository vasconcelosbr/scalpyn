"""Bounded Pump-only persistence/read API; legacy Pool/Shadow/exit stay untouched."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime,timedelta,timezone
from uuid import uuid4
from sqlalchemy import text
from . import pump_opportunity_engine as eng

logger=logging.getLogger(__name__)
CONFIG_TYPE="pump_opportunity"


async def label_health(db,user_id):
    counts=(await db.execute(text("""SELECT count(*) FILTER(WHERE ready_at<=now() AND resource_block IS NULL) AS due,
        count(*) FILTER(WHERE ready_at>now() AND resource_block IS NULL) AS waiting,
        count(*) FILTER(WHERE resource_block IS NOT NULL) AS resource_blocked,
        EXTRACT(epoch FROM now()-min(ready_at) FILTER(WHERE ready_at<=now() AND resource_block IS NULL)) AS oldest_due_seconds
        FROM pump_opportunity_label_queue WHERE user_id=:u AND completed_at IS NULL"""),{"u":user_id})).mappings().one()
    last=(await db.execute(text("SELECT payload FROM pump_opportunity_job_runs WHERE user_id=:u ORDER BY finished_at DESC LIMIT 1"),{"u":user_id})).scalar()
    return {**dict(counts),"oldest_due_seconds":float(counts["oldest_due_seconds"] or 0),"last_batch":last}


async def storage_bytes(db):
    return int((await db.execute(text("""SELECT pg_total_relation_size('pump_opportunity_observations')
        +pg_total_relation_size('pump_opportunity_labels')+pg_total_relation_size('pump_ml_experiments')
        +pg_total_relation_size('pump_ml_predictions')
        +COALESCE(pg_total_relation_size(to_regclass('pump_opportunity_price_paths')),0)
        +COALESCE(pg_total_relation_size(to_regclass('pump_opportunity_label_queue')),0)
        +COALESCE(pg_total_relation_size(to_regclass('pump_opportunity_job_runs')),0)
        +COALESCE(pg_total_relation_size(to_regclass('pump_ml_job_runs')),0)
        +COALESCE(pg_total_relation_size(to_regclass('pump_ml_artifacts')),0)"""))).scalar())


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
    merged=eng.merge(await get_config(db,user_id),requested)
    for field in ("listing_ids","listing_records"):
        if field in requested:merged[field]=requested[field]  # authoritative maps, removals must not retain stale evidence
    c=eng.config(merged)
    await config_service.update_config(db,CONFIG_TYPE,user_id,c,changed_by=user_id,
        change_description=f"Pump-only observation contract {eng.canonical_hash(c)}; delta=0; disconnected")
    return c


async def refresh_listing_contracts():
    """One bounded public metadata read; Pump-only ConfigService writes."""
    from ..database import run_db_task
    from ..exchange_adapters.gate_adapter import GateAdapter
    from .pump_contracts import gate_listing_record
    async def owners(db):
        return list((await db.execute(text("""SELECT DISTINCT user_id FROM config_profiles WHERE config_type=:type
            AND pool_id IS NULL AND is_active IS NOT FALSE AND config_json->>'enabled'='true' ORDER BY user_id LIMIT 10"""),{"type":CONFIG_TYPE})).scalars().all())
    users=await run_db_task(owners,celery=True)
    if not users:return {"status":"disabled","owners":0}
    pairs=await asyncio.wait_for(GateAdapter._public_get(f"{GateAdapter.SPOT_BASE}/spot/currency_pairs"),5)
    if not isinstance(pairs,list) or len(pairs)>10000:raise ValueError("Pump listing metadata budget/schema exceeded")
    pairs={p["id"]:p for p in pairs};captured=datetime.now(timezone.utc);results=[]
    for user_id in users:
        async def apply(db):
            c=await get_config(db,user_id)
            current=(await db.execute(text("""SELECT DISTINCT symbol FROM pump_opportunity_observations
                WHERE user_id=:u AND decision_at>=:since ORDER BY symbol LIMIT :max"""),
                {"u":user_id,"since":captured-timedelta(seconds=c["freshness_seconds"]*2),"max":c["budget"]["max_assets"]})).scalars().all()
            if not current:return {"owner":str(user_id),"status":"no_current_universe"}
            records={s:gate_listing_record(pairs.get(s,{}),captured) for s in current}
            records={s:r for s,r in records.items() if r["certified"]}
            await put_config(db,user_id,{"listing_records":records,"listing_ids":{s:r["listing_id"] for s,r in records.items()}})
            return {"owner":str(user_id),"certified":len(records),"unverified":len(current)-len(records)}
        results.append(await asyncio.wait_for(run_db_task(apply,celery=True),4))
    return {"status":"ok","source_captured_at":captured.isoformat(),"results":results}


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
            "label_health":await label_health(db,user_id),
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
        previous={p["symbol"]:p for p in sorted(previous,key=lambda p:p["decision_at"])}
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
        # Immutable stored specification schedules each horizon exactly once.
        await db.execute(text("""INSERT INTO pump_opportunity_label_queue
            (observation_id,user_id,label_spec_hash,horizon_minutes,ready_at)
            SELECT o.observation_id,o.user_id,o.payload->'manifest'->>'label_spec_hash',h.value::integer,
                o.decision_at+make_interval(mins=>h.value::integer)
                +make_interval(secs=>(o.payload->'label_spec'->>'settle_seconds')::double precision)
            FROM pump_opportunity_observations o
            CROSS JOIN LATERAL jsonb_array_elements_text(o.payload->'label_spec'->'horizons_minutes') h(value)
            WHERE o.user_id=:u AND o.slot_at=:slot ON CONFLICT DO NOTHING"""),{"u":user_id,"slot":slot})
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
        locked=(await db.execute(text("SELECT pg_try_advisory_xact_lock(hashtextextended('pump_labels:'||CAST(:u AS text),0))"),{"u":str(user_id)})).scalar()
        if not locked:return {"status":"busy","written":0}
        due=list((await db.execute(text("""SELECT o.payload,q.horizon_minutes AS horizon FROM pump_opportunity_label_queue q
            JOIN pump_opportunity_observations o ON o.observation_id=q.observation_id AND o.user_id=q.user_id
            WHERE q.user_id=:u AND q.completed_at IS NULL AND q.resource_block IS NULL AND q.ready_at<=:now
            ORDER BY q.ready_at,q.observation_id,q.horizon_minutes LIMIT :batch"""),
            {"u":user_id,"now":now,"batch":c["budget"]["batch_labels"]})).mappings().all())
        # Deduplicate overlapping instrument windows; bounded payloads are shared
        # by every observation/horizon in this batch, never queried per label.
        requests=[]
        for ordinal,item in enumerate(due,1):
            p=item["payload"];start=eng.utc(p["decision_at"])
            requests.append({"n":ordinal,"i":p["instrument_id"],"s":p["symbol"],"a":start.replace(second=0,microsecond=0).isoformat(),
                "b":(start+timedelta(minutes=int(item["horizon"]))).isoformat(),"exact":p["label_spec"]["resolution"]=="trades_exact_window_v1"})
        params={"u":user_id,"requests":json.dumps(requests),"count":len(requests),"points":c["budget"]["max_label_read_points"],"bytes":c["budget"]["max_label_read_bytes"]}
        # Each revision matches the same request set by instrument/minute. The
        # last ordering key identifies its earliest consumer without expanding
        # payloads into a second window query. Fit an entire FIFO prefix before
        # returning any points; unfinished horizons retain their exact identity.
        paths=(await db.execute(text("""WITH requests AS (
            SELECT * FROM jsonb_to_recordset(CAST(:requests AS jsonb)) AS r(n integer,i uuid,s text,a timestamptz,b timestamptz,exact boolean)),
            selected AS MATERIALIZED (SELECT DISTINCT ON(p.instrument_id,p.bucket_start) p.instrument_id,p.payload,r.n AS first_request
                FROM pump_opportunity_price_paths p JOIN requests r ON r.exact AND p.instrument_id=r.i
                    AND p.bucket_start>=r.a AND p.bucket_start<=r.b
                WHERE p.user_id=:u ORDER BY p.instrument_id,p.bucket_start,p.complete DESC,p.captured_at DESC,r.n),
            costs AS (SELECT first_request,sum(jsonb_array_length(payload->'points')) AS points,
                sum(octet_length(payload::text)) AS bytes FROM selected GROUP BY first_request),
            cumulative AS (SELECT first_request,sum(points) OVER(ORDER BY first_request) AS points,
                sum(bytes) OVER(ORDER BY first_request) AS bytes FROM costs),
            prefix AS (SELECT COALESCE(min(first_request) FILTER(WHERE points>:points OR bytes>:bytes),:count+1)-1 AS accepted_count FROM cumulative),
            budget AS (SELECT p.accepted_count,
                COALESCE(sum(jsonb_array_length(s.payload->'points')) FILTER(WHERE s.first_request<=p.accepted_count),0) AS points,
                COALESCE(sum(octet_length(s.payload::text)) FILTER(WHERE s.first_request<=p.accepted_count),0) AS bytes,
                COALESCE(sum(jsonb_array_length(s.payload->'points')) FILTER(WHERE s.first_request=1),0) AS head_points,
                COALESCE(sum(octet_length(s.payload::text)) FILTER(WHERE s.first_request=1),0) AS head_bytes,
                COALESCE(sum(jsonb_array_length(s.payload->'points')),0) AS requested_points,
                COALESCE(sum(octet_length(s.payload::text)),0) AS requested_bytes
                FROM prefix p LEFT JOIN selected s ON true GROUP BY p.accepted_count)
            SELECT NULL::uuid AS instrument_id,NULL::jsonb AS payload,b.points,b.bytes,b.accepted_count=0 AS exhausted,
                b.accepted_count,b.requested_points,b.requested_bytes,b.head_points,b.head_bytes FROM budget b
            UNION ALL SELECT s.instrument_id,s.payload,b.points,b.bytes,false,b.accepted_count,b.requested_points,b.requested_bytes,b.head_points,b.head_bytes
                FROM selected s CROSS JOIN budget b WHERE s.first_request<=b.accepted_count"""),params)).mappings().all() if requests else []
        if any(r["exhausted"] for r in paths):
            # The first whole window alone exceeds a fixed resource limit.
            # Pause only that identity, not the following FIFO work. No label,
            # completion, gap outcome or loss is invented; raw data is retained.
            first=due[0];p=first["payload"]
            block={"reason":"individual_window_exceeds_read_budget","blocked_at":now.isoformat(),
                "required_points":int(paths[0]["head_points"]),"required_bytes":int(paths[0]["head_bytes"]),
                "point_limit":c["budget"]["max_label_read_points"],"byte_limit":c["budget"]["max_label_read_bytes"],
                "retry_policy":"explicit_review_required"}
            await db.execute(text("""UPDATE pump_opportunity_label_queue SET resource_block=CAST(:p AS jsonb)
                WHERE user_id=:u AND observation_id=CAST(:id AS uuid) AND label_spec_hash=:hash
                AND horizon_minutes=:h AND completed_at IS NULL"""),
                {"u":user_id,"id":p["observation_id"],"hash":p["manifest"]["label_spec_hash"],
                 "h":int(first["horizon"]),"p":json.dumps(block)})
            result={"status":"label_read_budget_exhausted","written":0,"batch_limit":len(due),
                "read_points":0,"read_bytes":0,
                "requested_read_points":int(paths[0]["requested_points"]),"requested_read_bytes":int(paths[0]["requested_bytes"]),
                "reason":block["reason"],"resource_block":block,
                "observation_id":p["observation_id"],"horizon_minutes":int(first["horizon"]),
                "duration_ms":round((datetime.now(timezone.utc)-now).total_seconds()*1000)}
            await db.execute(text("INSERT INTO pump_opportunity_job_runs(run_id,user_id,payload) VALUES(:id,:u,CAST(:p AS jsonb))"),
                {"id":uuid4(),"u":user_id,"p":json.dumps(result)})
            return result
        requested_count=len(due)
        accepted_count=int(paths[0]["accepted_count"]) if paths else 0
        due=due[:accepted_count]
        requests=requests[:accepted_count]
        params["requests"]=json.dumps(requests)
        grouped={}
        for path in paths:
            if path["payload"] is not None:grouped.setdefault(str(path["instrument_id"]),[]).append(path["payload"])
        bars=(await db.execute(text("""WITH requests AS (
            SELECT * FROM jsonb_to_recordset(CAST(:requests AS jsonb)) AS r(i uuid,s text,a timestamptz,b timestamptz,exact boolean))
            SELECT DISTINCT f.symbol,f.bucket_start,f.high_price::double precision,f.low_price::double precision,
                f.close_price::double precision,f.partial FROM flow_buckets_1m f JOIN requests r
                ON NOT r.exact AND f.symbol=r.s AND f.bucket_start>=r.a AND f.bucket_start<=r.b"""),params)).mappings().all() if any(not r["exact"] for r in requests) else []
        grouped_bars={}
        for bar in bars:grouped_bars.setdefault(bar["symbol"],[]).append(dict(bar))
        records=[]
        for item in due:
            p=item["payload"]; h=int(item["horizon"]); start=eng.utc(p["decision_at"])
            if p["label_spec"]["resolution"]=="trades_exact_window_v1":
                label=eng.exact_gross_label(p,grouped.get(p["instrument_id"],[]),h,now)
            else:
                label=eng.gross_label(p,grouped_bars.get(p["symbol"],[]),h,now)
            records.append({"id":p["observation_id"],"hash":label["label_spec_hash"],"h":h,"p":label})
        if records:
            # One atomic insert rather than one database round trip per label.
            await db.execute(text("""INSERT INTO pump_opportunity_labels(observation_id,label_spec_hash,horizon_minutes,payload)
                SELECT CAST(r.id AS uuid),r.hash,r.h,r.p FROM jsonb_to_recordset(CAST(:records AS jsonb))
                    AS r(id text,hash text,h integer,p jsonb) ON CONFLICT DO NOTHING"""),
                {"records":json.dumps(records,allow_nan=False)})
            await db.execute(text("""UPDATE pump_opportunity_label_queue q SET completed_at=:now
                FROM jsonb_to_recordset(CAST(:records AS jsonb)) AS r(id uuid,hash text,h integer)
                WHERE q.user_id=:u AND q.observation_id=r.id AND q.label_spec_hash=r.hash AND q.horizon_minutes=r.h"""),
                {"u":user_id,"now":now,"records":json.dumps(records,allow_nan=False)})
        result={"status":"ok","written":len(records),"batch_limit":c["budget"]["batch_labels"],
            "requested":requested_count,"accepted":accepted_count,
            "read_points":int(paths[0]["points"]) if paths else 0,"read_bytes":int(paths[0]["bytes"]) if paths else 0,
            "duration_ms":round((datetime.now(timezone.utc)-now).total_seconds()*1000)}
        await db.execute(text("INSERT INTO pump_opportunity_job_runs(run_id,user_id,payload) VALUES(:id,:u,CAST(:p AS jsonb))"),
            {"id":uuid4(),"u":user_id,"p":json.dumps(result)})
        return result
    started=datetime.now(timezone.utc)
    result=await asyncio.wait_for(run_db_task(_label,celery=True),c["budget"]["label_timeout_seconds"])
    logger.info("[PUMP-LABELS] user=%s result=%s duration_ms=%s",user_id,result,
        round((datetime.now(timezone.utc)-started).total_seconds()*1000))
    return result


async def observation(db,user_id,observation_id):
    row=(await db.execute(text("""SELECT payload,published_at FROM pump_opportunity_observations
        WHERE user_id=:u AND observation_id=:id"""),{"u":user_id,"id":observation_id})).mappings().first()
    if not row: return None
    p=dict(row["payload"]);p["published_at"]=row["published_at"].isoformat()
    labels=(await db.execute(text("""SELECT payload FROM pump_opportunity_labels WHERE observation_id=:id
        AND label_spec_hash=:hash ORDER BY horizon_minutes"""),{"id":observation_id,"hash":p["manifest"]["label_spec_hash"]})).scalars().all()
    blocks=(await db.execute(text("""SELECT horizon_minutes,resource_block FROM pump_opportunity_label_queue
        WHERE user_id=:u AND observation_id=:id AND resource_block IS NOT NULL ORDER BY horizon_minutes"""),
        {"u":user_id,"id":observation_id})).mappings().all()
    events=(await db.execute(text("""SELECT observation_id,decision_at,payload->>'state' AS state,
        payload->'reference' AS reference,payload->'score_final' AS score,payload->'vetos' AS vetos
        FROM pump_opportunity_observations WHERE user_id=:u AND episode_id=CAST(:e AS uuid)
        ORDER BY decision_at LIMIT 500"""),{"u":user_id,"e":p["episode_id"]})).mappings().all()
    return {"contract_version":eng.CONTRACT_VERSION,"observation":p,"labels":list(labels),
            "label_resource_blocks":[dict(b) for b in blocks],"timeline":[dict(e) for e in events]}


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
    baseline=describe(rows);groups={};contracts={}
    for r in rows:
        manifest=r["payload"]["manifest"]
        contract=(manifest["score_config_hash"],manifest["label_spec_hash"],r["payload"]["label_spec"]["version"])
        contracts.setdefault(contract,[]).append(r)
        pattern=" + ".join(sorted(e["group"] for e in r["payload"]["ledger"] if e["result"] is True)) or "sem confirmação"
        groups.setdefault(pattern,[]).append(r)
    patterns=[{"pattern":name,**describe(sample)} for name,sample in sorted(groups.items())]
    exploration=describe([r for r in rows if all(eng.condition(r["payload"]["values"],c) is True for c in conditions)]) if conditions else None
    experiments=(await db.execute(text("SELECT experiment_id,created_at,status,manifest,metrics FROM pump_ml_experiments WHERE user_id=:u ORDER BY created_at DESC LIMIT 20"),{"u":user_id})).mappings().all()
    training_runs=(await db.execute(text("""SELECT run_id,started_at,finished_at,
        CASE WHEN status='running' AND deadline_at<now() THEN 'deadline_exceeded' ELSE status END AS status,payload
        FROM pump_ml_job_runs WHERE user_id=:u ORDER BY started_at DESC LIMIT 20"""),{"u":user_id})).mappings().all()
    return {"contract_version":eng.CONTRACT_VERSION,"as_of":datetime.now(timezone.utc).isoformat(),
            "model":{"status":"coletando","algorithm":"XGBoost Pump","delta":0,"probability":None,"auto_promotion":False,
                     "reason":"insufficient_validated_point_in_time_data","shadow_isolation":True},
            "baseline":baseline,"patterns":patterns,"exploration":exploration,"experiments":[dict(e) for e in experiments],
            "contract_cohorts":[{"score_config_hash":key[0],"label_spec_hash":key[1],"label_version":key[2],
                **describe(sample)} for key,sample in sorted(contracts.items())],
            "training_runs":[dict(r) for r in training_runs],
            "sample_limit":5000,"sample_policy":"most_recent_all_candidates","target":{"gross_pct":0.8,"horizon_minutes":5}}
