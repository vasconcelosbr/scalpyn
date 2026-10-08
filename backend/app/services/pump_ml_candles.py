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


# Optional feature groups (v1.18, 2026-10-08), switched on by ``research.candle.feature_groups``.
# The base list (no groups) is unchanged, so every stored model keeps its column order.
GROUPS = ("btc_beta", "candle_structure", "volume", "pool_context")
GROUP_FEATURES = {
    # beta estimated against BTC itself (the base lag_gap reuses the beta vs the POOL median)
    "btc_beta": ["beta_btc_24h", "lag_gap_btc_1", "lag_gap_btc_3"],
    # shape of the last closed candle + distance to the 1-hour extremes (needs OHLC)
    "candle_structure": ["cs_range", "cs_body", "cs_upper_wick", "cs_lower_wick", "cs_close_pos",
                         "cs_dist_high_12", "cs_dist_low_12"],
    # quote volume vs the asset's own 24 h median (needs volume)
    "volume": ["vol_rel_1", "vol_rel_3", "vol_accel_3"],
    # cross-section of the pool at the decision time
    "pool_context": ["pool_disp_1", "pool_disp_3", "pool_breadth_3", "pool_rank_3"],
}
NEEDS_BARS = {"candle_structure", "volume"}


def feature_names(cfg: Dict[str, Any]) -> List[str]:
    groups = set(cfg.get("feature_groups") or [])
    names = ["beta_24h"]
    names += [f"rel_resid_{k}" for k in cfg["prev_windows"]]
    names += ["vol_ratio", "btc_ret_1", "btc_ret_3"]
    if "btc_beta" not in groups:          # replaced by the BTC-beta version, never both
        names += ["lag_gap_1", "lag_gap_3"]
    names += ["mkt_ret_1", "mkt_ret_3"]
    for g in GROUPS:
        if g in groups:
            names += GROUP_FEATURES[g]
    return names


def all_feature_names(cfg: Dict[str, Any]) -> List[str]:
    """Union of every group's columns (ablation keeps them all in the rows)."""
    out = feature_names({**cfg, "feature_groups": []})
    for g in GROUPS:
        out += [f for f in GROUP_FEATURES[g] if f not in out]
    return out


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


def _bar_matrices(bars: Optional[Dict[str, Dict[int, Sequence[float]]]], syms, grid):
    """(O, H, L, V) aligned to the close grid; NaN where a bar is missing."""
    import numpy as np
    shape = (len(syms), len(grid))
    O, H, L, V = (np.full(shape, np.nan) for _ in range(4))
    if not bars:
        return O, H, L, V
    idx = {int(t): j for j, t in enumerate(grid)}
    for i, s in enumerate(syms):
        for t, bar in (bars.get(s) or {}).items():
            j = idx.get(int(t))
            if j is None:
                continue
            o, h, l, v = bar
            O[i, j], H[i, j], L[i, j] = (np.nan if x is None else float(x) for x in (o, h, l))
            V[i, j] = np.nan if v is None else float(v)
    return O, H, L, V


