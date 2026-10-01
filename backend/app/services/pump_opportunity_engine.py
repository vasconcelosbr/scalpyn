"""Pump-only point-in-time contracts. No execution or Shadow dependencies."""
from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from uuid import NAMESPACE_URL, uuid5

CONTRACT_VERSION = "pump_opportunity_v2"
DEFAULT_CONFIG = {
    "enabled": False, "ui_enabled": False, "version": "pump_groups_v1",
    "score_unit": "confirmation_points", "simulation_threshold": 20,
    "reference_policy": "gate_best_ask_v1", "max_quote_age_seconds": 60,
    "max_feature_age_seconds": 300, "freshness_seconds": 120,
    "episode_gap_seconds": 180, "visual_exit_cycles": 2,
    "listing_ids": {}, "ml_delta_enabled": False, "pool_connection_enabled": False,
    "price_paths_enabled": False,
    "labels_enabled": False, "inference_enabled": False, "training_enabled": False,
    "budget": {"max_assets": 100, "write_timeout_seconds": 4, "batch_labels": 10,
               "label_timeout_seconds": 4, "retention_days": 30, "max_storage_bytes": 1000000000,
               "max_price_points_per_cycle": 10000, "max_price_points_per_minute": 3000},
    "labels": {"version": "pump_gross_touch_v2", "horizons_minutes": [5,10,15,30,60,120],
               "targets_pct": [0.6,0.8], "downside_pct": -2, "settle_seconds": 3,
               "resolution": "closed_1m_conservative", "cost_policy": None},
    "groups": {
        "flow": {"points": 10, "conditions": [{"field":"delta_norm","op":"gt","value":0},
                   {"field":"buy_persistence","op":"gte","value":0.5},
                   {"field":"cvd_slope","op":"gt","value":0},
                   {"field":"rvol_strict","op":"gt","value":1}]},
        "structure": {"points":10,"conditions":[{"field":"price_progress_atr","op":"gt","value":0}]},
        "acceptance": {"points":10,"conditions":[{"field":"breakout_hold_ratio","op":"gte","value":0.5}]},
    },
    "risks": {"max_spread_pct": None, "max_slippage_pct": None, "max_extension_atr": None},
    "status": "PROVISIONAL_NOT_CALIBRATED",
}


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()


def merge(base, override):
    out = deepcopy(base)
    for key,value in (override or {}).items():
        out[key] = merge(out[key],value) if isinstance(value,dict) and isinstance(out.get(key),dict) else deepcopy(value)
    return out


def config(stored=None):
    result = merge(DEFAULT_CONFIG,stored)
    validate_config(result)
    return result


def number(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)


