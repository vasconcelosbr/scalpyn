"""Pump ML live evaluation (v1.14).

Every closed candle the APPLIED candle-family model scores the pool; those
probabilities are appended to ``pump_ml_live_predictions`` (one row per
experiment × horizon × decision × symbol, never updated). Evaluation joins them
with the realised label computed by the SAME ``build_frame`` used in training
(beta-residual vs. the universe median, ``label_mode`` from the model manifest),
so live AUC is directly comparable with the walk-forward numbers.

Read-only from the cycle's point of view: logging failures never touch the score.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

INSERT_SQL = """
    INSERT INTO pump_ml_live_predictions
        (user_id, experiment_id, objective, horizon_minutes, decision_at, symbol, p_up, applied)
    SELECT CAST(:u AS uuid), r.e, r.o, r.h, r.d, r.s, r.p, r.a
      FROM jsonb_to_recordset(CAST(:batch AS jsonb))
        AS r(e text, o text, h integer, d timestamptz, s text, p double precision, a boolean)
    ON CONFLICT (user_id, experiment_id, horizon_minutes, decision_at, symbol) DO NOTHING
"""

SELECT_SQL = """
    SELECT experiment_id, decision_at, symbol, p_up FROM pump_ml_live_predictions
     WHERE user_id = CAST(:u AS uuid) AND objective = :o AND horizon_minutes = :h
       AND decision_at >= :since
     ORDER BY decision_at
