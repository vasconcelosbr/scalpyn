"""Pump ML v1.7 (2026-10-07): optional v1/perp/regime/capital context features,
bounded calibration on a pooled block, and per-horizon model metrics."""
import asyncio
import json
from copy import deepcopy
from datetime import datetime, timezone

import numpy as np
import pytest

from app.services import pump_ml_inference as inf
from app.services import pump_monitor_service as svc
from app.services import pump_opportunity_engine as eng
from app.services import pump_score_v1 as v1
from app.services.pump_contracts import CONTEXT_FEATURE_SPEC, validate_manifest
from app.services.pump_directional_research import (apply_calibration, fit_calibration, load_directional_preview,
                                                    model_columns, train_directional)
from tests.test_pump_directional_research import fixture

OPTS = {"calibration_C": 1.0, "calibration_max_iter": 1000, "calibration_method": "platt_bounded",
        "calibration_pool": "validation_and_calibration", "calibration_max_slope": 1.0}


def context_fixture(present_from=160):
    """Context exists only in the newer rows, like production (captured after deploy)."""
    rows, spec = fixture()
    for i, r in enumerate(rows):
        if i >= present_from:
            r["values"]["ctx_capital_ratio"] = 0.05 if i % 4 in (0, 3) else -0.05
            r["values"]["perp_funding_rate"] = 0.0001 * (i % 3)
    spec = {**spec, "context_features": ["ctx_capital_ratio", "perp_funding_rate", "v1_wick"],
            "context_feature_spec_hash": eng.canonical_hash(CONTEXT_FEATURE_SPEC),
            "directional_evaluation": {**spec["directional_evaluation"], **OPTS, "bootstrap_repetitions": 20,
                                       "reliability_bins": 5}}
    return rows, spec


def test_default_config_has_valid_context_and_calibration():
    c = eng.config(None)
    r = c["research"]
    assert set(r["context_features"]) <= set(CONTEXT_FEATURE_SPEC["fields"])
    assert r["calibration_method"] == "platt_bounded" and r["calibration_pool"] == "validation_and_calibration"
    assert r["cohort_cuts"] == [0.5, 0.65, 0.8] and r["lookback_days"] == 30
    for bad in ({"cohort_cuts": [0.5, 0.4, 0.8]}, {"cohort_cuts": [0.1, 0.5, 0.8]},
                {"calibration_method": "isotonic"}, {"lookback_days": 0},
                {"context_features": ["rsi"]}, {"context_features": ["not_a_field"]},
                {"calibration_max_slope": 0}):
        with pytest.raises(ValueError):
            eng.config({"research": bad})


def test_context_dictionary_is_separate_from_frozen_feature_hash():
    """Historical observations keep matching: FEATURE_SPEC (and its hash) is unchanged."""
    from app.services.pump_contracts import FEATURE_SPEC
    assert not set(CONTEXT_FEATURE_SPEC["fields"]) & set(FEATURE_SPEC["fields"])
    # Hash every stored observation manifest carries (unchanged since v1).
    assert eng.canonical_hash(FEATURE_SPEC) == "37c1e680ae25ae5fe73790ea385aba5be8cb17556dfa98caf07ee35c44d2d72c"
    _, spec = context_fixture()
    with pytest.raises(ValueError):
        validate_manifest({**spec, "context_feature_spec_hash": "stale"})


def test_bounded_calibration_clips_amplifying_and_inverted_slopes():
    rng = np.random.default_rng(1)
    z = rng.normal(size=400)
    y = (z + rng.normal(scale=0.2, size=400) > 0).astype(int)  # strong signal → unbounded slope ≫ 1
    w = np.ones(400)
    free = fit_calibration(z, y, w, {**OPTS, "calibration_method": "platt"}, 1)
    bounded = fit_calibration(z, y, w, OPTS, 1)
    assert free["slope"] > 1 and bounded["slope"] == 1.0 and bounded["bounded"] is True
    assert bounded["unbounded_slope"] == pytest.approx(free["slope"])
    inverted = fit_calibration(z, 1 - y, w, OPTS, 1)
    assert inverted["slope"] == 0.0
    # slope 0 → constant probability equal to the weighted base rate of the pool
    p = apply_calibration(np.array([0.1, 0.9]), inverted)
    assert p[0] == pytest.approx(p[1]) and p[0] == pytest.approx((1 - y).mean(), abs=1e-6)


