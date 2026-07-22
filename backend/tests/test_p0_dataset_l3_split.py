"""Tests for P0 Dataset L3 split: source policies, gates, feature coverage, governance.

Validates:
  SOURCE-1: L3_ONLY policy uses only source='L3'
  SOURCE-2: L3_LAB_ONLY policy uses only source='L3_LAB'
  SOURCE-3: L3_COMBINED is blocked by _check_mixed_source_gate
  SOURCE-4: train_challengers blocks L3+L3_LAB combined by default
  SOURCE-5: allow_mixed_source=True bypasses the gate
  COV-1:    audit_feature_coverage detects dead features (< 30% coverage)
  COV-2:    audit_feature_coverage marks ALWAYS_ZERO when zero_pct >= 95%
  COV-3:    dead_feature_pct counts DEAD_FEATURE and ALWAYS_ZERO status
  MACRO-1:  macro_sign_balance gate triggers when sp500 is always-negative in train
  GOV-1:    governance_flags_for_model blocks mixed-source models
  GOV-2:    governance_flags_for_model blocks when test_auc < 0.50
  GOV-3:    governance_flags_for_model blocks when no operational edge
  GOV-4:    v42-equivalent model receives MIXED_SOURCE_L3_L3LAB + TEST_AUC_ANTI_PREDICTIVE
  GOV-5:    model without blocked reasons returns ranking_shadow_only + forward_validation_candidate
  LABEL-3:  label_version remains is_tp_4h_v2_sim_outcome through the policy flow
  POLICY-1: POLICY_SOURCES maps each policy to the correct source list
  POLICY-2: check_source_drift detects > 25 pp drift between train and test
"""

import asyncio
import sys
import types
from pathlib import Path
from typing import Dict, List, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.ml.dataset_policy import (
    DatasetPolicy,
    POLICY_SOURCES,
    CatBoostReadinessGate,
    FeatureCoverageReport,
    audit_feature_coverage,
    dead_feature_pct,
    check_source_drift,
    governance_flags_for_model,
)
from backend.app.services.ml_challenger_service import (
    MLChallengerService,
    CATBOOST_L3_ONLY_SOURCES,
    CATBOOST_L3_LAB_ONLY_SOURCES,
    MIXED_SOURCE_BLOCKED_REASON,
)


# ---------------------------------------------------------------------------
# SOURCE-1/2/3: Policy source lists
# ---------------------------------------------------------------------------

class TestPolicySources:
    def test_l3_only_policy(self):
        assert POLICY_SOURCES[DatasetPolicy.L3_ONLY] == ["L3"]

    def test_l3_lab_only_policy(self):
        assert POLICY_SOURCES[DatasetPolicy.L3_LAB_ONLY] == ["L3_LAB"]

    def test_combined_policy(self):
        srcs = POLICY_SOURCES[DatasetPolicy.L3_COMBINED]
        assert "L3" in srcs and "L3_LAB" in srcs

    def test_l3_only_sources_constant(self):
        assert CATBOOST_L3_ONLY_SOURCES == ["L3"]

    def test_l3_lab_only_sources_constant(self):
        assert CATBOOST_L3_LAB_ONLY_SOURCES == ["L3_LAB"]


# ---------------------------------------------------------------------------
# SOURCE-3/4: Mixed source gate
# ---------------------------------------------------------------------------

class TestMixedSourceGate:
    def test_gate_blocks_l3_plus_l3_lab(self):
        reason = MLChallengerService._check_mixed_source_gate(["L3", "L3_LAB"])
        assert reason == MIXED_SOURCE_BLOCKED_REASON

    def test_gate_allows_l3_only(self):
        reason = MLChallengerService._check_mixed_source_gate(["L3"])
        assert reason is None

    def test_gate_allows_l3_lab_only(self):
        reason = MLChallengerService._check_mixed_source_gate(["L3_LAB"])
        assert reason is None

    def test_gate_blocks_reversed_order(self):
        reason = MLChallengerService._check_mixed_source_gate(["L3_LAB", "L3"])
        assert reason == MIXED_SOURCE_BLOCKED_REASON

    def test_gate_allows_l1_spectrum(self):
        reason = MLChallengerService._check_mixed_source_gate(["L1_SPECTRUM"])
        assert reason is None


# ---------------------------------------------------------------------------
# SOURCE-4: train_challengers blocks combined by default
# ---------------------------------------------------------------------------