def validate_config(c):
    for key in ("enabled","ui_enabled","labels_enabled","price_paths_enabled","ml_delta_enabled","pool_connection_enabled","training_enabled","inference_enabled"):
        if not isinstance(c[key],bool):raise ValueError(f"Boolean flag required: {key}")
    if c.get("score_unit") != "confirmation_points":
        raise ValueError("SCORE_UNIT_MISMATCH: use confirmation_points; legacy 0-100 is incompatible")
    if c.get("ml_delta_enabled") or c.get("pool_connection_enabled"):
        raise ValueError("Pump delta and Pool connection require a separately approved release")
    if c.get("training_enabled") or c.get("inference_enabled"):
        raise ValueError("Pump ML activation requires resource budget and model validation approval")
    if c.get("reference_policy") != "gate_best_ask_v1":
        raise ValueError("Unsupported reference policy")
    for key in ("max_quote_age_seconds","max_feature_age_seconds","freshness_seconds","episode_gap_seconds","visual_exit_cycles"):
        if not number(c[key]) or c[key] <= 0:
            raise ValueError(f"Invalid positive configuration: {key}")
    for key in ("max_assets","batch_labels","retention_days","max_storage_bytes","max_price_points_per_cycle","max_price_points_per_minute"):
        if not isinstance(c["budget"][key],int) or c["budget"][key] <= 0:
            raise ValueError(f"Invalid budget: {key}")
    for key in ("write_timeout_seconds","label_timeout_seconds"):
        if not number(c["budget"][key]) or c["budget"][key] <= 0:
            raise ValueError(f"Invalid budget: {key}")
    if not c["groups"] or len(c["groups"]) > 20:
        raise ValueError("Expected bounded confirmation groups")
    for group,g in c["groups"].items():
        if not number(g["points"]) or g["points"] < 0 or not g["conditions"] or len(g["conditions"])>20:
            raise ValueError(f"Invalid group: {group}")
        for rule in g["conditions"]:
            if rule["op"] not in ("gt","gte","lt","lte","eq","between"):
                raise ValueError("Unsupported rule operator")
            if not rule.get("field") or not ("value" in rule or "other_field" in rule):
                raise ValueError("Rule requires field and value or other_field")
    flow_fields={"delta_norm","window_delta_norm","buy_persistence","cvd_slope","cvd_60m","rvol_strict","taker_ratio","volume_delta","flow_change","volume_acceleration","volume_spike"}
    flow_groups=[name for name,g in c["groups"].items() if any(r["field"] in flow_fields or r.get("other_field") in flow_fields for r in g["conditions"])]
    if len(flow_groups)>1:
        raise ValueError("Correlated flow inputs must share a single capped confirmation group")
    maximum=sum(g["points"] for g in c["groups"].values())
    if not number(c["simulation_threshold"]) or not 0 <= c["simulation_threshold"] <= maximum:
        raise ValueError("Threshold outside declared confirmation point range")
    for value in c["risks"].values():
        if value is not None and (not number(value) or value <= 0):
            raise ValueError("Risk limits must be positive or null (unconfigured)")
    if c["labels"]["resolution"] not in ("closed_1m_conservative","trades_exact_window_v1"):
        raise ValueError("Unsupported label resolution")
    if c["labels"]["resolution"]=="trades_exact_window_v1":
        if c["labels"].get("future_price_policy")!="last_trade_asof_endpoint_v1" or not number(c["labels"].get("endpoint_max_age_seconds")) or c["labels"]["endpoint_max_age_seconds"]<=0:
            raise ValueError("Exact label future-price policy and endpoint age required")
    if not c["labels"]["horizons_minutes"] or any(not isinstance(h,int) or not 0<h<=120 for h in c["labels"]["horizons_minutes"]):
        raise ValueError("Horizons must be integer minutes up to 120")
    if any(not number(t) or t<=0 for t in c["labels"]["targets_pct"]):
        raise ValueError("Targets must be positive")
    if not number(c["labels"]["downside_pct"]) or c["labels"]["downside_pct"] >= 0:
        raise ValueError("Downside must be negative")


def utc(value):
    if isinstance(value,datetime):
        result=value
    else:
        result=datetime.fromisoformat(str(value).replace("Z","+00:00"))
    if result.tzinfo is None:
        raise ValueError("UTC timezone required")
    return result.astimezone(timezone.utc)


def identity(*parts):
    return str(uuid5(NAMESPACE_URL,":".join(map(str,parts))))


def condition(values,rule):
    left=values.get(rule["field"])
    right=values.get(rule["other_field"]) if "other_field" in rule else rule.get("value")
    if left is None or right is None:
        return None
    if isinstance(left,float) and not math.isfinite(left):
        return None
    if rule["op"] == "between":
        lo,hi=right
        return (left>=lo if rule.get("include_lower",True) else left>lo) and (left<=hi if rule.get("include_upper",True) else left<hi)
    return {"gt":lambda:left>right,"gte":lambda:left>=right,"lt":lambda:left<right,
            "lte":lambda:left<=right,"eq":lambda:left==right}[rule["op"]]()


def evidence(values,c,reasons=None):
    reasons=reasons or {}
    ledger=[]
    for name,group in c["groups"].items():
        results=[False if values.get(r["field"]) is None and reasons.get(r["field"])=="no_active_breakout" else condition(values,r) for r in group["conditions"]]
        outcome=None if None in results else all(results)
        ledger.append({"rule_id":name,"group":name,"operator":"AND","conditions":group["conditions"],
                       "inputs":{k:values.get(k) for r in group["conditions"] for k in (r["field"],r.get("other_field")) if k},
                       "result":outcome,"points":group["points"] if outcome is True else 0,
                       "reason":"missing_input" if outcome is None else "confirmed" if outcome else "not_confirmed"})
    return ledger


