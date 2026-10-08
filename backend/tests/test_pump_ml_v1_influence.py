"""Pump ML (v1.6): quality gate, bounded influence on v1, inference round-trip,
Celery daily runner storing artifacts under the ``pump_ml/%`` namespace."""
import asyncio
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest

from app.services import pump_score_v1 as v1
from app.services import pump_ml_inference as inf
from app.services import pump_ml_daily as daily
from app.services.pump_directional_research import train_directional
from tests.test_pump_directional_research import fixture


def spec(**ml):
    s = deepcopy(v1.DEFAULT_V1)
    s["ml"].update(ml)
    return s


GOOD = {"episode_weighted_test": {"auc": 0.62, "brier": 0.22, "baseline_brier": 0.25,
                                  "calibration_prior_brier": 0.249},
        "paired_episode_brier_ci95": [0.004, 0.03], "cohort_episodes": [400, 120, 90, 80]}


def test_default_spec_validates():
    errors = []
    v1.validate(v1.DEFAULT_V1, errors)
    assert errors == []


def test_quality_gate():
    assert v1.ml_quality(GOOD, spec())["approved"] is True
    bad_auc = deepcopy(GOOD); bad_auc["episode_weighted_test"]["auc"] = 0.51
    assert "auc_below_min" in v1.ml_quality(bad_auc, spec())["reasons"]
    no_gain = deepcopy(GOOD); no_gain["episode_weighted_test"]["brier"] = 0.26
    assert "brier_not_better_than_base_rate" in v1.ml_quality(no_gain, spec())["reasons"]
    ci_zero = deepcopy(GOOD); ci_zero["paired_episode_brier_ci95"] = [-0.001, 0.02]
    assert "brier_improvement_ci_includes_zero" in v1.ml_quality(ci_zero, spec())["reasons"]
    relaxed = spec(quality={**v1.DEFAULT_V1["ml"]["quality"], "require_ci_positive": False})
    assert v1.ml_quality(ci_zero, relaxed)["approved"] is True
    few = deepcopy(GOOD); few["cohort_episodes"] = [10, 10, 10, 5]
    assert "too_few_test_episodes" in v1.ml_quality(few, spec())["reasons"]
    assert v1.ml_quality(None, spec())["approved"] is False


def test_score_multiplier_is_bounded_and_neutral_at_half():
    s = spec()
    assert v1.ml_score_multiplier(0.5, s) == 1.0
    k = s["ml"]["score"]["max_adjust"]          # 0.05 by default since 2026-10-08
    assert v1.ml_score_multiplier(1.0, s) == pytest.approx(1 + k)
    assert v1.ml_score_multiplier(0.0, s) == pytest.approx(1 - k)
    s["ml"]["score"]["max_adjust"] = 0.15
    assert v1.ml_score_multiplier(1.0, s) == pytest.approx(1.15)
    assert v1.ml_score_multiplier(None, s) == 1.0


def test_direction_confirm_and_contradict_never_flip():
    s = spec()
    up = {"direction": "long", "strength": "normal", "reason": "so_spot"}
    assert v1.ml_direction(up, 0.7, s)["strength"] == "forte"
    assert v1.ml_direction({**up, "strength": "forte"}, 0.3, s)["strength"] == "normal"
    assert v1.ml_direction(up, 0.3, s)["direction"] == "long"
    down = {"direction": "short", "strength": "normal", "reason": "so_spot"}
    assert v1.ml_direction(down, 0.3, s)["strength"] == "forte"
    neutral = {"direction": "neutral", "strength": None, "reason": "sem_tendencia"}
    assert v1.ml_direction(neutral, 0.9, s) == neutral


def test_regime_cap_from_universe_mean():
    s = spec()
    assert v1.ml_regime_cap({str(i): 0.38 for i in range(12)}, s)["cap"] == "desfavoravel"
    assert v1.ml_regime_cap({str(i): 0.43 for i in range(12)}, s)["cap"] == "neutro"
    assert v1.ml_regime_cap({str(i): 0.55 for i in range(12)}, s)["cap"] is None
    assert v1.ml_regime_cap({str(i): 0.30 for i in range(3)}, s)["cap"] is None  # too few assets


