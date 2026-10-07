"""Pump ML candle-history family (2026-10-07): point-in-time features from closed
candles, beta-residual relative label, train/live parity, family isolation."""
import asyncio
from datetime import datetime, timezone
from uuid import UUID

import numpy as np
import pytest

from app.services import pump_ml_candles as pc
from app.services import pump_ml_daily as daily
from app.services import pump_opportunity_engine as eng

CFG = {**eng.config(None)["research"]["candle"], "walk_forward": eng.config(None)["research"]["walk_forward"]}
STEP = 300
T0 = 1_790_000_100 - (1_790_000_100 % STEP)


def synthetic(n=2600, signal=0.0, seed=7):
    """BTC + 30 alts with betas; optional planted 1-candle relative reversal."""
    rng = np.random.default_rng(seed)
    mkt = rng.normal(scale=0.25, size=n)
    closes = {}
    betas = {"BTC_USDT": 1.0, **{f"A{i}_USDT": 0.4 + 0.07 * i for i in range(30)}}
    idio_prev = {s: 0.0 for s in betas}
    for s, b in betas.items():
        p, series = 100.0, {}
        for i in range(n):
            idio = rng.normal(scale=0.2) - signal * idio_prev[s]
            idio_prev[s] = idio
            p *= 1 + (b * mkt[i] + idio) / 100
            series[T0 + i * STEP] = p
        closes[s] = series
    return closes


def test_features_use_only_closed_candles_and_label_is_beta_residual():
    closes = synthetic(n=500)
    frame = pc.build_frame(closes, CFG, horizons_candles=[2])
    col = 400
    future = {s: {t: (c * 1.5 if t > int(frame["grid"][col]) else c) for t, c in v.items()} for s, v in closes.items()}
    later = pc.build_frame(future, CFG, horizons_candles=[2])
    for name, arr in frame["features"].items():
        np.testing.assert_allclose(arr[:, col], later["features"][name][:, col], equal_nan=True)
    # label at col depends on the future: shifting the future changes it, beta-residual median ~0
    lab = frame["labels"][(2, "endpoint")][:, col]
    assert np.nanmedian(lab) == pytest.approx(0.0, abs=1e-9)
    assert set(frame["features"]) == set(pc.feature_names(CFG))


def test_live_cells_equal_training_values_at_the_same_decision():
    closes = synthetic(n=500)
    frame = pc.build_frame(closes, CFG)
    col = 450
    now_s = int(frame["grid"][col]) + STEP          # decision at the close of candle col
    cells = pc.live_cells(frame, CFG, now_s + 120)  # any minute before the next close
    for name in pc.feature_names(CFG):
        v = frame["features"][name][3, col]
        got = cells[frame["syms"][3]][name]["value"]
        assert (got is None and not np.isfinite(v)) or got == pytest.approx(v)

    class DB:
        async def execute(self, stmt, binds):
            lo, hi = binds["lo"].timestamp(), binds["hi"].timestamp()
            data = [(s, datetime.fromtimestamp(t, timezone.utc), c) for s in binds["s"]
                    for t, c in closes.get(s, {}).items() if lo <= t < hi]
            return type("R", (), {"all": lambda self: data})()

    # The cross-section (market median) is the universe: the cycle passes every symbol.
    live = asyncio.run(pc.candle_context(DB(), list(frame["syms"]), CFG, now_s))
    v = frame["features"]["rel_resid_1"][3, col]
    assert live[frame["syms"][3]]["rel_resid_1"]["value"] == pytest.approx(v, rel=1e-6, abs=1e-9)


def test_training_rows_cap_and_planted_signal_is_found(tmp_path):
    closes = synthetic(n=3200, signal=0.6)        # strong 1-candle reversal of the idiosyncratic move
    frame = pc.build_frame(closes, CFG, horizons_candles=[2])
    rows = pc.training_rows(frame, {**CFG, "max_rows": 6000}, 2, "endpoint")
    per_time = {}
    for r in rows: per_time[r["episode_id"]] = per_time.get(r["episode_id"], 0) + 1
    assert max(per_time.values()) <= CFG["max_rows_per_time"]
    opts = {"calibration_C": 1.0, "calibration_max_iter": 1000, "calibration_method": "platt_bounded",
            "calibration_max_slope": 1.0, "bootstrap_repetitions": 50}
    params = {"n_estimators": 60, "max_depth": 3, "random_state": 20261001}
    result = pc.train_candle_model(rows, cfg={**CFG, "label_mode": "endpoint"}, horizon_minutes=10, options=opts,
                                   params=params, output_root=tmp_path,
                                   compare={"path_mean": pc.training_rows(frame, {**CFG, "max_rows": 6000}, 2, "path_mean")})
    wf = result["metrics"]["walk_forward"]["pooled"]
    assert wf["auc"] > 0.55                          # planted reversal is learnable out-of-sample
    assert set(result["metrics"]["label_variants"]) == {"endpoint", "path_mean"}
    m = result["manifest"]
    assert m["artifact_namespace"].startswith("pump_ml/candle/") and m["objective"] == pc.CANDLE_OBJECTIVE
    assert m["spec"]["directional_target"]["horizon_minutes"] == 10
    folder = tmp_path / m["artifact_namespace"]
    assert {p.name for p in folder.iterdir()} == {"xgboost.json", "calibrator.json", "manifest.json", "metrics.json"}


def test_family_has_its_own_daily_rule_and_lock():
    calls = []

    class Conn:
        async def fetchval(self, sql, *args):
            calls.append((sql, args))
            if "pg_try_advisory_lock" in sql: return True
            if "SELECT run_id" in sql: return "prior"
            return None
        async def execute(self, sql, *args): calls.append((sql, args))

    out = asyncio.run(daily.run_owner(Conn(), UUID(int=1), horizons=[10], family="candle"))
    assert out["status"] == "daily_already_recorded"
    lock_args = [a for q, a in calls if "pg_try_advisory_lock" in q][0]
    assert lock_args[0] == f"pump_ml:candle:{UUID(int=1)}"
    prior_sql, prior_args = [(q, a) for q, a in calls if "SELECT run_id" in q][0]
    assert "coalesce(payload->>'family','observation')" in prior_sql and "candle" in prior_args
    with pytest.raises(ValueError):
        asyncio.run(daily.run_owner(Conn(), UUID(int=1), horizons=[10], family="x"))


def test_config_validation():
    with pytest.raises(ValueError):
        eng.config({"research": {"candle": {**eng.config(None)["research"]["candle"], "horizons_minutes": [7]}}})
    with pytest.raises(ValueError):
        eng.config({"research": {"candle": {**eng.config(None)["research"]["candle"], "embargo_seconds": 60}}})
