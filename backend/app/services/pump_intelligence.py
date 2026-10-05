"""Bounded descriptive summaries. No forecasts, training or trading dependencies."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from statistics import median
import math

from . import pump_opportunity_engine as eng


def metric(values):
    values = [v for v in values if eng.number(v)]
    return {"known": len(values), "mean": sum(values)/len(values) if values else None,
            "median": median(values) if values else None, "min": min(values) if values else None,
            "max": max(values) if values else None}


def contract_key(p):
    m=p["manifest"]
    # Producer/features/cost/reference are compatibility boundaries too.
    return tuple(str(m.get(k) or "unknown") for k in (
        "score_config_hash", "label_spec_hash", "feature_spec_hash", "legacy_config_hash", "cost_policy_hash")) + (
        str(p.get("label_version") or "unknown"), str(p.get("reference_policy") or "unknown"))


def describe(rows, horizon):
    times=[r["payload"]["decision_at"] for r in rows]
    labels=[r.get("label") for r in rows]
    pending=sum(not l or l.get("status")=="pending" for l in labels)
    coverage={"complete":sum(bool(l and l.get("coverage_complete") is True) for l in labels),
              "pending":pending, "incomplete":sum(bool(l and l.get("status")!="pending" and l.get("coverage_complete") is not True) for l in labels),
              "with_gaps":sum(bool(l and (l.get("missing_minutes") or 0)>0) for l in labels),
              "boundary_ambiguous":sum(bool(l and l.get("boundary_ambiguous")) for l in labels),
              "order_ambiguous":sum(bool(l and l.get("order_ambiguous")) for l in labels)}
    targets={}
    for target in ("0.6","0.8"):
        outcomes=[(l or {}).get("targets",{}).get(target,{}) for l in labels]
        hits=sum(t.get("hit") is True for t in outcomes)
        misses=sum(t.get("hit") is False for t in outcomes)
        known=hits+misses
        exact=[];interval_lo=[];interval_hi=[];drops=[];maes=[]
        for row,t in zip(rows,outcomes):
            if t.get("hit") is not True: continue
            if not t.get("first_touch_censored"):
                seconds=t.get("time_to_touch_seconds")
                interval=t.get("first_touch_interval")
                if eng.number(seconds) and 0<=seconds<=horizon*60: exact.append(seconds)
                elif interval and len(interval)==2:
                    start=eng.utc(row["payload"]["decision_at"])
                    lo=(eng.utc(interval[0])-start).total_seconds();hi=(eng.utc(interval[1])-start).total_seconds()
                    # Intervals crossing the reference boundary are not exact times.
                    if 0<=lo<=hi<=horizon*60:
                        if lo==hi:exact.append(lo)
                        else:interval_lo.append(lo);interval_hi.append(hi)
            pre=t.get("pre_touch") or {}
            if pre.get("status")=="known" and not pre.get("order_ambiguous"):
                drops.append(pre.get("drawdown_before_touch_pct"));maes.append(pre.get("mae_before_touch_pct"))
        pre_states=[(t.get("pre_touch") or {}).get("status","not_measured") for t in outcomes]
        targets[target]={"hits":hits,"misses":misses,"known":known,"unknown":len(rows)-known,
            "descriptive_hit_rate":hits/known if known else None,
            "complete_hits":sum(bool(l and l.get("coverage_complete") is True and t.get("hit") is True) for l,t in zip(labels,outcomes)),
            "complete_known":sum(bool(l and l.get("coverage_complete") is True and t.get("hit") is not None) for l,t in zip(labels,outcomes)),
            "first_touch_censored":sum(bool(t.get("first_touch_censored")) for t in outcomes),
            "time_to_touch_seconds":metric(exact),"time_interval_lower_seconds":metric(interval_lo),
            "time_interval_upper_seconds":metric(interval_hi),"time_unknown_among_hits":hits-len(exact)-len(interval_lo),
            "drawdown_before_touch_pct":metric(drops),"mae_before_touch_pct":metric(maes),
            "pre_touch_unknown":pre_states.count("unknown"),"pre_touch_not_reached":pre_states.count("not_reached"),
            "pre_touch_not_measured":pre_states.count("not_measured")}
    support={"observations":len(rows),"episodes":len({r["payload"]["episode_id"] for r in rows}),
        "instruments":len({r["payload"]["instrument_id"] for r in rows}),"days":len({t[:10] for t in times}),
        "from":min(times,default=None),"to":max(times,default=None),"coverage":coverage,"targets":targets,
        "confidence_interval":None,"out_of_sample":False,"probability_validated":False}
    # Endpoint direction is a separate question from maximum favorable touch.
    directional=[(r,l['endpoint_return_pct']) for r,l in zip(rows,labels)
                 if l and l.get('status')=='known' and l.get('coverage_complete') is True
                 and eng.number(l.get('endpoint_return_pct'))]
    up=sum(v>0 for _,v in directional);down=sum(v<0 for _,v in directional);flat=sum(v==0 for _,v in directional)
    by_episode=defaultdict(list)
    for r,v in directional:
        if v!=0:by_episode[r['payload']['episode_id']].append(v>0)
    support['directional']={'horizon_minutes':horizon,'up':up,'down':down,'flat':flat,
                            'unknown_or_pending':len(rows)-len(directional),'nonflat_known':up+down,
                            'descriptive_up_frequency_nonflat':up/(up+down) if up+down else None,
                            'episode_weighted_up_frequency_nonflat':sum(sum(v)/len(v) for v in by_episode.values())/len(by_episode) if by_episode else None,
                            'known_episodes':len({r['payload']['episode_id'] for r,_ in directional}),
                            'nonflat_episodes':len(by_episode),'endpoint_return_pct':metric([v for _,v in directional]),
                            'reference_policy':'gate_best_ask_v1','probability_validated':False,'confidence_interval':None}
    # Compatibility fields are descriptive 0.8/5m only, never total history.
    support.update(known=targets["0.8"]["known"],unknown_or_pending=targets["0.8"]["unknown"],
                   hits=targets["0.8"]["hits"],descriptive_hit_rate=targets["0.8"]["descriptive_hit_rate"])
    return support


def summarize(rows, horizon, score_edges, conditions=None):
    contracts=defaultdict(list)
    for r in rows:contracts[contract_key(r["payload"])].append(r)
    cohorts=[]
    for key,sample in sorted(contracts.items()):
        patterns=defaultdict(list);scores=defaultdict(list)
        for r in sample:
            p=r["payload"]
            pattern=" + ".join(sorted(x["group"] for x in p["ledger"] if x["result"] is True)) or "sem confirmação"
            patterns[pattern].append(r)
            score=p.get("score_final")
            band="indisponível"
            if eng.number(score):
                band=f"<{score_edges[0]}" if score<score_edges[0] else f">={score_edges[-1]}"
                for lo,hi in zip(score_edges,score_edges[1:]):
                    if lo<=score<hi:band=f"[{lo}, {hi})";break
            scores[band].append(r)
        cohort_id=eng.canonical_hash({"compatibility":key,"horizon_minutes":horizon})
        cohorts.append({"cohort_id":cohort_id,"score_config_hash":key[0],"label_spec_hash":key[1],
            "feature_spec_hash":key[2],"producer_config_hash":key[3],"cost_policy_hash":key[4],
            "label_version":key[5],"reference_policy":key[6],"horizon_minutes":horizon,
            "baseline":describe(sample,horizon),"patterns":[{"pattern":k,**describe(v,horizon)} for k,v in sorted(patterns.items())],
            "score_bands":[{"pattern":k,**describe(v,horizon)} for k,v in sorted(scores.items())],
            "exploration":describe([r for r in sample if all(eng.condition(r["payload"]["values"],c) is True for c in conditions)],horizon) if conditions else None})
        from .pump_executive_insights import executive_groups
        cohorts[-1]['executive']=executive_groups(sample,horizon,describe)
    return cohorts