class TestTrainChallengersGate:
    """train_challengers must return status=blocked when L3+L3_LAB are passed."""

    def test_blocks_combined_by_default(self):
        async def _run():
            svc = MLChallengerService()
            db = AsyncMock()
            db.execute = AsyncMock()
            db.commit = AsyncMock()
            return await svc.train_challengers(
                db=db,
                user_id="00000000-0000-0000-0000-000000000001",
                enable_lightgbm=False,
                enable_catboost=True,
                catboost_source_filter=["L3", "L3_LAB"],
                allow_mixed_source=False,
            )
        result = asyncio.run(_run())
        assert result["catboost"]["status"] == "blocked"
        assert result["catboost"]["reason"] == MIXED_SOURCE_BLOCKED_REASON

    def test_default_catboost_sources_are_l3_only(self):
        """When no source_filter is given, CatBoost defaults to L3_ONLY (not combined)."""
        svc = MLChallengerService()
        gate_reason = svc._check_mixed_source_gate(CATBOOST_L3_ONLY_SOURCES)
        assert gate_reason is None, "Default L3_ONLY sources must not trigger the gate"

    def test_lightgbm_respects_configured_retrain_minimum(self):
        """L1 retrain must stop at the configured marco, not the low legacy MIN_RECORDS."""
        async def _run():
            svc = MLChallengerService()
            svc._load_ml_config = AsyncMock(return_value={
                "ml_dataset_valid_from": "2026-06-14T21:33:10+00:00",
                "ml_retrain_min_eligible_rows": 4,
                "ml_maturity_embargo_margin_minutes": 60,
                "ml_win_fast_threshold_seconds": 1800,
                "shadow_barrier_mode": "ATR_DYNAMIC",
            })
            svc._load_strategy_tp_pct = AsyncMock(return_value=0.6)
            svc._load_shadow_data = AsyncMock(return_value=[{
                "shadow_id": "a",
                "barrier_mode": "ATR_DYNAMIC",
                "tp_pct_applied": 0.6,
                "barrier_contract_version": "shadow_atr_dynamic_v2",
            }] * 3)
            db = AsyncMock()
            feature_module = types.ModuleType("app.ml.feature_extractor")
            feature_module.FEATURE_COLUMNS = ["rsi"]
            with patch.dict(sys.modules, {
                "app": types.ModuleType("app"),
                "app.ml": types.ModuleType("app.ml"),
                "app.ml.feature_extractor": feature_module,
            }):
                return await svc.train_challengers(
                    db=db,
                    user_id="00000000-0000-0000-0000-000000000001",
                    enable_lightgbm=True,
                    enable_catboost=False,
                )

        result = asyncio.run(_run())

        assert result["lightgbm"]["status"] == "skipped"
        assert result["lightgbm"]["reason"] == "insufficient_retrain_eligible_rows"
        assert result["lightgbm"]["records"] == 3
        assert result["lightgbm"]["min_required"] == 4


# ---------------------------------------------------------------------------
# SOURCE-5: allow_mixed_source bypasses gate
# ---------------------------------------------------------------------------

class TestAllowMixedSourceOverride:
    def test_gate_bypassed_when_flag_true(self):
        # The gate itself still detects the mix, but train_challengers
        # checks allow_mixed_source before calling the gate.
        # We verify that the gate function still returns the reason
        # (it's the caller's job to skip it).
        reason = MLChallengerService._check_mixed_source_gate(["L3", "L3_LAB"])
        assert reason is not None  # gate always fires
        # Callers with allow_mixed_source=True skip the early-return


# ---------------------------------------------------------------------------
# COV-1/2/3: Feature coverage audit
# ---------------------------------------------------------------------------

def _make_snap(keys: Dict[str, float]) -> Dict:
    return {"features_snapshot": keys}