"""


def prediction_rows(model: Dict[str, Any], objective: str, horizon: int, decision_s: int,
                    probabilities: Dict[str, Optional[float]], applied: bool) -> List[Dict[str, Any]]:
    """Batch rows for ``INSERT_SQL``. ``decision_s`` = close of the candle the features used."""
    exp = str((model or {}).get("experiment_id") or "")
    if not exp:
        return []
    d = datetime.fromtimestamp(int(decision_s), timezone.utc).isoformat()
    return [{"e": exp, "o": objective, "h": int(horizon), "d": d, "s": s, "p": round(float(p), 6), "a": bool(applied)}
            for s, p in sorted(probabilities.items()) if p is not None]


def evaluate(preds: List[Dict[str, Any]], closes: Dict[str, Dict[int, float]], cfg: Dict[str, Any], *,
             horizon_minutes: int, quantile: float, min_day_rows: int = 20) -> Dict[str, Any]:
    """Live metrics for ``preds`` = [{decision_at (datetime), symbol, p_up}].

    A prediction is *mature* when its realised label exists (all candles of the
    horizon closed and at least ``min_assets`` assets at that time, as in training)."""
    import numpy as np
    from sklearn.metrics import roc_auc_score
    from .pump_directional_research import sign_test_p
    from .pump_ml_candles import build_frame

    step = int(cfg["step_seconds"])
    k = max(1, int(round(int(horizon_minutes) * 60 / step)))
    mode = cfg.get("label_mode", "path_mean")
    frame = build_frame(closes, cfg, horizons_candles=[k])
    out: Dict[str, Any] = {"predictions": len(preds), "label_mode": mode, "horizon_candles": k}
    if not frame["syms"] or (k, mode) not in frame["labels"]:
        return {**out, "mature": 0, "pending": len(preds), "reason": "no_candles"}
    label, excess, assets = frame["labels"][(k, mode)], frame["excess"][k], frame["assets"][k]
    col = {int(t): j for j, t in enumerate(frame["grid"])}
    row = {s: i for i, s in enumerate(frame["syms"])}
    min_assets = int(cfg["min_assets"])
    P, Y, E, D = [], [], [], []
    pending = 0
    for p in preds:
        t = col.get(int(p["decision_at"].timestamp()) - step)       # candle whose close is the decision
        i = row.get(p["symbol"])
        if t is None or i is None or assets[t] < min_assets:
            pending += 1
            continue
        lab = label[i, t]
        if not np.isfinite(lab) or abs(lab) <= 1e-9:
            pending += 1
            continue
        P.append(float(p["p_up"])); Y.append(int(lab > 0))
        E.append(float(excess[i, t]) if np.isfinite(excess[i, t]) else np.nan)
        D.append(p["decision_at"].date().isoformat())
    out.update(mature=len(P), pending=pending)
    if len(P) < 2 or len(set(Y)) < 2:
        return {**out, "reason": "too_few_mature"}
    P, Y, E = np.array(P), np.array(Y), np.array(E)
    days: Dict[str, List[int]] = defaultdict(list)
    for j, d in enumerate(D):
        days[d].append(j)
    per_day = []
    for d in sorted(days):
        idx = np.array(days[d])
        if len(idx) >= min_day_rows and len(set(Y[idx])) == 2:
            per_day.append({"day": d, "rows": int(len(idx)), "auc": float(roc_auc_score(Y[idx], P[idx])),
                            "up_frequency": float(Y[idx].mean()), "mean_probability": float(P[idx].mean())})
    aucs = np.array([x["auc"] for x in per_day])
    above = int((aucs > 0.5).sum())
    ok = ~np.isnan(E)
    eco = None
    if ok.sum() >= 20:
        hi, lo = np.quantile(P[ok], 1 - quantile), np.quantile(P[ok], quantile)
        top, bottom = E[ok][P[ok] >= hi], E[ok][P[ok] <= lo]
        if top.size and bottom.size:
            eco = {"quantile": quantile, "unit": "pp_excess_over_benchmark",
                   "top_mean": float(top.mean()), "bottom_mean": float(bottom.mean()),
                   "top_minus_bottom": float(top.mean() - bottom.mean()),
                   "top_rows": int(top.size), "bottom_rows": int(bottom.size)}
    return {**out,
            "decision_times": int(len({p["decision_at"] for p in preds})),
            "pooled_auc": float(roc_auc_score(Y, P)),
            "day_auc_median": float(np.median(aucs)) if aucs.size else None,
            "days_scored": int(aucs.size), "days_auc_above_half": above,
            "sign_test_p": sign_test_p(above, int(aucs.size)) if aucs.size else None,
            "mean_probability": float(P.mean()), "realised_up_frequency": float(Y.mean()),
            "brier": float(np.mean((Y - P) ** 2)), "baseline_brier": float(np.mean((Y - Y.mean()) ** 2)),
            "economic": eco, "days": per_day}


async def live_evaluation(db, user_id, spec: Dict[str, Any], *, days: int) -> Dict[str, Any]:
    """``GET /ml/live-evaluation``: applied candle objective and horizon, last ``days`` days."""
    from sqlalchemy import text
    from . import pump_ml_inference as inf
    from . import pump_opportunity_engine as eng
    from .pump_ml_candles import REFERENCE, _LIVE_CANDLES

    ml = spec.get("ml") or {}
    objective = inf.objective_for(spec)
    horizon = int(ml["horizon_minutes"])
    base = {"objective": objective, "horizon_minutes": horizon, "days": days}
    if objective != inf.CANDLE_OBJECTIVE:
        return {**base, "status": "unsupported_objective",
                "note": "Avaliação ao vivo só para a família de velas (rótulo recalculável a partir de ohlcv)."}
    loaded = await inf.load_model(db, user_id, spec)
    cfg = inf.candle_params(loaded.get("predictor")) or eng.config(None)["research"]["candle"]
    quantile = float(eng.config(None)["research"]["walk_forward"]["economic_quantile"])
    since = datetime.now(timezone.utc) - timedelta(days=days)
    await db.execute(text("SET LOCAL statement_timeout = '20000ms'"))
    rows = (await db.execute(text(SELECT_SQL), {"u": str(user_id), "o": objective, "h": horizon,
                                                "since": since})).all()
    if not rows:
        return {**base, "status": "no_predictions"}
    preds = [{"experiment_id": r[0], "decision_at": r[1], "symbol": r[2], "p_up": float(r[3])} for r in rows]
    step = int(cfg["step_seconds"])
    lo = preds[0]["decision_at"] - timedelta(seconds=step * (int(cfg["beta_window"]) + 3))
    hi = preds[-1]["decision_at"] + timedelta(minutes=horizon, seconds=2 * step)
    syms = sorted({p["symbol"] for p in preds} | {REFERENCE})
    candles = (await db.execute(text(_LIVE_CANDLES), {"s": syms, "tf": cfg["timeframe"], "lo": lo, "hi": hi})).all()
    closes: Dict[str, Dict[int, float]] = {}
    for sym, t, close in candles:
        if close is not None:
            closes.setdefault(sym, {})[int(t.timestamp())] = float(close)
    experiments = defaultdict(int)
    for p in preds:
        experiments[p["experiment_id"]] += 1
    metrics = evaluate(preds, closes, cfg, horizon_minutes=horizon, quantile=quantile)
    return {**base, "status": "ok", "experiments": dict(experiments),
            "first": preds[0]["decision_at"].isoformat(), "last": preds[-1]["decision_at"].isoformat(),
            **metrics}