def build_observation(*,user_id,row,book,source_meta,legacy_config,c,decision_at,previous=None):
    decision=utc(decision_at)
    slot=decision.replace(second=0,microsecond=0)
    symbol=row["symbol"]
    listing=c["listing_ids"].get(symbol)
    listing_id=listing or "listing_unverified"
    instrument=identity("gate_spot",symbol,listing_id)
    observation_id=identity(CONTRACT_VERSION,user_id,instrument,slot.isoformat())
    cells=dict(row.get("indicators",{}))
    for key,value in row.get("opportunity_source_values",{}).items():
        meta=(source_meta or {}).get(key) or {}
        cells[key]={"value":value,"source":meta.get("source"),"age_seconds":meta.get("age_seconds")}
    values={}; availability={}; reasons={}
    for key,cell in cells.items():
        meta=(source_meta or {}).get(key) or {}
        available=meta.get("available_at")
        age=cell.get("age_seconds",meta.get("age_seconds"))
        reason=cell.get("reason")
        if meta.get("stale") or (number(age) and age>c["max_feature_age_seconds"]):
            reason="stale"
        if available:
            try:
                if utc(available)>decision: reason="not_available_at_decision"
            except (TypeError,ValueError): reason="invalid_available_at"
        # Retrieval is observed now; unknown producer timing stays explicit.
        availability[key]={"captured_at":decision.isoformat(),"producer_available_at":available,
                           "source_timestamp":meta.get("source_timestamp"),"age_seconds":age,
                           "source":cell.get("source"),"reason":reason}
        value=cell.get("value")
        values[key]=None if reason or (isinstance(value,float) and not math.isfinite(value)) else value
        if values[key] is None: reasons[key]=reason or "not_observed"
    quote_reason=None; price=None; quote_at=None
    try:
        quote_at=utc(book["observed_at"])
        bids,asks=book["bids"],book["asks"]
        price=float(asks[0][0]); bid=float(bids[0][0])
        age=(decision-quote_at).total_seconds()
        if not math.isfinite(price) or not math.isfinite(bid) or bid<=0 or price<=0 or bid>price:
            quote_reason="invalid_or_crossed_book"
        elif age<0 or age>c["max_quote_age_seconds"]: quote_reason="quote_stale_or_future"
    except (KeyError,TypeError,ValueError,IndexError):
        quote_reason="quote_unavailable"
    if quote_reason: price=None
    values["price"]=price
    if price is not None:
        values["spread_pct"]=(price-bid)/((price+bid)/2)*100
        availability["spread_pct"]={"captured_at":decision.isoformat(),"producer_available_at":None,
            "source_timestamp":quote_at.isoformat(),"age_seconds":(decision-quote_at).total_seconds(),"source":"gate_book","reason":None}
        reasons.pop("spread_pct",None)
    ledger=evidence(values,c,reasons)
    score=sum(e["points"] for e in ledger)
    vetos=[]
    if quote_reason: vetos.append(quote_reason)
    if row.get("collection_error"): vetos.append("collection_failed")
    # Legacy veto is reported, never used to modify legacy admission.
    legacy_veto=row.get("exhaustion_veto") or {}
    if row.get("exhaustion_flag") or legacy_veto.get("hit") or legacy_veto.get("triggered") or legacy_veto.get("active"):
        vetos.append("legacy_exhaustion_veto")
    for key,limit_key in (("spread_pct","max_spread_pct"),("estimated_slippage_buy_pct","max_slippage_pct"),("price_extension_atr","max_extension_atr")):
        limit=c["risks"][limit_key]
        if limit is not None and (values.get(key) is None or values[key]>limit):
            vetos.append(f"risk:{key}")
    liquidity_ok=values.get("estimated_slippage_buy_pct") is not None and values.get("ask_depth_usdt_1pct") is not None
    if not liquidity_ok: vetos.append("liquidity_unavailable")
    complete=all(e["result"] is not None for e in ledger)
    if not complete: vetos.append("confirmation_data_incomplete")
    eligible=not vetos and score>=c["simulation_threshold"]
    previous=previous or {}
    new_episode=not previous or previous.get("instrument_id")!=instrument or previous.get("state")=="invalidado" or (decision-utc(previous["decision_at"])).total_seconds()>c["episode_gap_seconds"]
    episode=identity("pump_episode",user_id,instrument,slot.isoformat()) if new_episode else previous["episode_id"]
    weak=0 if eligible else int(previous.get("weak_cycles",0))+1
    state="invalidado" if vetos else "ativo" if eligible else "enfraquecendo" if previous.get("state") in ("ativo","enfraquecendo") and weak<c["visual_exit_cycles"] else "formando"
    reference={"price":price,"at":quote_at.isoformat() if quote_at else None,"policy":c["reference_policy"],"reason":quote_reason,
               "levels":{str(t):price*(1+t/100) if price else None for t in c["labels"]["targets_pct"]}}
    manifest={"schema_version":CONTRACT_VERSION,"owner_scope":str(user_id),"feature_spec_hash":canonical_hash(c["groups"]),
              "label_spec_hash":canonical_hash(c["labels"]),"score_config_hash":canonical_hash(c),
              "legacy_config_hash":legacy_config["_meta"]["config_hash"],"cost_policy_hash":canonical_hash(c["labels"]["cost_policy"]),
              "eligibility_policy":legacy_config["universe_filter"],"listing_certified":bool(listing),"role":"opportunity"}
    return {"observation_id":observation_id,"episode_id":episode,"instrument_id":instrument,"listing_id":listing_id,
            "symbol":symbol,"slot_at":slot.isoformat(),"decision_at":decision.isoformat(),"bar_start_at":row.get("last_closed_minute"),
            "bar_close_at":(utc(row["last_closed_minute"])+timedelta(minutes=1)).isoformat() if row.get("last_closed_minute") else None,
            "published_at":None,"reference":reference,"episode_reference":reference if new_episode else previous["episode_reference"],
            "state":state,"weak_cycles":weak,"score_base":score,"score_final":score if complete else None,
            "score_unit":c["score_unit"],"score_version":c["version"],"score_status":c["status"],
            "score_max":sum(g["points"] for g in c["groups"].values()),"confirmed_groups":sum(e["result"] is True for e in ledger),
            "ledger":ledger,"values":values,"availability":availability,"null_reasons":reasons,"vetos":vetos,
            "risks":{k:values.get(k) for k in ("price_extension_atr","upper_wick_ratio","spread_pct","estimated_slippage_buy_pct","ask_depth_usdt_1pct")},
            "simulation":{"eligible":eligible,"threshold":c["simulation_threshold"],"connected":False,"pool_id":None},
            "ml":{"status":"coletando","delta":0,"probability":None,"reason":"no_validated_pump_model"},
            "manifest":manifest,"label_spec":deepcopy(c["labels"]),"contract_version":CONTRACT_VERSION}


