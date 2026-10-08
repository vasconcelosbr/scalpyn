"""Pump ML relative objective (2026-10-07): label = asset endpoint return above the
same-minute universe median; regime effect disabled; objectives never mixed."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from app.services import pump_ml_inference as inf
from app.services import pump_ml_selection as sel
from app.services import pump_opportunity_engine as eng
from app.services import pump_score_v1 as v1
from app.services.pump_directional_research import (RELATIVE_OBJECTIVE, mid_return, relative_target,
                                                    target_contract, train_directional)
from tests.test_pump_directional_research import fixture


def row(own, bench, n=40, status="known", complete=True, spread=0.0):
    return {"endpoint_return_pct": own, "benchmark_return_pct": bench, "benchmark_assets": n,
            "label_status": status, "label_coverage_complete": complete, "values": {"spread_pct": spread}}


def test_relative_target_beats_the_market_not_the_zero_line():
    assert relative_target(row(-0.10, -0.40), 10) is True     # fell, but less than the market → above
    assert relative_target(row(+0.20, +0.60), 10) is False    # rose, but less than the market → below
    assert relative_target(row(0.30, 0.30), 10) is None       # exact tie → excluded
    assert relative_target(row(0.30, 0.10, n=5), 10) is None  # thin cross-section → excluded
    assert relative_target(row(0.30, None), 10) is None
    assert relative_target(row(0.30, 0.10, status="pending"), 10) is None
    assert relative_target(row(0.30, 0.10, complete=False), 10) is None
    # Ask-referenced −0.30 with a 0.8 % spread is −0.30 + ~0.40 ≈ +0.10 from mid:
    # above a flat market. Without the mid correction illiquidity alone reads "below".
    assert relative_target(row(-0.30, 0.0, spread=0.8), 10) is True
    assert relative_target(row(-0.30, 0.0, spread=None), 10) is None
    assert mid_return(-0.30, 0.8) == pytest.approx(((1 - 0.003) * 1.004 - 1) * 100)


def test_default_config_is_relative():
    r = eng.config(None)["research"]
    assert r["target_mode"] == "relative_universe_median" and r["relative_min_assets"] == 10
    assert r["relative_beta"] == {"enabled": True, "timeframe": "5m", "window_candles": 288, "min_points": 200,
                                  "prev_candles": 3}
    with pytest.raises(ValueError):
        eng.config({"research": {"relative_beta": {"enabled": True, "timeframe": "5m", "window_candles": 288,
                                                   "min_points": 500}}})
    assert v1.DEFAULT_V1["ml"]["objective"] == "relative_candle"
    assert v1.DEFAULT_V1["ml"]["score"]["max_adjust"] == 0.05
    with pytest.raises(ValueError):
        eng.config({"research": {"target_mode": "touch"}})
    errors = []
    bad = deepcopy(v1.DEFAULT_V1); bad["ml"]["objective"] = "touch"
    v1.validate(bad, errors)
    assert any("objective" in e for e in errors)


def test_relative_training_uses_benchmark_and_marks_objective(tmp_path):
    rows, spec = fixture()
    for i, r in enumerate(rows):
        # Every row moves with a shared market drift; the asset-specific part follows rsi.
        drift = 0.5 if (i // 40) % 2 else -0.5
        r["benchmark_return_pct"] = drift
        r["benchmark_assets"] = 40
        r["values"]["spread_pct"] = 0.0
        r["endpoint_return_pct"] = drift + (0.1 if r["values"]["rsi"] == 60 else -0.1)
    spec = {**spec, "directional_target": target_contract(10, True, 10)}
    assert spec["directional_target"]["benchmark_policy"] == "universe_beta_residual_median_mid_v1"
    result = train_directional(rows, spec=spec, output_root=tmp_path)
    m, manifest = result["metrics"], result["manifest"]
    assert manifest["objective"] == RELATIVE_OBJECTIVE
    assert manifest["probability_event"] == "endpoint_return_above_same_minute_universe_median"
    # rsi alone separates above/below the market; the shared drift no longer dominates.
    assert m["episode_weighted_test"]["auc"] > 0.9
    assert all(0.4 <= f <= 0.6 for f in m["cohort_up_frequency"])
    bad = {**spec, "directional_target": {**spec["directional_target"], "benchmark_policy": "btc"}}
    with pytest.raises(ValueError):
        train_directional(rows, spec=bad, output_root=tmp_path / "x")


def test_rolling_beta_is_point_in_time_and_recovers_slope():
    import numpy as np
    rng = np.random.default_rng(3)
    step, n = 300, 400
    t0 = 1_790_000_000
    mkt = rng.normal(scale=0.3, size=n)
    closes = {}
    for sym, b in (("HI_USDT", 2.0), ("LO_USDT", 0.2), ("M1_USDT", 1.0), ("M2_USDT", 1.0)):
        p, series = 100.0, {}
        for i in range(n):
            p *= 1 + (b * mkt[i] + rng.normal(scale=0.02)) / 100
            series[t0 + i * step] = p
        closes[sym] = series
    dec = t0 + 350 * step
    betas = sel.rolling_betas(closes, {("HI_USDT", dec), ("LO_USDT", dec)}, step_seconds=step, window=288,
                              min_points=200)
    assert betas[("HI_USDT", dec)] > betas[("LO_USDT", dec)]
    # Future candles must not change a past beta.
    future = {s: {t: (c * 3 if t > dec else c) for t, c in v.items()} for s, v in closes.items()}
    again = sel.rolling_betas(future, {("HI_USDT", dec)}, step_seconds=step, window=288, min_points=200)
    assert again[("HI_USDT", dec)] == pytest.approx(betas[("HI_USDT", dec)])
    few = sel.rolling_betas(closes, {("HI_USDT", t0 + 50 * step)}, step_seconds=step, window=288, min_points=200)
    assert few == {}


def test_beta_residual_benchmark_removes_market_direction():
    """In a −3 % market minute a low-beta asset no longer 'beats the market' for free."""
    t0 = datetime(2026, 10, 7, 1, 50, tzinfo=timezone.utc)
    cross = [("HI_USDT", -6.0), ("M1_USDT", -3.0), ("M2_USDT", -2.9), ("LO_USDT", -0.4), ("X_USDT", -3.1)]
    betas = {"HI_USDT": 2.0, "M1_USDT": 1.0, "M2_USDT": 1.0, "LO_USDT": 0.2, "X_USDT": 1.0}

    class Conn:
        async def fetch(self, sql, *args):
            if "FROM ohlcv" in sql:
                return []
            return [{"slot_at": t0, "symbol": s, "r": r} for s, r in cross]

    rows = [{"slot_at": t0.isoformat(), "symbol": "LO_USDT", "endpoint_return_pct": -0.4,
             "label_status": "known", "label_coverage_complete": True, "values": {"spread_pct": 0.0}},
            {"slot_at": t0.isoformat(), "symbol": "M2_USDT", "endpoint_return_pct": -2.9,
             "label_status": "known", "label_coverage_complete": True, "values": {"spread_pct": 0.0}}]
    diag = {}
    import app.services.pump_ml_selection as mod
    orig = mod.relative_features
    mod.relative_features = lambda closes, needed, **kw: {(s, int(t0.timestamp())): {"beta": betas[s], "r_prev": None}
                                                          for s, _ in cross}
    try:
        asyncio.run(sel.attach_universe_benchmark(Conn(), "u", rows, 15, "h", t0, diag,
                                                  beta={"timeframe": "5m", "window_candles": 288, "min_points": 200}))
    finally:
        mod.relative_features = orig
    # plain median = −3.0; residuals: HI 0, M1 0, M2 +0.1, LO +0.2, X −0.1 → median 0
    lo, m2 = rows
    assert lo["beta"] == 0.2 and lo["benchmark_assets"] == 5
    assert lo["benchmark_return_pct"] == pytest.approx(0.2 * -3.0 + 0.0)
    assert relative_target(lo, 3) is True and relative_target(m2, 3) is True
    # Without beta the low-beta asset wins by +2.6 pp purely because the market fell.
    plain = [dict(r) for r in rows]
    asyncio.run(sel.attach_universe_benchmark(Conn(), "u", plain, 15, "h", t0, {}))
    assert plain[0]["benchmark_return_pct"] == pytest.approx(-3.0)
    assert diag["benchmark"]["policy"] == "universe_beta_residual_median_mid_v1"
    assert "spread_pct" in sel.CROSS_SECTION_SQL and "labeled_at<=$5" in sel.CROSS_SECTION_SQL
    assert "'symbol',o.symbol" in sel.DIRECTIONAL_ROWS_SQL


def test_relative_ml_never_caps_the_regime():
    rows = [{"symbol": f"S{i}_USDT", "indicators": {}} for i in range(12)]
    payload = {"active": True, "probabilities": {r["symbol"]: 0.30 for r in rows}}
    rel = v1.evaluate_universe(rows, {}, None, {}, minute_ms=60_000, spec=deepcopy(v1.DEFAULT_V1), ml=payload)
    assert rel["regime"]["ml"]["cap"] is None
    assert rel["regime"]["ml"]["regime_effect"] == "disabled_relative_objective"
    absolute = deepcopy(v1.DEFAULT_V1); absolute["ml"]["objective"] = "absolute"
    out = v1.evaluate_universe(rows, {}, None, {}, minute_ms=60_000, spec=absolute, ml=payload)
    assert out["regime"]["ml"]["cap"] == "desfavoravel"


def test_inference_loads_only_the_configured_objective(monkeypatch):
    asked = []

    async def newest(db, user_id, horizon, max_age_days, **kw):
        asked.append(kw.get("objective"))
        return None, {}

    monkeypatch.setattr(inf, "_fetch_newest", newest)
    inf._CACHE.clear()
    out = asyncio.run(inf.load_model(None, "u", deepcopy(v1.DEFAULT_V1), now=1.0))
    assert out["active"] is False and out["reason"] == "no_recent_model"
    assert asked == [inf.CANDLE_OBJECTIVE]       # default objective since 2026-10-08
    relative = deepcopy(v1.DEFAULT_V1); relative["ml"]["objective"] = "relative"
    inf._CACHE.clear()
    asyncio.run(inf.load_model(None, "u", relative, now=1.0))
    assert asked[-1] == RELATIVE_OBJECTIVE
    absolute = deepcopy(v1.DEFAULT_V1); absolute["ml"]["objective"] = "absolute"
    asyncio.run(inf.load_model(None, "u", absolute, now=1.0))
    assert asked[-1] == inf.OBJECTIVE
