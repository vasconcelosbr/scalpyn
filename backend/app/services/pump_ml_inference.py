"""Pump ML inference for Pump Score v1 (v1.6, 2026-10-07).

Loads the NEWEST directional XGBoost challenger for ``score_v1.ml.horizon_minutes``
(a newer model always supersedes an older one, even when it fails the gate),
checks its stored out-of-time test metrics against ``score_v1.ml.quality`` and,
only when approved, returns P(price ends above the reference at the horizon)
per symbol. Every failure path returns ``active=False`` (zero effect).

Features are the same indicator cells the training observations stored
(``build_observation`` copies ``row["indicators"][k]["value"]``); missing, stale
or out-of-training-range features abstain for that symbol.
"""
from __future__ import annotations

import json
import logging
import math
import time
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from . import pump_score_v1 as v1

logger = logging.getLogger(__name__)

OBJECTIVE = "pump_endpoint_direction_v1"
_CACHE: Dict[str, Dict[str, Any]] = {}
_REFRESH_SECONDS = 600


class DirectionalModel:
    def __init__(self, experiment_id: str, manifest: Dict[str, Any], metrics: Dict[str, Any],
                 booster_json: bytes, calibrator: Dict[str, Any], created_at: str):
        import xgboost as xgb
        self.experiment_id = experiment_id
        self.manifest = manifest
        self.metrics = metrics
        self.created_at = created_at
        self.features: List[str] = list(manifest["spec"]["features"])
        self.bounds: Dict[str, Dict[str, float]] = manifest.get("feature_bounds") or {}
        self.calibrator = calibrator
        if calibrator.get("input") != "clipped_logit":
            raise ValueError("unknown_calibration_input")
        booster = xgb.Booster()
        booster.load_model(bytearray(booster_json))
        booster.set_param({"nthread": 1})
        self.booster = booster

    def vector(self, cells: Dict[str, Any], max_age_seconds: float) -> Optional[List[float]]:
        out = []
        for f in self.features:
            cell = cells.get(f) or {}
            value = cell.get("value")
            if cell.get("reason") or isinstance(value, bool) or not isinstance(value, (int, float)) \
                    or not math.isfinite(float(value)):
                return None
            age = cell.get("age_seconds")
            if isinstance(age, (int, float)) and age > max_age_seconds:
                return None
            b = self.bounds.get(f)
            if b and not (float(b["min"]) <= float(value) <= float(b["max"])):
                return None
            out.append(float(value))
        return out

    def predict(self, vectors: List[List[float]]) -> List[float]:
        import numpy as np
        import xgboost as xgb
        if not vectors:
            return []
        raw = self.booster.predict(xgb.DMatrix(np.array(vectors, dtype=float)))
        clip = float(self.calibrator["clip"])
        p = np.clip(raw, clip, 1 - clip)
        z = float(self.calibrator["coef"][0][0]) * np.log(p / (1 - p)) + float(self.calibrator["intercept"][0])
        return [float(x) for x in 1 / (1 + np.exp(-np.clip(z, -700, 700)))]


async def _fetch_newest(db, user_id, horizon: int, max_age_days: int):
    row = (await db.execute(text("""
        SELECT experiment_id, created_at, manifest, metrics FROM pump_ml_experiments
         WHERE user_id = CAST(:u AS uuid) AND manifest->>'objective' = :obj
           AND (manifest->'spec'->'directional_target'->>'horizon_minutes')::int = :h
           AND created_at >= now() - make_interval(days => :d)
         ORDER BY created_at DESC LIMIT 1
    """), {"u": str(user_id), "obj": OBJECTIVE, "h": int(horizon), "d": int(max_age_days)})).mappings().first()
    if row is None:
        return None, {}
    files = (await db.execute(text(
        "SELECT path, content FROM pump_ml_artifacts WHERE experiment_id = :e"),
        {"e": row["experiment_id"]})).mappings().all()
    return row, {r["path"].rsplit("/", 1)[-1]: bytes(r["content"]) for r in files}


def _as_dict(value):
    return json.loads(value) if isinstance(value, (str, bytes)) else (value or {})


async def load_model(db, user_id, spec: Dict[str, Any], *, now: Optional[float] = None) -> Dict[str, Any]:
    """``{active, reason, model(meta), predictor}`` with a 10-minute in-process cache."""
    cfg = spec.get("ml") or {}
    if not cfg.get("enabled"):
        return {"active": False, "reason": "ml_disabled"}
    now = now if now is not None else time.time()
    key = f"{user_id}:{cfg['horizon_minutes']}"
    cached = _CACHE.get(key)
    if cached and now - cached["fetched_at"] < _REFRESH_SECONDS:
        return cached["value"]
    try:
        row, files = await _fetch_newest(db, user_id, int(cfg["horizon_minutes"]), int(cfg["max_model_age_days"]))
        if row is None:
            value = {"active": False, "reason": "no_recent_model"}
        else:
            metrics = _as_dict(row["metrics"])
            quality = v1.ml_quality(metrics, spec)
            meta = {"experiment_id": str(row["experiment_id"]), "created_at": row["created_at"].isoformat(),
                    "horizon_minutes": int(cfg["horizon_minutes"]), "quality": quality}
            if not quality["approved"]:
                value = {"active": False, "reason": "quality_gate_failed", "model": meta}
            elif "xgboost.json" not in files or "calibrator.json" not in files:
                value = {"active": False, "reason": "artifacts_missing", "model": meta}
            else:
                predictor = DirectionalModel(meta["experiment_id"], _as_dict(row["manifest"]), metrics,
                                             files["xgboost.json"], json.loads(files["calibrator.json"]),
                                             meta["created_at"])
                value = {"active": True, "reason": None, "model": meta, "predictor": predictor}
    except Exception as exc:
        logger.warning("[PUMP-ML] model load failed user=%s reason=%s", user_id, type(exc).__name__)
        value = {"active": False, "reason": f"load_failed:{type(exc).__name__}"}
    _CACHE[key] = {"fetched_at": now, "value": value}
    return value


def predict_rows(loaded: Dict[str, Any], rows: List[Dict[str, Any]], spec: Dict[str, Any]) -> Dict[str, Any]:
    """Evaluate-universe payload: ``{active, reason, model, probabilities}``."""
    out = {k: loaded.get(k) for k in ("active", "reason", "model")}
    if not loaded.get("active"):
        return {**out, "probabilities": {}}
    predictor: DirectionalModel = loaded["predictor"]
    max_age = float(spec["ml"]["max_feature_age_seconds"])
    symbols, vectors = [], []
    for row in rows:
        vec = predictor.vector(row.get("indicators") or {}, max_age)
        if vec is not None:
            symbols.append(row["symbol"])
            vectors.append(vec)
    try:
        probs = dict(zip(symbols, predictor.predict(vectors)))
    except Exception as exc:
        logger.warning("[PUMP-ML] prediction failed reason=%s", type(exc).__name__)
        return {**out, "active": False, "reason": f"predict_failed:{type(exc).__name__}", "probabilities": {}}
    return {**out, "probabilities": probs, "covered": len(probs), "universe": len(rows)}