def gross_label(observation,bars,horizon,now):
    """Only full admissible minutes. Partial boundary bars never prove a hit."""
    start=utc(observation["decision_at"]); end=start+timedelta(minutes=horizon)
    spec=observation["label_spec"]; price=observation["reference"]["price"]
    base={"horizon_minutes":horizon,"label_spec_hash":canonical_hash(spec),"targets":{},"endpoint_return_pct":None,
          "mfe_pct":None,"mae_pct":None,"order_ambiguous":False,"cost_policy":spec["cost_policy"],"net_return_pct":None,
          "resolution":spec["resolution"],"endpoint_at":end.isoformat(),"status":"pending","reason":None}
    if utc(now)<end+timedelta(seconds=spec["settle_seconds"]): return base
    base["status"]="unknown"
    if not number(price) or price<=0:
        base["reason"]="reference_unavailable"; return base
    by_start={utc(b["bucket_start"]):b for b in bars}
    cursor=start.replace(second=0,microsecond=0)
    if cursor<start: cursor+=timedelta(minutes=1)
    expected=[]
    while cursor+timedelta(minutes=1)<=end:
        expected.append(cursor); cursor+=timedelta(minutes=1)
    admissible=[]; missing=[]
    for ts in expected:
        b=by_start.get(ts)
        if not b or b.get("partial") or not all(number(b.get(k)) and b[k]>0 for k in ("high_price","low_price","close_price")):
            missing.append(ts)
        else: admissible.append((ts,b))
    boundary=start.second!=0 or start.microsecond!=0
    complete=not missing and not boundary and bool(expected)
    base.update(expected_minutes=len(expected),observed_minutes=len(admissible),missing_minutes=len(missing),
                boundary_ambiguous=boundary,coverage_complete=complete)
    highs=[(b["high_price"]/price-1)*100 for _,b in admissible]
    lows=[(b["low_price"]/price-1)*100 for _,b in admissible]
    base["partial_mfe_lower_bound_pct"]=max(highs) if highs else None
    base["partial_mae_upper_bound_pct"]=min(lows) if lows else None
    for target in spec["targets_pct"]:
        hits=[ts for ts,b in admissible if b["high_price"]>=price*(1+target/100)]
        first=hits[0] if hits else None
        censored=bool(first and (boundary or any(ts<first for ts in missing)))
        base["targets"][str(target)]={"hit":True if hits else False if complete else None,
             "first_touch_interval":None if not first or censored else [first.isoformat(),(first+timedelta(minutes=1)).isoformat()],
             "first_touch_censored":censored,"reason":"gap_or_boundary_before_observed_hit" if censored else None}
    down=[ts for ts,b in admissible if b["low_price"]<=price*(1+spec["downside_pct"]/100)]
    base["downside"]={"level_pct":spec["downside_pct"],"hit":True if down else False if complete else None}
    base["order_ambiguous"]=any(b["high_price"]>=price*(1+max(spec["targets_pct"])/100) and b["low_price"]<=price*(1+spec["downside_pct"]/100) for _,b in admissible)
    base["conservative_stop_first"]=True if base["order_ambiguous"] else None
    endpoint=by_start.get(end-timedelta(minutes=1)) if not boundary else None
    if endpoint and not endpoint.get("partial") and number(endpoint.get("close_price")):
        base["endpoint_return_pct"]=(endpoint["close_price"]/price-1)*100
    if complete:
        base.update(status="known",mfe_pct=max(highs),mae_pct=min(lows))
    else:
        base["reason"]="minute_boundary_ambiguous" if boundary else "price_gap"
    return base


