import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from backend.app.services.ml_challenger_service import (
    MLChallengerService,
    _require_positive_int_config,
)
from backend.app.ml.promotion_gate import BLOCKED, evaluate_promotion_gate
from backend.scripts.run_catboost_retrain import _dry_run_gate_payload


def test_catboost_minimum_is_required():
    with pytest.raises(
        ValueError, match="missing_ml_catboost_retrain_min_eligible_rows"
    ):
        _require_positive_int_config({}, "ml_catboost_retrain_min_eligible_rows")


@pytest.mark.parametrize("value", [0, -1, "200", 1.5, True, False])
def test_catboost_minimum_rejects_non_positive_or_non_integer_values(value):
    with pytest.raises(
        ValueError, match="invalid_ml_catboost_retrain_min_eligible_rows"
    ):
        _require_positive_int_config(
            {"ml_catboost_retrain_min_eligible_rows": value},
            "ml_catboost_retrain_min_eligible_rows",
        )


def test_catboost_minimum_accepts_positive_integer():
    assert _require_positive_int_config(
        {"ml_catboost_retrain_min_eligible_rows": 200},
        "ml_catboost_retrain_min_eligible_rows",
    ) == 200


def test_shared_catboost_preparation_applies_profile_and_barrier_contract():
    async def _run():
        svc = MLChallengerService()
        svc._last_shadow_load_diagnostics = {
            "official_candidates": 4,
            "labels_unresolved_at_cutoff": 0,
            "observations_immature_at_cutoff": 0,
            "records_mature": 4,
        }
        svc._load_shadow_data = AsyncMock(return_value=[
            {
                "source": "L3",
                "profile_id": "profile-a",
                "barrier_mode": "ATR_DYNAMIC",
                "tp_pct_applied": 0.6,
            },
            {
                "source": "L3",
                "profile_id": "profile-a",
                "barrier_mode": "FIXED",
                "tp_pct_applied": 0.6,
            },
            {
                "source": "L3",
                "profile_id": "profile-a",
                "barrier_mode": "ATR_DYNAMIC",
                "tp_pct_applied": 1.5,
            },
            {
                "source": "L3",
                "profile_id": None,
                "barrier_mode": "ATR_DYNAMIC",
                "tp_pct_applied": 0.6,
            },
        ])
        cutoff = datetime(2026, 7, 13, 17, 0, tzinfo=timezone.utc)
        records, meta = await svc._prepare_catboost_gate_records(
            AsyncMock(),
            "00000000-0000-0000-0000-000000000001",
            lookback_days=90,
            cb_sources=["L3"],
            dataset_query_cutoff=cutoff,
            ml_config={
                "ml_dataset_valid_from": "2026-07-01T00:00:00+00:00",
                "ml_l3_dataset_valid_from": "2026-07-11T03:21:06+00:00",
                "ml_maturity_embargo_margin_minutes": 60,
                "shadow_barrier_mode": "ATR_DYNAMIC",
            },
            strategy_tp_pct=0.6,
            collect_diagnostics=True,
        )
        return svc, cutoff, records, meta

    svc, cutoff, records, meta = asyncio.run(_run())

    assert len(records) == 1
    assert meta["records_with_profile"] == 3
    assert meta["barrier_contract"]["barrier_contract_included"] == 1
    assert meta["barrier_contract"]["barrier_contract_mode_mismatch"] == 1
    assert meta["barrier_contract"]["barrier_contract_tp_mismatch"] == 1
    assert meta["l3_strict_meta"]["excluded_null_profile_id"] == 1
    load_kwargs = svc._load_shadow_data.await_args.kwargs
    assert load_kwargs["dataset_query_cutoff"] == cutoff
    assert load_kwargs["maturity_embargo_margin_minutes"] == 60
    assert load_kwargs["collect_diagnostics"] is True


