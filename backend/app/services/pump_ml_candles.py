"""Candle-based history for the Pump ML (2026-10-07).

Why: the observation dataset only starts 01/10, so the train block of each model
sees ~2 days. Everything the relative objective needs (beta, relative residuals,
BTC lead, market return, the label itself) is a function of closed spot candles,
which ``ohlcv`` already holds — so a price-only model can learn from the whole
candle history now. Order-flow features (delta, CVD, buy persistence) exist only
live and are NOT part of this family.

One vectorized builder (``build_frame``) serves BOTH training (all decision times)
and live inference (last closed candle), so features are identical by
construction. A decision at the close of candle ``t`` uses candles ``<= t`` only;
labels use candles ``t+1 .. t+k`` and are built only for training.

Nothing here mutates captured observation snapshots (separate research dataset,
provenance ``ohlcv_closed_candles``).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

CANDLE_OBJECTIVE = "pump_relative_candle_v1"
REFERENCE = "BTC_USDT"


def feature_names(cfg: Dict[str, Any]) -> List[str]:
    names = ["beta_24h"]
    names += [f"rel_resid_{k}" for k in cfg["prev_windows"]]
    names += ["vol_ratio", "btc_ret_1", "btc_ret_3", "lag_gap_1", "lag_gap_3", "mkt_ret_1", "mkt_ret_3"]
    return names


def _matrix(closes: Dict[str, Dict[int, float]], step: int):
    import numpy as np
    grid = sorted({t for s in closes.values() for t in s})
    if grid:
        grid = list(range(grid[0], grid[-1] + step, step))   # regular grid; gaps stay NaN
    syms = sorted(closes)
    idx = {t: i for i, t in enumerate(grid)}
    C = np.full((len(syms), len(grid)), np.nan)
    for i, s in enumerate(syms):
        for t, c in closes[s].items():
            if c and c > 0 and t in idx:
                C[i, idx[t]] = c
    return syms, np.array(grid, dtype=np.int64), C


def _ret(C, k):
    import numpy as np
    out = np.full_like(C, np.nan)
    out[:, k:] = (C[:, k:] / C[:, :-k] - 1) * 100
    return out


def _fwd(C, k):
    import numpy as np
    out = np.full_like(C, np.nan)
    out[:, :-k] = (C[:, k:] / C[:, :-k] - 1) * 100
    return out


def _nanmedian(a, axis=0):
    import numpy as np
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return np.nanmedian(a, axis=axis)


def _residual(r, beta):
    """Cross-sectional residual per decision time: (r - beta*median(r)) - median(...)."""
    m = _nanmedian(r, axis=0)
    e = r - beta * m[None, :]
    return e - _nanmedian(e, axis=0)[None, :], e


def _rolling_beta(R1, M1, window, min_points):
    import numpy as np
    ok = ~np.isnan(R1) & ~np.isnan(M1)[None, :]
    x = np.where(ok, M1[None, :], 0.0); y = np.where(ok, R1, 0.0)
    pad = lambda a: np.concatenate([np.zeros((a.shape[0], 1)), np.cumsum(a, axis=1)], axis=1)
    n, sx, sy, sxx, sxy = pad(ok.astype(float)), pad(x), pad(y), pad(x * x), pad(x * y)
    T = R1.shape[1]
    hi = np.arange(1, T + 1); lo = np.maximum(0, hi - window)
    cnt = n[:, hi] - n[:, lo]
    with np.errstate(all="ignore"):
        vx = (sxx[:, hi] - sxx[:, lo]) - (sx[:, hi] - sx[:, lo]) ** 2 / cnt
        cov = (sxy[:, hi] - sxy[:, lo]) - (sx[:, hi] - sx[:, lo]) * (sy[:, hi] - sy[:, lo]) / cnt
        b = cov / vx
    b[(cnt < min_points) | ~(vx > 0)] = np.nan
    return b


def _rolling_std(R1, window, min_points):
    import numpy as np
    ok = ~np.isnan(R1); y = np.where(ok, R1, 0.0)
    pad = lambda a: np.concatenate([np.zeros((a.shape[0], 1)), np.cumsum(a, axis=1)], axis=1)
    n, s, ss = pad(ok.astype(float)), pad(y), pad(y * y)
    T = R1.shape[1]; hi = np.arange(1, T + 1); lo = np.maximum(0, hi - window)
    cnt = n[:, hi] - n[:, lo]
    with np.errstate(all="ignore"):
        var = ((ss[:, hi] - ss[:, lo]) - (s[:, hi] - s[:, lo]) ** 2 / cnt) / (cnt - 1)
    var[cnt < min_points] = np.nan
    return np.sqrt(np.clip(var, 0, None))


def build_frame(closes: Dict[str, Dict[int, float]], cfg: Dict[str, Any], *,
                horizons_candles: Sequence[int] = ()) -> Dict[str, Any]:
    """Features (and, for training, labels) on the regular candle grid.

    Returns ``{syms, grid(open epochs), features{name:[S,T]}, labels{(k,mode):[S,T]},
    excess{k:[S,T]}, assets{k:[T]}}``. Value at column t = decision at the CLOSE of
    candle t (uses candles <= t)."""
    import numpy as np
    step = int(cfg["step_seconds"])
    syms, grid, C = _matrix(closes, step)
    out: Dict[str, Any] = {"syms": syms, "grid": grid, "features": {}, "labels": {}, "excess": {}, "assets": {}}
    if not syms or len(grid) < 2:
        return out
    R1 = _ret(C, 1)
    M1 = _nanmedian(R1, axis=0)
    beta = _rolling_beta(R1, M1, int(cfg["beta_window"]), int(cfg["beta_min_points"]))
    f = out["features"]
    f["beta_24h"] = beta
    for k in cfg["prev_windows"]:
        f[f"rel_resid_{k}"], _ = _residual(_ret(C, int(k)), beta)
    short = _rolling_std(R1, int(cfg["vol_short"]), max(3, int(cfg["vol_short"]) // 2))
    long_ = _rolling_std(R1, int(cfg["beta_window"]), int(cfg["beta_min_points"]))
    with np.errstate(all="ignore"):
        f["vol_ratio"] = short / long_
    ref = syms.index(REFERENCE) if REFERENCE in syms else None
    for k in (1, 3):
        rk = _ret(C, k)
        btc = rk[ref] if ref is not None else np.full(len(grid), np.nan)
        f[f"btc_ret_{k}"] = np.broadcast_to(btc, C.shape).copy()
        f[f"lag_gap_{k}"] = beta * btc[None, :] - rk
        f[f"mkt_ret_{k}"] = np.broadcast_to(_nanmedian(rk, axis=0), C.shape).copy()
    for k in horizons_candles:
        resid_end, e_end = _residual(_fwd(C, int(k)), beta)
        out["excess"][k] = resid_end
        out["assets"][k] = np.sum(~np.isnan(e_end), axis=0)
        out["labels"][(k, "endpoint")] = resid_end
        path = [_residual(_fwd(C, j), beta)[0] for j in range(1, int(k) + 1)]
        stack = np.stack(path)
        with np.errstate(all="ignore"):
            mean = np.mean(stack, axis=0)            # NaN if any step is missing
        out["labels"][(k, "path_mean")] = mean
    return out


def live_cells(frame: Dict[str, Any], cfg: Dict[str, Any], now_s: int) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """Feature cells at the last candle CLOSED by ``now_s`` (open <= now - step)."""
    import numpy as np
    step = int(cfg["step_seconds"])
    grid = frame["grid"]
    if len(grid) == 0:
        return {}
    col = int(np.searchsorted(grid, now_s - step, side="right")) - 1
    if col < 0:
        return {}
    out = {}
    for i, s in enumerate(frame["syms"]):
        cells = {}
        for name, arr in frame["features"].items():
            v = float(arr[i, col])
            ok = np.isfinite(v)
            cells[name] = {"value": v if ok else None, "reason": None if ok else "candle_feature_unavailable",
                           "source": "ohlcv_closed_candles_v1"}
        out[s] = cells
    return out


def training_rows(frame: Dict[str, Any], cfg: Dict[str, Any], k: int, mode: str) -> List[Dict[str, Any]]:
    """Deterministic, outcome-blind subsample: at most ``max_rows_per_time`` assets per
    decision time (hash order), then evenly spaced times down to ``max_rows``."""
    import numpy as np
    names = feature_names(cfg)
    label = frame["labels"][(k, mode)]; excess = frame["excess"][k]; assets = frame["assets"][k]
    beta = frame["features"]["beta_24h"]
    step = int(cfg["step_seconds"]); cap = int(cfg["max_rows_per_time"]); min_assets = int(cfg["min_assets"])
    by_time = []
    for t, open_epoch in enumerate(frame["grid"]):
        if assets[t] < min_assets:
            continue
        cand = [i for i in range(len(frame["syms"]))
                if np.isfinite(label[i, t]) and abs(label[i, t]) > 1e-9 and np.isfinite(beta[i, t])
                and np.isfinite(excess[i, t])]
        if not cand:
            continue
        key = lambda i: hashlib.md5(f"{frame['syms'][i]}:{int(open_epoch)}".encode()).hexdigest()
        by_time.append((t, sorted(cand, key=key)[:cap]))
    total = sum(len(c) for _, c in by_time)
    if total > int(cfg["max_rows"]) and by_time:
        keep = max(1, int(len(by_time) * int(cfg["max_rows"]) / total))
        pick = np.linspace(0, len(by_time) - 1, keep).round().astype(int)
        by_time = [by_time[i] for i in sorted(set(pick.tolist()))]
    rows = []
    for t, idx in by_time:
        decision = datetime.fromtimestamp(int(frame["grid"][t]) + step, timezone.utc)
        for i in idx:
            rows.append({"decision_at": decision, "episode_id": f"t{int(frame['grid'][t])}",
                         "symbol": frame["syms"][i], "target": bool(label[i, t] > 0),
                         "endpoint_return_pct": float(excess[i, t]),
                         "values": {n: (float(frame["features"][n][i, t])
                                        if np.isfinite(frame["features"][n][i, t]) else None) for n in names}})
    return rows


def train_candle_model(rows: List[Dict[str, Any]], *, cfg: Dict[str, Any], horizon_minutes: int,
                       options: Dict[str, Any], params: Dict[str, Any], output_root,
                       compare: Optional[Dict[str, List[Dict[str, Any]]]] = None) -> Dict[str, Any]:
    """Persisted model: fit on the past, bounded-Platt calibration on the last
    ``calibration_fraction`` (embargoed); quality judged by the walk-forward block.
    ``compare``: other label modes' rows, evaluated walk-forward only (reported)."""
    import numpy as np
    import sklearn
    import xgboost as xgb
    from . import pump_opportunity_engine as eng
    from .pump_directional_research import (_bounds, _importance, apply_calibration, episode_weights,
                                            fit_calibration, walk_forward_evaluation)
    names = feature_names(cfg)
    rows = sorted(rows, key=lambda r: r["decision_at"])
    if len(rows) < 500 or len({r["target"] for r in rows}) < 2:
        raise ValueError("insufficient_candle_rows")
    spec = {"features": names, "context_features": [], "params": params, "max_threads": 1,
            "embargo_seconds": int(cfg["embargo_seconds"]), "walk_forward": cfg["walk_forward"],
            "directional_target": {"version": CANDLE_OBJECTIVE, "horizon_minutes": int(horizon_minutes),
                                   "label_mode": cfg["label_mode"], "benchmark_policy": "universe_beta_residual_median_close_v1"},
            "candle": cfg}
    embargo = timedelta(seconds=int(cfg["embargo_seconds"]))
    cut = rows[int(len(rows) * (1 - float(cfg["walk_forward"]["calibration_fraction"])))]["decision_at"]
    cal = [r for r in rows if r["decision_at"] >= cut]
    fit = [r for r in rows if r["decision_at"] < cut - embargo]
    def X(c):
        return np.array([[np.nan if r["values"].get(n) is None else r["values"][n] for n in names] for r in c],
                        dtype=float).reshape(len(c), len(names))
    fx, fy, fw = X(fit), np.array([int(r["target"]) for r in fit]), np.array(episode_weights(fit))
    model = xgb.XGBClassifier(**{**params, "n_jobs": 1, "objective": "binary:logistic"})
    model.fit(fx, fy, sample_weight=fw, verbose=False)
    raw = np.clip(model.predict_proba(X(cal))[:, 1], 1e-6, 1 - 1e-6)
    calib = fit_calibration(np.log(raw / (1 - raw)), np.array([int(r["target"]) for r in cal]),
                            np.array(episode_weights(cal)), options, params["random_state"])
    wf = walk_forward_evaluation(rows, columns=names, spec=spec, options=options, relative=False)
    variants = {cfg["label_mode"]: wf.get("pooled")}
    for mode, other in (compare or {}).items():
        variants[mode] = walk_forward_evaluation(sorted(other, key=lambda r: r["decision_at"]), columns=names,
                                                 spec=spec, options=options, relative=False).get("pooled")
    versions = {"xgboost": xgb.__version__, "numpy": np.__version__, "scikit_learn": sklearn.__version__}
    experiment = eng.canonical_hash({"spec": spec, "n": len(rows), "first": rows[0]["decision_at"].isoformat(),
                                     "last": rows[-1]["decision_at"].isoformat(), "library_versions": versions})
    folder = Path(output_root).resolve() / "pump_ml" / "candle" / experiment
    folder.mkdir(parents=True, exist_ok=False)
    model.get_booster().save_model(folder / "xgboost.json")
    days = sorted({r["decision_at"].date() for r in rows})
    metrics = {"status": "requires_independent_directional_review", "walk_forward": wf, "label_variants": variants,
               "support": {"rows": len(rows), "decision_times": len({r["episode_id"] for r in rows}),
                           "symbols": len({r["symbol"] for r in rows}), "days": len(days),
                           "from": rows[0]["decision_at"].isoformat(), "to": rows[-1]["decision_at"].isoformat()},
               "cohort_rows": [len(fit), 0, len(cal), (wf.get("pooled") or {}).get("rows", 0)],
               "cohort_episodes": [len({r["episode_id"] for r in fit}), 0, len({r["episode_id"] for r in cal}),
                                   (wf.get("pooled") or {}).get("episodes", 0)],
               "cohort_days": [len({r["decision_at"].date() for r in fit}), 0,
                               len({r["decision_at"].date() for r in cal}), wf.get("scored_days", 0)],
               "calibration": {k: calib[k] for k in ("method", "slope", "intercept", "unbounded_slope", "bounded")}
                              | {"pool": "last_fraction", "rows": len(cal)},
               "feature_importance_gain": _importance(model, names),
               "applied_delta": 0, "auto_promotion": False}
    manifest = {"experiment_id": experiment, "artifact_namespace": f"pump_ml/candle/{experiment}",
                "objective": CANDLE_OBJECTIVE, "spec": spec, "status": "challenger", "auto_promotion": False,
                "delta": 0, "library_versions": versions, "provenance": "ohlcv_closed_candles",
                "probability_event": f"beta_adjusted_relative_{cfg['label_mode']}_above_universe_median",
                "feature_bounds": _bounds(fx, names)}
    calibrator = {"input": "clipped_logit", "clip": 1e-6, "coef": [[calib["slope"]]],
                  "intercept": [calib["intercept"]], "method": calib["method"], "pool": "last_fraction"}
    for name, data in (("manifest.json", manifest), ("calibrator.json", calibrator), ("metrics.json", metrics)):
        (folder / name).write_text(json.dumps(data, sort_keys=True, allow_nan=False, default=str), encoding="utf-8")
    return {"manifest": manifest, "metrics": metrics}