def purged_split(rows,boundary,embargo_seconds,max_horizon_minutes=120):
    """Episodes that cross the cut are excluded from both sets."""
    cut=utc(boundary); train=[]; test=[]; crossed=set()
    for r in rows:
        t=utc(r["decision_at"])
        if t<cut and t+timedelta(minutes=max_horizon_minutes)>=cut:
            crossed.add(r["episode_id"])
    earlier={r["episode_id"] for r in rows if utc(r["decision_at"])<cut}
    later={r["episode_id"] for r in rows if utc(r["decision_at"])>=cut}
    crossed |= earlier & later
    for r in rows:
        if r["episode_id"] in crossed: continue
        t=utc(r["decision_at"])
        if t+timedelta(minutes=max_horizon_minutes)<cut: train.append(r)
        elif t>=cut+timedelta(seconds=embargo_seconds): test.append(r)
    return train,test


def price_path(raw,bucket,limit):
    """Archive every price timestamp in a closed minute, or mark truncation unknown."""
    start=int(bucket["bucket_start_ms"]);end=start+60000
    points=[];seen=set();invalid=0
    for t in sorted(raw.get("trades",[]),key=lambda t:(float(t.get("ts_ms") or 0),str(t.get("trade_id") or ""))):
        try: stamp=float(t["ts_ms"]);price=float(t["price"])
        except (KeyError,TypeError,ValueError):invalid+=1;continue
        if not math.isfinite(stamp):invalid+=1;continue
        if not start<=stamp<end:continue
        if not math.isfinite(price) or price<=0:invalid+=1;continue
        key=str(t.get("trade_id")) if t.get("trade_id") is not None else f"{stamp}:{price}:{t.get('side')}:{t.get('amount')}"
        if key in seen:continue
        seen.add(key);points.append([stamp,price,key])
    reason=bucket.get("gap_reason")
    if bucket.get("partial"):reason=reason or "partial_source"
    if str(raw.get("source","")).startswith("gate_trades_ws") and raw.get("alive_slots") is None:
        reason="ws_liveness_unavailable"
    if invalid:reason="invalid_raw_trade"
    if len(points)>limit:reason="price_point_budget_exceeded"
    # Invalid/truncated revisions remain preserved and never certify absence.
    return {"bucket_start_ms":start,"bucket_end_ms":end,"points":points[:limit],
            "reported_points":len(points),"complete":reason is None,"reason":reason,
            "source":raw.get("source"),"covered_from_ms":raw.get("covered_from_ms"),
            "gap_windows":list(raw.get("gap_windows") or []),"capture_contract":"pump_raw_price_path_v1"}