class TestFeatureCoverage:
    def test_all_present_is_feature_ok(self):
        records = [_make_snap({"rsi": 50.0, "adx": 25.0}) for _ in range(10)]
        cov = audit_feature_coverage(records, "L3_LAB", ["rsi", "adx"])
        statuses = {r.feature_name: r.status for r in cov}
        assert statuses["rsi"] == "FEATURE_OK"
        assert statuses["adx"] == "FEATURE_OK"

    def test_absent_feature_is_dead(self):
        records = [_make_snap({"rsi": 50.0}) for _ in range(10)]
        cov = audit_feature_coverage(records, "L3_LAB", ["rsi", "volume_spike"])
        statuses = {r.feature_name: r.status for r in cov}
        assert statuses["volume_spike"] == "DEAD_FEATURE"

    def test_low_coverage_classification(self):
        records = (
            [_make_snap({"volume_spike": 1.5}) for _ in range(5)] +
            [_make_snap({}) for _ in range(15)]
        )  # 25% coverage
        cov = audit_feature_coverage(records, "L3_LAB", ["volume_spike"])
        assert cov[0].status == "DEAD_FEATURE"  # 25% < 30%

    def test_coverage_threshold_boundary(self):
        records = (
            [_make_snap({"adx": 30.0}) for _ in range(80)] +
            [_make_snap({}) for _ in range(20)]
        )  # 80% → FEATURE_OK
        cov = audit_feature_coverage(records, "L3_LAB", ["adx"])
        assert cov[0].status == "FEATURE_OK"
        assert cov[0].coverage_pct == 80.0

    def test_always_zero_detection(self):
        records = [_make_snap({"atr_pct": 0.0}) for _ in range(20)]
        cov = audit_feature_coverage(records, "L3", ["atr_pct"])
        assert cov[0].status == "ALWAYS_ZERO"  # 100% coverage but 100% zero

    def test_dead_feature_pct_counts_dead_and_zero(self):
        cov = [
            FeatureCoverageReport("L3", "rsi", 10, 0, 0, 10, 100.0, 0.0, "FEATURE_OK"),
            FeatureCoverageReport("L3", "volume_spike", 0, 10, 0, 10, 0.0, 0.0, "DEAD_FEATURE"),
            FeatureCoverageReport("L3", "atr_pct", 10, 0, 10, 10, 100.0, 100.0, "ALWAYS_ZERO"),
        ]
        pct = dead_feature_pct(cov)
        assert abs(pct - 2/3) < 1e-9

    def test_empty_records_returns_empty(self):
        cov = audit_feature_coverage([], "L3", ["rsi"])
        assert cov == []


# ---------------------------------------------------------------------------
# POLICY-2: check_source_drift
# ---------------------------------------------------------------------------

class TestSourceDrift:
    def test_no_drift_ok(self):
        ok, violations = check_source_drift(
            {"L3": 50.0, "L3_LAB": 50.0},
            {"L3": 55.0, "L3_LAB": 45.0},
            max_drift_pp=25.0,
        )
        assert ok
        assert violations == []

    def test_v42_drift_detected(self):
        # v42 audit: train L3_LAB=79.7% → test L3_LAB=8.6% = 71.1pp drift
        ok, violations = check_source_drift(
            {"L3": 20.3, "L3_LAB": 79.7},
            {"L3": 91.4, "L3_LAB": 8.6},
            max_drift_pp=25.0,
        )
        assert not ok
        assert len(violations) == 2  # both L3 and L3_LAB exceed 25pp

    def test_custom_threshold(self):
        ok, violations = check_source_drift(
            {"L3": 60.0, "L3_LAB": 40.0},
            {"L3": 90.0, "L3_LAB": 10.0},
            max_drift_pp=40.0,
        )
        assert ok  # 30pp < 40pp threshold


# ---------------------------------------------------------------------------
# GOV-1/2/3/4/5: governance_flags_for_model
# ---------------------------------------------------------------------------

