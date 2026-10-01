"""Explicit offline XGBoost Pump challenger. Never reads Shadow or promotes."""
from __future__ import annotations
import json
from pathlib import Path
from . import pump_opportunity_engine as eng

ARTIFACT_NAMESPACE="pump_ml"


def selection_metrics(actual,selected):
    positives=sum(v is True for v in actual)
    chosen=sum(selected)
    kept=sum(v is True and s for v,s in zip(actual,selected))
    return {"observations":len(actual),"positives":positives,"selected":chosen,
            "precision":kept/chosen if chosen else None,"recall":kept/positives if positives else None,
            "rejected_winners":positives-kept}


def train_challenger(rows,*,spec,output_root):
    """All numeric gates, split dates, costs and resource ceilings must be explicit."""
    required=("features","boundaries","embargo_seconds","min_episodes","min_days","min_instruments",
              "max_rows","max_threads","params","decision_threshold","cost_policy_hash","support_criteria")
    if any(k not in spec for k in required): raise ValueError("Explicit Pump training manifest required")
    if len(rows)>spec["max_rows"] or not 0<spec["max_threads"]<=2:
        raise ValueError("Pump research budget exceeded")
    usable=[r for r in rows if r["manifest"]["listing_certified"] and r.get("target") in (True,False)
            and r.get("label_coverage_complete") and all(eng.number(r["values"].get(f)) for f in spec["features"])]
    hashes={tuple(r["manifest"][k] for k in ("feature_spec_hash","label_spec_hash","cost_policy_hash")) for r in usable}
    if len(hashes)!=1 or (hashes and next(iter(hashes))[2]!=spec["cost_policy_hash"]):
        raise ValueError("Incompatible Pump contracts or empty validated co cohort")
    if len({r["episode_id"] for r in usable})<spec["min_episodes"] or len({r["decision_at"][:10] for r in usable})<spec["min_days"] or len({r["instrument_id"] for r in usable})<spec["min_instruments"]:
        raise ValueError("Insufficient independent Pump support")
    boundaries=spec["boundaries"]
    if len(boundaries)!=3 or list(map(eng.utc,boundaries))!=sorted(map(eng.utc,boundaries)):
        raise ValueError("Three ordered train/validation/calibration/test cuts required")
    train,rest=eng.purged_split(usable,boundaries[0],spec["embargo_seconds"])
    val,rest=eng.purged_split(rest,boundaries[1],spec["embargo_seconds"])
    calibration,test=eng.purged_split(rest,boundaries[2],spec["embargo_seconds"])
    cohorts=[train,val,calibration,test]
    if any(not r or len({x["target"] for x in r})<2 for r in cohorts):
        raise ValueError("Each temporal cohort must contain both target outcomes")
    episode_sets=[{r["episode_id"] for r in cohort} for cohort in cohorts]
    if any(a&b for i,a in enumerate(episode_sets) for b in episode_sets[i+1:]):
        raise ValueError("Episode leakage across temporal cohorts")
    # Heavy libraries only load after all eligibility and budget gates pass.
    import numpy as np
    from xgboost import XGBClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import brier_score_loss,log_loss
    features=spec["features"]
    def xy(sample):
        return np.array([[r["values"][f] for f in features] for r in sample]),np.array([int(r["target"]) for r in sample])
    x,y=xy(train);vx,vy=xy(val);cx,cy=xy(calibration);tx,ty=xy(test)
    model=XGBClassifier(**{**spec["params"],"n_jobs":spec["max_threads"],"objective":"binary:logistic"})
    model.fit(x,y,eval_set=[(vx,vy)],verbose=False)
    calibrator=LogisticRegression().fit(model.predict_proba(cx)[:,1].reshape(-1,1),cy)
    probs=calibrator.predict_proba(model.predict_proba(tx)[:,1].reshape(-1,1))[:,1]
    baseline=float(y.mean())
    selected=(probs>=spec["decision_threshold"]).tolist()
    metrics={"test":selection_metrics([bool(v) for v in ty],selected),
             "base_score":selection_metrics([bool(v) for v in ty],[r["simulation"]["eligible"] for r in test]),
             "brier":float(brier_score_loss(ty,probs)),"log_loss":float(log_loss(ty,probs)),
             "baseline_brier":float(brier_score_loss(ty,np.repeat(baseline,len(ty)))),
             "cohort_rows":[len(c) for c in cohorts],"cohort_episodes":[len(c) for c in episode_sets],
             "calibration_curve":[{"lo":lo,"hi":lo+0.1,"n":int(sum((probs>=lo)&(probs<lo+0.1)))} for lo in np.arange(0,1,0.1)],
             "cluster_intervals":None,"status":"requires_clustered_review","applied_delta":0}
    experiment=eng.canonical_hash({"spec":spec,"observation_ids":[r["observation_id"] for r in usable]})
    root=Path(output_root).resolve()/ARTIFACT_NAMESPACE/experiment
    root.mkdir(parents=True,exist_ok=False)
    # Store the native Booster; avoid sklearn wrapper tag/version coupling.
    model.get_booster().save_model(root/"xgboost.json")
    (root/"calibrator.json").write_text(json.dumps({"coef":calibrator.coef_.tolist(),"intercept":calibrator.intercept_.tolist()}),encoding="utf-8")
    manifest={"experiment_id":experiment,"artifact_namespace":f"pump_ml/{experiment}","spec":spec,
              "contracts":list(next(iter(hashes))),"status":"challenger","auto_promotion":False,"delta":0}
    (root/"manifest.json").write_text(json.dumps(manifest,sort_keys=True),encoding="utf-8")
    (root/"metrics.json").write_text(json.dumps(metrics,sort_keys=True),encoding="utf-8")
    return {"manifest":manifest,"metrics":metrics}