def test_training_with_partial_context_reports_coverage_and_round_trips(tmp_path):
    rows, spec = context_fixture(present_from=60)  # starts inside the train block
    result = train_directional(rows, spec=spec, output_root=tmp_path)
    m, manifest = result["metrics"], result["manifest"]
    assert model_columns(manifest["spec"]) == ["rsi", "adx", "ctx_capital_ratio", "perp_funding_rate", "v1_wick"]
    assert m["context_coverage"]["train"]["ctx_capital_ratio"] < m["context_coverage"]["test"]["ctx_capital_ratio"]
    assert m["context_coverage"]["test"]["v1_wick"] == 0.0
    assert manifest["feature_bounds"]["v1_wick"] is None                       # never observed → no bound
    assert set(m["feature_importance_gain"]) == set(model_columns(manifest["spec"]))
    assert m["calibration"]["pool"] == "validation_and_calibration"
    assert m["calibration"]["rows"] == m["cohort_rows"][1] + m["cohort_rows"][2]
    assert len(m["cohort_up_frequency"]) == 4
    folder = tmp_path / manifest["artifact_namespace"]
    model = inf.DirectionalModel("e", manifest, m, (folder / "xgboost.json").read_bytes(),
                                 json.loads((folder / "calibrator.json").read_text()), "now")
    assert model.uses_context
    newest = rows[-1]["values"]
    cells = {f: {"value": v} for f, v in newest.items()}
    vec = model.vector(cells, 120)
    assert np.isnan(vec[-1])                                                    # v1_wick missing → NaN, not abstain
    ref = load_directional_preview(newest, folder)["research_only"]["estimated_up_probability"]
    assert model.predict([vec])[0] == pytest.approx(ref, abs=1e-6)
    no_core = {k: c for k, c in cells.items() if k != "rsi"}
    assert model.vector(no_core, 120) is None                                   # core missing → abstain
    far = {**cells, "ctx_capital_ratio": {"value": 5.0}}
    assert model.vector(far, 120) is None                                       # present but out of range
    stale = {**cells, "ctx_capital_ratio": {"value": 0.05, "age_seconds": 999}}
    assert np.isnan(model.vector(stale, 120)[2])


def test_legacy_models_without_context_keep_working(tmp_path):
    rows, spec = fixture()
    result = train_directional(rows, spec=spec, output_root=tmp_path)
    folder = tmp_path / result["manifest"]["artifact_namespace"]
    cal = json.loads((folder / "calibrator.json").read_text())
    assert cal["method"] == "platt" and result["metrics"]["calibration"]["pool"] == "calibration"
    model = inf.DirectionalModel("e", result["manifest"], result["metrics"], (folder / "xgboost.json").read_bytes(),
                                 cal, "now")
    assert not model.uses_context and model.features == ["rsi", "adx"]


def test_context_cells_use_price_regime_and_raw_capital_never_ml():
    spec = deepcopy(v1.DEFAULT_V1)
    out = {"regime": {"breadth": 0.6, "reference_progress_atr": 0.4, "reference_ret_pct": 0.2,
                      "state": "desfavoravel", "price_state": "favoravel",
                      "capital": {"ratio": -0.03, "z": float("nan")},
                      "ml": {"active": True, "mean_up_probability": 0.3, "cap": "desfavoravel"}},
           "results": {"A_USDT": {"values": {"wick": 0.2}, "derivatives": {"funding_rate": 0.0001},
                                  "ml_up_probability": 0.9, "score": 80}}}
    cells = svc.context_cells(out, spec)["A_USDT"]
    assert cells["ctx_breadth"]["value"] == 0.6 and cells["ctx_capital_ratio"]["value"] == -0.03
    assert cells["ctx_capital_z"]["value"] is None and cells["ctx_capital_z"]["reason"] == "regime_unavailable"
    assert cells["v1_wick"]["value"] == 0.2 and cells["perp_funding_rate"]["value"] == 0.0001
    assert not any("ml" in k or "score" in k for k in cells)
    assert set(cells) <= set(CONTEXT_FEATURE_SPEC["fields"])


def test_apply_engines_writes_the_same_context_cells_observations_capture():
    spec = deepcopy(v1.DEFAULT_V1)
    out = {"regime": {"breadth": 0.5, "capital": {"ratio": 0.01, "z": 1.2}},
           "results": {"A_USDT": {"values": {"wick": 0.1}, "derivatives": {}, "score": 10, "reason": None,
                                  "state": "observando", "condition": "subindo"}}}
    rows = [{"symbol": "A_USDT", "indicators": {}}]
    svc.apply_engines(rows, out, "v1", spec)
    expected = svc.context_cells(out, spec)["A_USDT"]
    assert {k: rows[0]["indicators"][k] for k in expected} == expected


def test_models_summary_reports_every_horizon(monkeypatch):
    created = datetime(2026, 10, 7, 15, tzinfo=timezone.utc)
    good = {"episode_weighted_test": {"auc": 0.6, "brier": 0.2, "baseline_brier": 0.25,
                                      "calibration_prior_brier": 0.24, "observed_up_frequency": 0.4,
                                      "baseline_train_up_frequency": 0.5, "baseline_calibration_up_frequency": 0.45},
            "paired_episode_brier_ci95": [0.01, 0.05], "cohort_episodes": [300, 90, 80, 103],
            "cohort_rows": [2000, 600, 500, 700]}
    bad = deepcopy(good); bad["episode_weighted_test"]["auc"] = 0.52

    async def newest(db, user_id, horizon, max_age_days, **_):
        if horizon == 30:
            return None, {}
        return {"experiment_id": f"e{horizon}", "created_at": created,
                "manifest": {"spec": {"features": ["rsi"], "context_features": []}},
                "metrics": good if horizon == 10 else bad}, {}

    monkeypatch.setattr(inf, "_fetch_newest", newest)
    spec = deepcopy(v1.DEFAULT_V1)
    spec["ml"]["training"]["horizons_minutes"] = [10, 15, 30]
    out = asyncio.run(inf.models_summary(None, "u", spec))
    by_h = {m["horizon_minutes"]: m for m in out["models"] if m["family"] == "observation"}
    assert out["applied_horizon_minutes"] == 15 and by_h[15]["applied"] is True
    assert by_h[10]["quality"]["approved"] is True and by_h[15]["quality"]["approved"] is False
    assert by_h[30]["status"] == "no_recent_model"
    assert by_h[10]["diagnostics"]["test_up_frequency"] == 0.4