def test_catboost_train_gate_uses_database_minimum_and_reports_deficit(monkeypatch):
    async def _run():
        svc = MLChallengerService()
        svc._load_ml_config = AsyncMock(return_value={
            "ml_dataset_valid_from": "2026-07-01T00:00:00+00:00",
            "ml_l3_dataset_valid_from": "2026-07-11T03:21:06+00:00",
            "ml_catboost_retrain_min_eligible_rows": 4,
            "ml_promotion_min_test_samples": 3,
            "ml_maturity_embargo_margin_minutes": 60,
            "shadow_barrier_mode": "ATR_DYNAMIC",
        })
        svc._load_strategy_tp_pct = AsyncMock(return_value=0.6)
        svc._prepare_catboost_gate_records = AsyncMock(return_value=(
            [{"shadow_id": "a"}] * 3,
            {
                "lane": "L3_PROFILE",
                "dataset_policy": "L3_ONLY",
                "dataset_valid_from": datetime(2026, 7, 11, tzinfo=timezone.utc),
                "all_record_count": 3,
                "records_with_profile": 3,
                "maturity_diagnostics": {"records_mature": 3},
                "barrier_contract": {"barrier_contract_included": 3},
                "l3_strict_meta": {
                    "excluded_null_profile_id": 0,
                    "distinct_profiles": 1,
                    "unknown_profile_pct": 0.0,
                },
            },
        ))
        return await svc.train_challengers(
            db=AsyncMock(),
            user_id="00000000-0000-0000-0000-000000000001",
            enable_lightgbm=False,
            enable_catboost=True,
            catboost_source_filter=["L3"],
        )

    result = asyncio.run(_run())

    assert result["catboost"]["status"] == "skipped"
    assert result["catboost"]["reason"] == "insufficient_retrain_eligible_rows"
    assert result["catboost"]["records"] == 3
    assert result["catboost"]["min_required"] == 4
    assert result["catboost"]["deficit"] == 1


def test_catboost_dry_run_payload_uses_post_barrier_records():
    cutoff = datetime(2026, 7, 13, 17, 0, tzinfo=timezone.utc)
    payload = _dry_run_gate_payload(
        records=155,
        min_required=200,
        dataset_query_cutoff=cutoff,
        maturity_margin=60,
        sources=["L3"],
        gate_meta={
            "records_with_profile": 165,
            "maturity_diagnostics": {
                "official_candidates": 209,
                "labels_unresolved_at_cutoff": 0,
                "observations_immature_at_cutoff": 44,
                "records_mature": 165,
            },
            "barrier_contract": {
                "barrier_contract_included": 155,
                "barrier_contract_missing": 0,
                "barrier_contract_mode_mismatch": 1,
                "barrier_contract_tp_mismatch": 9,
            },
            "l3_strict_meta": {"dataset_policy": "L3_ONLY"},
        },
    )

    assert payload["status"] == "skipped"
    assert payload["reason"] == "insufficient_retrain_eligible_rows"
    assert payload["records"] == 155
    assert payload["min_required"] == 200
    assert payload["deficit"] == 45
    assert payload["records_with_profile"] == 165
    assert payload["barrier_contract_included"] == 155


def test_catboost_dry_run_blocks_when_promotion_holdout_is_infeasible():
    cutoff = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    payload = _dry_run_gate_payload(
        records=536,
        min_required=200,
        dataset_query_cutoff=cutoff,
        maturity_margin=60,
        sources=["L3"],
        gate_meta={
            "records_with_profile": 536,
            "maturity_diagnostics": {"records_mature": 536},
            "barrier_contract": {"barrier_contract_included": 536},
            "l3_strict_meta": {"dataset_policy": "L3_ONLY"},
        },
        split_readiness={
            "has_test": False,
            "train_samples": 536,
            "validation_samples": 0,
            "test_samples": 0,
            "diagnostics": {
                "required_test_samples": 300,
                "max_candidate_test_samples": 244,
                "test_sample_deficit": 56,
            },
        },
    )

    assert payload["status"] == "skipped"
    assert payload["reason"] == "insufficient_promotion_holdout"
    assert payload["deficit"] == 0
    assert payload["split_readiness"]["diagnostics"]["test_sample_deficit"] == 56


