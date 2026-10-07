"""Pump ML walk-forward evaluation (2026-10-07): each UTC day is tested once with a
model trained only on the past; the quality gate reads it; economic spread reported."""
from copy import deepcopy

import pytest

from app.services import pump_opportunity_engine as eng
from app.services import pump_score_v1 as v1
from app.services.pump_directional_research import train_directional, walk_forward_evaluation, model_columns
from tests.test_pump_directional_research import fixture

WF = {"enabled": True, "min_train_days": 2, "calibration_fraction": 0.2, "economic_quantile": 0.1}


def wf_fixture():
    rows, spec = fixture()
    spec = {**spec, "walk_forward": WF,
            "directional_evaluation": {**spec["directional_evaluation"], "calibration_method": "platt_bounded",
                                       "calibration_pool": "validation_and_calibration",
                                       "calibration_max_slope": 1.0}}
    return rows, spec


def test_every_day_after_the_first_two_is_a_fold_and_never_sees_its_future(tmp_path):
    rows, spec = wf_fixture()
    result = train_directional(rows, spec=spec, output_root=tmp_path)
    wf = result["metrics"]["walk_forward"]
    days = sorted({eng.utc(r["decision_at"]).date() for r in rows})
    assert [f["day"] for f in wf["folds"]] == [d.isoformat() for d in days[2:]]
    pooled = wf["pooled"]
    assert pooled["rows"] == sum(f["test_rows"] for f in wf["folds"] if "auc" in f)
    assert 0 <= pooled["auc"] <= 1 and len(pooled["paired_episode_brier_ci95"]) == 2
    assert pooled["days_auc_above_half"] <= wf["scored_days"]
    eco = pooled["economic"]
    assert eco["top_rows"] > 0 and eco["bottom_rows"] > 0 and "top_minus_bottom_ci95" in eco


def test_fold_training_is_past_only():
    rows, spec = wf_fixture()
    usable = [{**r, "target": r["endpoint_return_pct"] > 0} for r in rows]
    seen = {}
    import app.services.pump_directional_research as mod
    real = mod.fit_calibration

    def spy(z, y, w, options, seed):
        seen.setdefault("calls", 0)
        seen["calls"] += 1
        return real(z, y, w, options, seed)

    mod.fit_calibration = spy
    try:
        wf = walk_forward_evaluation(usable, columns=model_columns(spec), spec=spec,
                                     options=spec["directional_evaluation"], relative=False)
    finally:
        mod.fit_calibration = real
    scored = [f for f in wf["folds"] if "auc" in f]
    assert seen["calls"] == len(scored)
    for f in scored:   # fit + calibration rows all precede the test day by construction
        assert f["fit_rows"] + f["calibration_rows"] < len(usable)


def test_quality_gate_reads_walk_forward_when_present():
    spec = deepcopy(v1.DEFAULT_V1)
    holdout_good = {"episode_weighted_test": {"auc": 0.7, "brier": 0.2, "baseline_brier": 0.25,
                                              "calibration_prior_brier": 0.25},
                    "paired_episode_brier_ci95": [0.01, 0.02], "cohort_episodes": [1, 1, 1, 200]}
    wf_bad = {"walk_forward": {"scored_days": 4, "pooled": {"auc": 0.52, "brier": 0.249, "baseline_brier": 0.25,
                                                            "paired_episode_brier_ci95": [-0.003, 0.004],
                                                            "episodes": 900, "days_auc_above_half": 3}}}
    q = v1.ml_quality({**holdout_good, **wf_bad}, spec)
    assert q["source"] == "walk_forward" and not q["approved"]
    assert set(q["reasons"]) == {"auc_below_min", "brier_improvement_ci_includes_zero"}
    spec["ml"]["quality"]["source"] = "holdout"
    assert v1.ml_quality({**holdout_good, **wf_bad}, spec)["approved"] is True
    spec["ml"]["quality"]["source"] = "walk_forward"
    assert v1.ml_quality(holdout_good, spec)["approved"] is True       # legacy model: holdout fallback


def test_config_validation():
    assert eng.config(None)["research"]["walk_forward"] == WF
    with pytest.raises(ValueError):
        eng.config({"research": {"walk_forward": {**WF, "min_train_days": 0}}})
    bad = deepcopy(v1.DEFAULT_V1); bad["ml"]["quality"]["source"] = "x"
    errors = []
    v1.validate(bad, errors)
    assert errors