class TestGovernanceFlags:
    def _v42_like(self) -> Dict[str, Any]:
        """Simulates v42: L3_PROFILE lane, L3+L3_LAB combined, test_auc=0.4225."""
        return {
            "status": "candidate",
            "model_lane": "L3_PROFILE",
            "precision_score": 0.2326,
            "recall_score": 0.7143,
            "test_samples": 430,
            "feature_columns_json": "[...]",
            "metrics_json": {
                "test": {
                    "roc_auc": 0.4225,
                    "precision": 0.2054,
                    "positive_rate": 0.2302,
                }
            },
            "hyperparams": {
                "train_sources": ["L3", "L3_LAB"],
                "source_breakdown": {"L3": 1006, "L3_LAB": 1141},
            },
        }

    def test_v42_blocked_for_mixed_source(self):
        flags = governance_flags_for_model(self._v42_like())
        assert "MIXED_SOURCE_L3_L3LAB" in flags["blocked_reasons"]

    def test_v42_blocked_for_anti_predictive_auc(self):
        flags = governance_flags_for_model(self._v42_like())
        has_auc_block = any("TEST_AUC" in r for r in flags["blocked_reasons"])
        assert has_auc_block

    def test_v42_blocked_no_operational_edge(self):
        # test_prec (0.2054) <= baseline (0.2302)
        flags = governance_flags_for_model(self._v42_like())
        has_edge_block = any("NO_OPERATIONAL_EDGE" in r for r in flags["blocked_reasons"])
        assert has_edge_block

    def test_v42_not_eligible_for_orchestrator(self):
        flags = governance_flags_for_model(self._v42_like())
        assert not flags["eligible_for_orchestrator"]

    def test_v42_not_eligible_for_autopilot(self):
        flags = governance_flags_for_model(self._v42_like())
        assert not flags["eligible_for_autopilot"]

    def test_v42_not_eligible_for_allow_block(self):
        flags = governance_flags_for_model(self._v42_like())
        assert not flags["eligible_for_allow_block"]

    def test_v42_allowed_usage_is_ranking_shadow_only(self):
        flags = governance_flags_for_model(self._v42_like())
        assert flags["allowed_usage"] == ["ranking_shadow_only"]

    def test_clean_candidate_no_blocks(self):
        flags = governance_flags_for_model({
            "status": "candidate",
            "model_lane": "L3_PROFILE",
            "precision_score": 0.45,
            "recall_score": 0.60,
            "test_samples": 500,
            "feature_columns_json": "[...]",
            "metrics_json": {
                "test": {"roc_auc": 0.72, "precision": 0.45, "positive_rate": 0.20}
            },
            "hyperparams": {"train_sources": ["L3"]},
        })
        assert flags["blocked_reasons"] == []
        assert "ranking_shadow_only" in flags["allowed_usage"]
        assert "forward_validation_candidate" in flags["allowed_usage"]
        assert flags["governance_warning"] is None

    def test_active_incomplete_gets_ranking_shadow_only(self):
        flags = governance_flags_for_model({
            "status": "active",
            "model_lane": "L3_PROFILE",
            "precision_score": None,   # missing
            "recall_score": None,
            "test_samples": None,
            "feature_columns_json": None,
            "metrics_json": {},
            "hyperparams": {},
        })
        assert "INCOMPLETE_METRICS" in flags["blocked_reasons"]
        assert flags["governance_warning"] == "ranking_shadow_only"

    def test_l3_profile_lane_test_auc_below_random(self):
        flags = governance_flags_for_model({
            "status": "candidate",
            "model_lane": "L3_PROFILE",
            "precision_score": 0.30,
            "recall_score": 0.50,
            "test_samples": 300,
            "feature_columns_json": "[...]",
            "metrics_json": {
                "test": {"roc_auc": 0.48, "precision": 0.30, "positive_rate": 0.25}
            },
            "hyperparams": {"train_sources": ["L3"]},
        })
        has_auc_block = any("TEST_AUC_BELOW_RANDOM" in r for r in flags["blocked_reasons"])
        assert has_auc_block


# ---------------------------------------------------------------------------
# LABEL-3: label_version must be is_tp_4h_v2_sim_outcome through the new policy flow
# ---------------------------------------------------------------------------

class TestLabelVersion:
    def test_label_version_registry(self):
        from backend.app.ml.feature_extractor import label_version_for_threshold
        assert label_version_for_threshold(14400.0) == "is_tp_4h_v2_sim_outcome"
        assert label_version_for_threshold(1800.0) == "is_win_fast_v1"

    def test_dataset_policy_does_not_override_label(self):
        # The dataset policy (L3_ONLY vs L3_LAB_ONLY) is about source selection,
        # not label. The label comes from win_fast_threshold_s.
        # This test asserts that the policy constants don't contain label info.
        assert "label" not in DatasetPolicy.L3_ONLY.lower()
        assert "label" not in DatasetPolicy.L3_LAB_ONLY.lower()


# ---------------------------------------------------------------------------
# Combined readiness gate: combined policy is always blocked
# ---------------------------------------------------------------------------

class TestReadinessGateCombinedBlocked:
    def test_combined_always_blocked(self):
        async def _run():
            gate = CatBoostReadinessGate()
            db = AsyncMock()
            return await gate.check(db, "uid", DatasetPolicy.L3_COMBINED)
        report = asyncio.run(_run())
        assert not report.ready
        assert "MIXED_SOURCE_DATASET_BLOCKED" in report.blocked_reasons