def test_inactive_ml_has_zero_effect():
    rows = [{"symbol": "NEAR_USDT", "indicators": {}}]
    a = v1.evaluate_universe(rows, {}, None, {}, minute_ms=60_000, spec=spec())
    b = v1.evaluate_universe(rows, {}, None, {}, minute_ms=60_000, spec=spec(),
                             ml={"active": False, "probabilities": {"NEAR_USDT": 0.99}})
    assert a["results"]["NEAR_USDT"]["ml_up_probability"] is None
    assert b["results"]["NEAR_USDT"]["ml_up_probability"] is None
    assert b["regime"]["ml"]["active"] is False


def test_active_ml_caps_regime_and_reports_probability():
    rows = [{"symbol": f"S{i}_USDT", "indicators": {}} for i in range(12)]
    out = v1.evaluate_universe(rows, {}, None, {}, minute_ms=60_000, spec=spec(objective="absolute"),
                               ml={"active": True, "probabilities": {r["symbol"]: 0.35 for r in rows}})
    assert out["regime"]["ml"]["cap"] == "desfavoravel"
    assert out["results"]["S0_USDT"]["ml_up_probability"] == 0.35


def _trained(tmp_path):
    rows, s = fixture()
    result = train_directional(rows, spec=s, output_root=tmp_path)
    folder = tmp_path / result["manifest"]["artifact_namespace"]
    return rows, result, folder


def test_inference_round_trip_matches_research_preview(tmp_path):
    from app.services.pump_directional_research import load_directional_preview
    rows, result, folder = _trained(tmp_path)
    model = inf.DirectionalModel("e", result["manifest"], result["metrics"],
                                 (folder / "xgboost.json").read_bytes(),
                                 json.loads((folder / "calibrator.json").read_text()), "now")
    cells = {f: {"value": v} for f, v in rows[0]["values"].items()}
    vec = model.vector(cells, 120)
    p = model.predict([vec])[0]
    ref = load_directional_preview(rows[0]["values"], folder)["research_only"]["estimated_up_probability"]
    assert p == pytest.approx(ref, abs=1e-6) and 0 <= p <= 1
    assert model.vector({**cells, "rsi": {"value": 999}}, 120) is None             # outside training range
    assert model.vector({**cells, "rsi": {"value": 50, "age_seconds": 500}}, 120) is None  # stale
    assert model.vector({**cells, "rsi": {"value": None, "reason": "stale"}}, 120) is None


def test_predict_rows_only_when_active(tmp_path):
    rows, result, folder = _trained(tmp_path)
    model = inf.DirectionalModel("e", result["manifest"], result["metrics"],
                                 (folder / "xgboost.json").read_bytes(),
                                 json.loads((folder / "calibrator.json").read_text()), "now")
    universe = [{"symbol": "A_USDT", "indicators": {f: {"value": v} for f, v in rows[0]["values"].items()}},
                {"symbol": "B_USDT", "indicators": {}}]
    on = inf.predict_rows({"active": True, "predictor": model, "model": {}}, universe, spec())
    assert set(on["probabilities"]) == {"A_USDT"} and on["covered"] == 1
    off = inf.predict_rows({"active": False, "reason": "quality_gate_failed"}, universe, spec())
    assert off["probabilities"] == {} and off["active"] is False