def _group_features(f: Dict[str, Any], groups, C, R1, beta, syms, grid, cfg, bars):
    """Optional groups, all point-in-time (column t uses candles <= t only)."""
    import numpy as np
    import pandas as pd
    window, min_points = int(cfg["beta_window"]), int(cfg["beta_min_points"])
    ref = syms.index(REFERENCE) if REFERENCE in syms else None
    if "btc_beta" in groups:
        btc1 = R1[ref] if ref is not None else np.full(len(grid), np.nan)
        bb = _rolling_beta(R1, btc1, window, min_points)
        f["beta_btc_24h"] = bb
        for k in (1, 3):
            rk = _ret(C, k)
            btc = rk[ref] if ref is not None else np.full(len(grid), np.nan)
            f[f"lag_gap_btc_{k}"] = bb * btc[None, :] - rk
    if groups & NEEDS_BARS:
        O, H, L, V = _bar_matrices(bars, syms, grid)
    if "candle_structure" in groups:
        with np.errstate(all="ignore"):
            rng = H - L
            ok = rng > 0
            f["cs_range"] = np.where(C > 0, rng / C * 100, np.nan)
            f["cs_body"] = np.where(ok, (C - O) / rng, np.nan)
            f["cs_upper_wick"] = np.where(ok, (H - np.maximum(O, C)) / rng, np.nan)
            f["cs_lower_wick"] = np.where(ok, (np.minimum(O, C) - L) / rng, np.nan)
            f["cs_close_pos"] = np.where(ok, (C - L) / rng, np.nan)
            hi12 = pd.DataFrame(H.T).rolling(12, min_periods=12).max().to_numpy().T
            lo12 = pd.DataFrame(L.T).rolling(12, min_periods=12).min().to_numpy().T
            f["cs_dist_high_12"] = (C / hi12 - 1) * 100
            f["cs_dist_low_12"] = (C / lo12 - 1) * 100
    if "volume" in groups:
        med = pd.DataFrame(V.T).rolling(window, min_periods=min_points).median().to_numpy().T
        s3 = pd.DataFrame(V.T).rolling(3, min_periods=3).sum().to_numpy().T
        prev3 = np.full_like(s3, np.nan); prev3[:, 3:] = s3[:, :-3]
        with np.errstate(all="ignore"):
            f["vol_rel_1"] = np.where(med > 0, V / med, np.nan)
            f["vol_rel_3"] = np.where(med > 0, s3 / (3 * med), np.nan)
            f["vol_accel_3"] = np.where(prev3 > 0, s3 / prev3, np.nan)
    if "pool_context" in groups:
        import warnings
        R3 = _ret(C, 3)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            d1, d3 = np.nanstd(R1, axis=0), np.nanstd(R3, axis=0)
            valid = np.sum(~np.isnan(R3), axis=0)
            breadth = np.where(valid > 0, np.sum(R3 > 0, axis=0) / np.maximum(valid, 1), np.nan)
        f["pool_disp_1"] = np.broadcast_to(d1, C.shape).copy()
        f["pool_disp_3"] = np.broadcast_to(d3, C.shape).copy()
        f["pool_breadth_3"] = np.broadcast_to(breadth, C.shape).copy()
        f["pool_rank_3"] = pd.DataFrame(R3).rank(axis=0, pct=True).to_numpy()


def build_frame(closes: Dict[str, Dict[int, float]], cfg: Dict[str, Any], *,
                horizons_candles: Sequence[int] = (), bars: Optional[Dict[str, Dict[int, Sequence[float]]]] = None,
                all_groups: bool = False) -> Dict[str, Any]:
    """Features (and, for training, labels) on the regular candle grid.

    Returns ``{syms, grid(open epochs), features{name:[S,T]}, labels{(k,mode):[S,T]},
    excess{k:[S,T]}, assets{k:[T]}}``. Value at column t = decision at the CLOSE of
    candle t (uses candles <= t). ``bars`` = {symbol: {open_epoch: (open, high, low,
    quote_volume)}} for the groups that need them; ``all_groups`` computes every group
    (ablation), otherwise only ``cfg['feature_groups']``."""
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
    groups = set(GROUPS) if all_groups else set(cfg.get("feature_groups") or [])
    if groups:
        _group_features(f, groups, C, R1, beta, syms, grid, cfg, bars)
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