async def load_closes(conn, symbols: List[str], timeframe: str, lo: datetime, hi: datetime,
                      chunk: int = 10) -> Dict[str, Dict[int, float]]:
    """Closed spot candles, Gate preferred (asyncpg; chunks of symbols)."""
    from .pump_ml_selection import CANDLES_SQL
    out: Dict[str, Dict[int, float]] = {}
    for i in range(0, len(symbols), chunk):
        for rec in await conn.fetch(CANDLES_SQL, symbols[i:i + chunk], timeframe, lo, hi):
            if rec["close"] is not None:
                out.setdefault(rec["symbol"], {})[int(rec["time"].timestamp())] = float(rec["close"])
    return out


UNIVERSE_SQL = """SELECT DISTINCT symbol FROM pump_opportunity_observations
 WHERE user_id=$1 AND slot_at >= now() - interval '1 day'"""


async def run_candle_horizons(conn, owner, c: Dict[str, Any], diagnostics: Dict[str, Any], staging: str,
                              deadline: datetime) -> List[Dict[str, Any]]:
    """Train one candle model per configured horizon (multiple of the timeframe)."""
    import asyncio
    research = c["research"]; cfg = {**research["candle"], "walk_forward": research["walk_forward"]}
    diag: Dict[str, Any] = {}
    diagnostics["candle"] = diag
    symbols = sorted({r["symbol"] for r in await conn.fetch(UNIVERSE_SQL, owner)} | {REFERENCE})
    hi = datetime.now(timezone.utc)
    lo = hi - timedelta(days=int(cfg["lookback_days"]))
    closes = await load_closes(conn, symbols, cfg["timeframe"], lo, hi)
    step = int(cfg["step_seconds"])
    ks = {int(h) // (step // 60): int(h) for h in cfg["horizons_minutes"]}
    frame = await asyncio.to_thread(build_frame, closes, cfg, horizons_candles=list(ks))
    diag.update(symbols=len(symbols), symbols_with_candles=len(closes),
                candles=sum(len(v) for v in closes.values()),
                first=datetime.fromtimestamp(int(frame["grid"][0]), timezone.utc).isoformat() if len(frame["grid"]) else None,
                last=datetime.fromtimestamp(int(frame["grid"][-1]), timezone.utc).isoformat() if len(frame["grid"]) else None,
                horizons={})
    results = []
    for k, h in ks.items():
        if (deadline - datetime.now(timezone.utc)).total_seconds() < 60:
            diag["horizons"][str(h)] = {"blocked_reason": "runtime_budget_exhausted"}
            continue
        rows = training_rows(frame, cfg, k, cfg["label_mode"])
        compare = {m: training_rows(frame, cfg, k, m) for m in cfg["compare_label_modes"] if m != cfg["label_mode"]}
        diag["horizons"][str(h)] = {"rows": len(rows), "decision_times": len({r["episode_id"] for r in rows})}
        try:
            result = await asyncio.wait_for(asyncio.to_thread(
                train_candle_model, rows, cfg=cfg, horizon_minutes=h, options={
                    k2: research[k2] for k2 in ("calibration_C", "calibration_max_iter", "calibration_method",
                                                "calibration_max_slope", "bootstrap_repetitions")},
                params=research["params"], output_root=staging, compare=compare),
                timeout=max(1, (deadline - datetime.now(timezone.utc)).total_seconds()))
        except ValueError as exc:
            diag["horizons"][str(h)]["blocked_reason"] = str(exc)
            continue
        results.append(result)
    return results


_LIVE_CANDLES = """
    SELECT DISTINCT ON (symbol, time) symbol, time, close FROM ohlcv
     WHERE symbol = ANY(CAST(:s AS text[])) AND timeframe = :tf AND market_type = 'spot'
       AND is_closed IS TRUE AND time >= :lo AND time < :hi
     ORDER BY symbol, time, CASE WHEN exchange ILIKE 'gate%' THEN 0 ELSE 1 END
"""


async def candle_context(db, symbols: List[str], cfg: Dict[str, Any], now_s: int) -> Dict[str, Dict[str, Any]]:
    """Live candle-family cells for ``symbols`` (SQLAlchemy session), via the same
    ``build_frame`` used in training; ``cfg`` comes from the model manifest."""
    from sqlalchemy import text
    step = int(cfg["step_seconds"])
    need = int(cfg["beta_window"]) + max(int(k) for k in cfg["prev_windows"]) + 3
    hi = datetime.fromtimestamp(now_s, timezone.utc)
    lo = hi - timedelta(seconds=step * need)
    syms = sorted(set(symbols) | {REFERENCE})
    rows = (await db.execute(text(_LIVE_CANDLES), {"s": syms, "tf": cfg["timeframe"], "lo": lo, "hi": hi})).all()
    closes: Dict[str, Dict[int, float]] = {}
    for sym, t, close in rows:
        if close is not None:
            closes.setdefault(sym, {})[int(t.timestamp())] = float(close)
    frame = build_frame(closes, cfg)
    cells = live_cells(frame, cfg, now_s)
    return {s: cells[s] for s in symbols if s in cells}