def test_daily_runner_persists_artifacts_under_pump_ml_namespace(monkeypatch, tmp_path):
    """Regression for the 06/10 CheckViolationError: every stored path matches pump_ml/%."""
    rows, s = fixture()
    calls = []

    async def fake_horizons(conn, owner, c, diagnostics, staging, start, horizons):
        return [train_directional(rows, spec=s, output_root=staging)]

    monkeypatch.setattr(daily, "run_directional_horizons", fake_horizons)
    monkeypatch.setattr(daily, "config", lambda raw: {"enabled": True, "training_job_enabled": True,
                                                       "labels": {"horizons_minutes": [10, 15]},
                                                       "budget": {"max_storage_bytes": 10**10}})

    class Tx:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    class Conn:
        async def fetchval(self, sql, *args):
            if "pg_try_advisory_lock" in sql: return True
            if "SELECT run_id" in sql: return None
            if "SELECT config_json" in sql: return {}
            if "pg_total_relation_size" in sql: return 0
            return None

        async def execute(self, sql, *args):
            calls.append((sql, args))

        def transaction(self): return Tx()

    out = asyncio.run(daily.run_owner(Conn(), UUID(int=1), horizons=[10]))
    assert out["status"] == "challenger" and out["runner"] == "celery"
    namespaces = [a[4] for q, a in calls if "INSERT INTO pump_ml_experiments" in q]
    paths = [a[1] for q, a in calls if "INSERT INTO pump_ml_artifacts" in q]
    assert namespaces and all(n.startswith("pump_ml/") for n in namespaces)
    assert len(paths) == 4 and all(p.startswith("pump_ml/") for p in paths)


def test_load_model_gates_newest_and_caches(monkeypatch, tmp_path):
    rows, result, folder = _trained(tmp_path)
    created = datetime(2026, 10, 7, tzinfo=timezone.utc)
    files = {n: (folder / n).read_bytes() for n in ("xgboost.json", "calibrator.json")}
    calls = {"n": 0}

    async def fetch(db, user_id, horizon, max_age_days, **_):
        calls["n"] += 1
        return {"experiment_id": "x", "created_at": created, "manifest": result["manifest"],
                "metrics": metrics}, files

    monkeypatch.setattr(inf, "_fetch_newest", fetch)
    inf._CACHE.clear()
    metrics = deepcopy(GOOD)
    ok = asyncio.run(inf.load_model(None, "u1", spec(), now=1000.0))
    assert ok["active"] is True
    asyncio.run(inf.load_model(None, "u1", spec(), now=1100.0))
    assert calls["n"] == 1  # cached
    metrics = {**deepcopy(GOOD), "episode_weighted_test": {**GOOD["episode_weighted_test"], "auc": 0.5}}
    rejected = asyncio.run(inf.load_model(None, "u2", spec(), now=1000.0))
    assert rejected["active"] is False and rejected["reason"] == "quality_gate_failed"


def _skip_conn(prior_for):
    class Conn:
        def __init__(self): self.sql = []
        async def fetchval(self, sql, *args):
            self.sql.append(sql)
            if "pg_try_advisory_lock" in sql: return True
            if "SELECT run_id" in sql: return prior_for(sql)
            return None
        async def execute(self, sql, *args): self.sql.append(sql)
    return Conn()


def test_force_skips_daily_rule_but_not_recent_or_running_runs():
    # schedule: any run today blocks
    conn = _skip_conn(lambda sql: "x" if "date_trunc('day'" in sql else None)
    assert asyncio.run(daily.run_owner(conn, UUID(int=1), horizons=[10]))["status"] == "daily_already_recorded"
    # force: today's old run does not block, a recent/running one does
    conn = _skip_conn(lambda sql: "y" if "make_interval(mins=>$2)" in sql and "status='running'" in sql else None)
    out = asyncio.run(daily.run_owner(conn, UUID(int=1), horizons=[10], force=True))
    assert out["status"] == "recent_run_exists"
    assert not any("INSERT INTO pump_ml_job_runs" in q for q in conn.sql)



def test_manual_interval_comes_from_config():
    assert v1.DEFAULT_V1["ml"]["training"]["manual_min_interval_minutes"] == 5
    seen = []

    class Conn:
        async def fetchval(self, sql, *args):
            if "pg_try_advisory_lock" in sql: return True
            if "SELECT run_id" in sql:
                seen.append(args)
                return "z"
            return None
        async def execute(self, sql, *args): return None

    out = asyncio.run(daily.run_owner(Conn(), UUID(int=1), horizons=[10], force=True, min_interval_minutes=5))
    assert out["status"] == "recent_run_exists" and seen[0][1] == 5
    bad = deepcopy(v1.DEFAULT_V1); bad["ml"]["training"]["manual_min_interval_minutes"] = 0
    errors = []
    v1.validate(bad, errors)
    assert errors