def training_rows(frame: Dict[str, Any], cfg: Dict[str, Any], k: int, mode: str,
                  names: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Deterministic, outcome-blind subsample: at most ``max_rows_per_time`` assets per
    decision time (hash order), then evenly spaced times down to ``max_rows``.
    ``names`` overrides the stored columns (ablation keeps every group's columns)."""
    import numpy as np
    names = names or feature_names(cfg)
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


BARS_SQL = """SELECT DISTINCT ON (symbol, time) symbol, time, open::float8 AS o, high::float8 AS h,
       low::float8 AS l, close::float8 AS c, coalesce(quote_volume, volume)::float8 AS v FROM ohlcv
 WHERE symbol=ANY($1::text[]) AND timeframe=$2 AND market_type='spot' AND is_closed IS TRUE
  AND time>=$3 AND time<$4
 ORDER BY symbol, time, CASE WHEN exchange ILIKE 'gate%' THEN 0 ELSE 1 END"""


def needs_bars(cfg: Dict[str, Any], all_groups: bool = False) -> bool:
    return all_groups or bool(set(cfg.get("feature_groups") or []) & NEEDS_BARS)


async def load_bars(conn, symbols: List[str], timeframe: str, lo: datetime, hi: datetime, chunk: int = 10):
    """(closes, bars) from the same rows, Gate preferred (asyncpg)."""
    closes: Dict[str, Dict[int, float]] = {}
    bars: Dict[str, Dict[int, tuple]] = {}
    for i in range(0, len(symbols), chunk):
        for rec in await conn.fetch(BARS_SQL, symbols[i:i + chunk], timeframe, lo, hi):
            if rec["c"] is None:
                continue
            t = int(rec["time"].timestamp())
            closes.setdefault(rec["symbol"], {})[t] = float(rec["c"])
            bars.setdefault(rec["symbol"], {})[t] = (rec["o"], rec["h"], rec["l"], rec["v"])
    return closes, bars


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
    bars = None
    if needs_bars(cfg):
        closes, bars = await load_bars(conn, symbols, cfg["timeframe"], lo, hi)
    else:
        closes = await load_closes(conn, symbols, cfg["timeframe"], lo, hi)
    step = int(cfg["step_seconds"])
    ks = {int(h) // (step // 60): int(h) for h in cfg["horizons_minutes"]}
    frame = await asyncio.to_thread(build_frame, closes, cfg, horizons_candles=list(ks), bars=bars)
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


def paired_vs_base(base_wf: Dict[str, Any], wf: Dict[str, Any]) -> Dict[str, Any]:
    """Day-paired comparison on the SAME test days: per-day AUC difference vs the base,
    days better, one-sided sign test (variant > base)."""
    import numpy as np
    from .pump_directional_research import sign_test_p
    a = {f["day"]: f["auc"] for f in (base_wf.get("folds") or []) if "auc" in f}
    b = {f["day"]: f["auc"] for f in (wf.get("folds") or []) if "auc" in f}
    days = sorted(set(a) & set(b))
    if not days:
        return {"days": 0}
    d = np.array([b[x] - a[x] for x in days])
    better = int((d > 0).sum())
    return {"days": len(days), "median_auc_delta": float(np.median(d)), "mean_auc_delta": float(d.mean()),
            "days_better": better, "sign_test_p": sign_test_p(better, len(days))}


def _summary(wf: Dict[str, Any]) -> Dict[str, Any]:
    p = wf.get("pooled") or {}
    e = p.get("economic") or {}
    return {"day_auc_median": p.get("day_auc_median"), "days_auc_above_half": p.get("days_auc_above_half"),
            "scored_days": wf.get("scored_days"), "sign_test_p": p.get("sign_test_p"),
            "brier_improvement_day_mean": p.get("brier_improvement_day_mean"),
            "brier_ci95": p.get("paired_episode_brier_ci95"),
            "top_minus_bottom": e.get("top_minus_bottom"), "top_minus_bottom_ci95": e.get("top_minus_bottom_ci95")}


async def run_candle_ablation(conn, owner, c: Dict[str, Any], diagnostics: Dict[str, Any],
                              deadline: datetime) -> Dict[str, Any]:
    """Feature-group ablation (no model is saved or applied): the SAME rows and test days,
    base columns vs base + one group at a time (+ all groups), walk-forward each, then a
    day-paired comparison against the base. Variants left when the budget runs out are
    reported as ``runtime_budget_exhausted``."""
    import asyncio
    from .pump_directional_research import walk_forward_evaluation
    research = c["research"]; cand = research["candle"]; ab = cand["ablation"]
    cfg = {**cand, "max_rows": int(ab["max_rows"]),
           "walk_forward": {**research["walk_forward"], "max_folds": int(ab["max_folds"])}}
    symbols = sorted({r["symbol"] for r in await conn.fetch(UNIVERSE_SQL, owner)} | {REFERENCE})
    hi = datetime.now(timezone.utc)
    lo = hi - timedelta(days=int(cfg["lookback_days"]))
    closes, bars = await load_bars(conn, symbols, cfg["timeframe"], lo, hi)
    step = int(cfg["step_seconds"]); h = int(ab["horizon_minutes"]); k = h // (step // 60)
    frame = await asyncio.to_thread(build_frame, closes, cfg, horizons_candles=[k], bars=bars, all_groups=True)
    rows = training_rows(frame, cfg, k, cfg["label_mode"], names=all_feature_names(cfg))
    diagnostics["candle_ablation"] = {"symbols": len(symbols), "rows": len(rows), "horizon_minutes": h}
    spec = {"params": research["params"], "max_threads": 1, "embargo_seconds": int(cfg["embargo_seconds"]),
            "walk_forward": cfg["walk_forward"]}
    options = {k2: research[k2] for k2 in ("calibration_C", "calibration_max_iter", "calibration_method",
                                           "calibration_max_slope", "bootstrap_repetitions")}
    variants = [("base", [])] + [(g, [g]) for g in ab["groups"]]
    if ab.get("include_all") and len(ab["groups"]) > 1:
        variants.append(("all", list(ab["groups"])))
    out: Dict[str, Any] = {"horizon_minutes": h, "rows": len(rows), "max_folds": int(ab["max_folds"]),
                           "variants": {}}
    base_wf = None
    for name, groups in variants:
        if (deadline - datetime.now(timezone.utc)).total_seconds() < 60:
            out["variants"][name] = {"blocked_reason": "runtime_budget_exhausted"}
            continue
        cols = feature_names({**cfg, "feature_groups": groups})
        t0 = datetime.now(timezone.utc)
        wf = await asyncio.wait_for(asyncio.to_thread(
            walk_forward_evaluation, rows, columns=cols, spec=spec, options=options, relative=False),
            timeout=max(1, (deadline - datetime.now(timezone.utc)).total_seconds()))
        item = {"features": len(cols), "seconds": round((datetime.now(timezone.utc) - t0).total_seconds(), 1),
                **_summary(wf)}
        if name == "base":
            base_wf = wf
        elif base_wf is not None:
            item["vs_base"] = paired_vs_base(base_wf, wf)
        out["variants"][name] = item
    return out


_LIVE_BARS = """
    SELECT DISTINCT ON (symbol, time) symbol, time, open, high, low, close, coalesce(quote_volume, volume) AS v
      FROM ohlcv
     WHERE symbol = ANY(CAST(:s AS text[])) AND timeframe = :tf AND market_type = 'spot'
       AND is_closed IS TRUE AND time >= :lo AND time < :hi
     ORDER BY symbol, time, CASE WHEN exchange ILIKE 'gate%' THEN 0 ELSE 1 END
"""


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
    closes: Dict[str, Dict[int, float]] = {}
    bars: Optional[Dict[str, Dict[int, tuple]]] = None
    if needs_bars(cfg):
        bars = {}
        rows = (await db.execute(text(_LIVE_BARS), {"s": syms, "tf": cfg["timeframe"], "lo": lo, "hi": hi})).all()
        for sym, t, o, h, l, close, v in rows:
            if close is not None:
                closes.setdefault(sym, {})[int(t.timestamp())] = float(close)
                bars.setdefault(sym, {})[int(t.timestamp())] = tuple(None if x is None else float(x) for x in (o, h, l, v))
    else:
        rows = (await db.execute(text(_LIVE_CANDLES), {"s": syms, "tf": cfg["timeframe"], "lo": lo, "hi": hi})).all()
        for sym, t, close in rows:
            if close is not None:
                closes.setdefault(sym, {})[int(t.timestamp())] = float(close)
    frame = build_frame(closes, cfg, bars=bars)
    cells = live_cells(frame, cfg, now_s)
    return {s: cells[s] for s in symbols if s in cells}