def exact_gross_label(observation,paths,horizon,now):
    start=utc(observation["decision_at"]);end=start+timedelta(minutes=horizon)
    spec=observation["label_spec"];price=observation["reference"]["price"]
    result={"horizon_minutes":horizon,"label_spec_hash":canonical_hash(spec),"targets":{},"status":"pending",
            "reason":None,"resolution":spec["resolution"],"future_price_policy":spec["future_price_policy"],
            "endpoint_at":end.isoformat(),"endpoint_return_pct":None,"net_return_pct":None,
            "cost_policy":spec["cost_policy"],"mfe_pct":None,"mae_pct":None,"order_ambiguous":False}
    if utc(now)<end+timedelta(seconds=spec["settle_seconds"]):return result
    result["status"]="unknown"
    if not number(price) or price<=0:result["reason"]="reference_unavailable";return result
    start_ms=start.timestamp()*1000;end_ms=end.timestamp()*1000
    expected=list(range(int(start_ms//60000)*60000,int(end_ms//60000)*60000+1,60000))
    by_minute={int(p["bucket_start_ms"]):p for p in paths}
    gaps=[ts for ts in expected if ts not in by_minute or not by_minute[ts]["complete"]]
    points=sorted((p for path in paths for p in path["points"] if start_ms<=p[0]<=end_ms),key=lambda p:(p[0],p[2]))
    complete=not gaps
    result.update(coverage_complete=complete,expected_minutes=len(expected),observed_minutes=len(expected)-len(gaps),missing_minutes=len(gaps),boundary_ambiguous=False)
    for target in spec["targets_pct"]:
        hits=[p for p in points if p[1]>=price*(1+target/100)]
        first=hits[0] if hits else None
        censored=bool(first and any(gap<first[0] for gap in gaps))
        stamp=datetime.fromtimestamp(first[0]/1000,timezone.utc).isoformat() if first else None
        result["targets"][str(target)]={"hit":True if first else False if complete else None,
            "first_touch_at":stamp if first and not censored else None,
            "first_touch_interval":None if not first or censored else [stamp,stamp],
            "time_to_touch_seconds":(first[0]-start_ms)/1000 if first and not censored else None,
            "first_touch_censored":censored,"reason":"gap_before_observed_hit" if censored else None}
    endpoint_candidates=[p for path in paths for p in path["points"] if p[0]<=end_ms]
    endpoint=max(endpoint_candidates,key=lambda p:(p[0],p[2])) if endpoint_candidates else None
    endpoint_age=(end_ms-endpoint[0])/1000 if endpoint else None
    endpoint_gap=any(ts+60000> (endpoint[0] if endpoint else start_ms) for ts in gaps)
    if endpoint and endpoint_age<=spec["endpoint_max_age_seconds"] and not endpoint_gap:
        result["endpoint_return_pct"]=(endpoint[1]/price-1)*100
        result["endpoint_price_at"]=datetime.fromtimestamp(endpoint[0]/1000,timezone.utc).isoformat()
        result["endpoint_age_seconds"]=endpoint_age
        if spec["cost_policy"]:
            result["net_return_pct"]=result["endpoint_return_pct"]-spec["cost_policy"]["roundtrip_pct"]
    changes=[(p[1]/price-1)*100 for p in points]
    result["partial_mfe_lower_bound_pct"]=max(changes) if changes else None
    result["partial_mae_upper_bound_pct"]=min(changes) if changes else None
    down=next((p for p in points if p[1]<=price*(1+spec["downside_pct"]/100)),None)
    result["downside"]={"level_pct":spec["downside_pct"],"hit":True if down else False if complete else None}
    if down:
        result["order_ambiguous"]=any(p[0]==down[0] and p[1]>=price*(1+max(spec["targets_pct"])/100) for p in points)
    result["conservative_stop_first"]=True if result["order_ambiguous"] else None
    if complete and points:
        result.update(status="known",mfe_pct=max(changes),mae_pct=min(changes))
    else:result["reason"]="price_gap" if gaps else "no_trades_observed"
    return result