def test_new_candidate_persists_contracts_and_fail_closed_governance():
    class _ScalarResult:
        @staticmethod
        def scalar():
            return 80

    async def _run():
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[_ScalarResult(), None, None])
        svc = MLChallengerService()
        svc._load_ml_config = AsyncMock(return_value={
            "ml_label_version": "is_tp_4h_v2_sim_outcome",
            "ml_promotion_min_test_auc": 0.6,
            "ml_promotion_min_test_samples": 300,
            "ml_promotion_max_val_test_gap": 0.05,
            "ml_promotion_max_test_fpr": 0.5,
            "ml_promotion_require_positive_net_ev": True,
        })
        await svc._save_to_db(
            db=db,
            model_type="catboost",
            model_obj={"kind": "test"},
            feature_columns=["f1", "f2"],
            metrics={
                "roc_auc": 0.7,
                "f1": 0.5,
                "train_samples": 200,
                "val_samples": 20,
                "train_from": datetime(2026, 7, 1, tzinfo=timezone.utc),
                "train_to": datetime(2026, 7, 10, tzinfo=timezone.utc),
                "dataset_query_cutoff": datetime(2026, 7, 14, tzinfo=timezone.utc),
                "dataset_hash": "a" * 64,
                "train_sources": ["L3"],
                "label_objective": "positive_net_return",
            },
            threshold=0.55,
            profile_id=None,
            user_id="00000000-0000-0000-0000-000000000001",
            model_lane="L3_PROFILE",
            test_metrics={
                "samples": 20,
                "roc_auc": 0.7,
                "fpr": 0.2,
                "net_ev": 0.1,
            },
            win_fast_threshold_s=14400,
        )
        return db

    db = asyncio.run(_run())
    insert_params = db.execute.await_args_list[1].args[1]
    assert insert_params["dataset_contract_id"]
    assert insert_params["label_contract_id"]
    assert insert_params["feature_contract_id"]
    assert insert_params["predictive_status"] == "PREDICTIVE_REJECTED"
    assert insert_params["calibration_authority"] is False
    assert insert_params["rule_generation_authority"] is False
    assert insert_params["execution_authority"] is False


def test_promotion_gate_requires_label_and_feature_contract_ids():
    config = {
        "ml_promotion_min_test_auc": 0.6,
        "ml_promotion_min_test_samples": 300,
        "ml_promotion_max_val_test_gap": 0.05,
        "ml_promotion_max_test_fpr": 0.5,
        "ml_promotion_require_positive_net_ev": True,
    }
    result = evaluate_promotion_gate({
        "metrics_json": {
            "validation": {"roc_auc": 0.62},
            "test": {
                "roc_auc": 0.63,
                "samples": 300,
                "fpr": 0.2,
                "net_ev": 0.1,
            },
        },
        "test_samples": 300,
        "feature_count": 2,
        "label_version": "is_tp_4h_v2_sim_outcome",
        "model_lane": "L3_PROFILE",
        "source_filter": "L3",
        "dataset_contract_id": "00000000-0000-0000-0000-000000000001",
        "label_contract_id": None,
        "feature_contract_id": None,
        "train_from": datetime(2026, 7, 1, tzinfo=timezone.utc),
        "train_to": datetime(2026, 7, 10, tzinfo=timezone.utc),
        "dataset_query_cutoff": datetime(2026, 7, 14, tzinfo=timezone.utc),
        "dataset_hash": "a" * 64,
    }, promotion_config=config)

    assert result["status"] == BLOCKED
    assert "missing_label_contract_id" in result["reasons"]
    assert "missing_feature_contract_id" in result["reasons"]


def test_new_candidate_sanitizes_non_finite_metrics_before_jsonb():
    class _ScalarResult:
        @staticmethod
        def scalar():
            return 81

    async def _run():
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[_ScalarResult(), None, None])
        svc = MLChallengerService()
        svc._load_ml_config = AsyncMock(return_value={
            "ml_label_version": "positive_net_return_v1",
            "ml_promotion_min_test_auc": 0.6,
            "ml_promotion_min_test_samples": 300,
            "ml_promotion_max_val_test_gap": 0.05,
            "ml_promotion_max_test_fpr": 0.5,
            "ml_promotion_require_positive_net_ev": True,
        })
        await svc._save_to_db(
            db=db,
            model_type="catboost",
            model_obj={"kind": "test"},
            feature_columns=["f1", "f2"],
            metrics={
                "roc_auc": float("nan"),
                "f1": 1.0,
                "train_samples": 200,
                "val_samples": 15,
                "train_from": datetime(2026, 7, 1, tzinfo=timezone.utc),
                "train_to": datetime(2026, 7, 10, tzinfo=timezone.utc),
                "dataset_query_cutoff": datetime(2026, 7, 14, tzinfo=timezone.utc),
                "dataset_hash": "b" * 64,
                "train_sources": ["L3"],
                "label_objective": "positive_net_return",
            },
            threshold=0.81,
            profile_id=None,
            user_id="00000000-0000-0000-0000-000000000001",
            model_lane="L3_PROFILE",
            test_metrics={
                "samples": 310,
                "roc_auc": 0.42,
                "fpr": 0.0,
                "net_ev": float("nan"),
            },
            win_fast_threshold_s=14400,
        )
        return db

    db = asyncio.run(_run())
    insert_params = db.execute.await_args_list[1].args[1]
    assert insert_params["roc_auc"] is None
    assert "NaN" not in insert_params["hyperparams"]
    assert "NaN" not in insert_params["metrics_json"]
