"""Tests for the ML Promotion Gate (audit 2026-06-24, P0-1 fix).

Validates that ml_models can no longer reach `status='active'` eligibility
(as consumed by inference/ranking) without passing an objective, quantitative
gate. Specifically must confirm:

  - v44 (CatBoost/L3_PROFILE) and v46 (LightGBM/L1_SPECTRUM) — the two models
    that were actually marked active in production with test ROC AUC < 0.5 —
    are REJECTED when run through this gate (test scenario A/B below).
  - A model with test_roc_auc < 0.5 is rejected in every case (rule #13,
    absolute, non-bypassable).
  - Missing lineage metadata (label_version, model_lane, source_filter,
    dataset_contract_id, feature_count) blocks promotion even with good AUC.
  - A fully well-formed, well-performing model is APPROVED.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.ml.promotion_gate import (
    APPROVED,
    BLOCKED,
    REJECTED,
    evaluate_promotion_gate,
    is_eligible,
    merge_promotion_gate_into_metrics_json,
)


PROMOTION_CONFIG = {
    "ml_promotion_min_test_auc": 0.55,
    "ml_promotion_min_test_samples": 300,
    "ml_promotion_max_val_test_gap": 0.15,
    "ml_promotion_max_test_fpr": 0.55,
    "ml_promotion_require_positive_net_ev": True,
}


def _evaluate(row, **config_overrides):
    return evaluate_promotion_gate(
        row,
        promotion_config={**PROMOTION_CONFIG, **config_overrides},
    )


def _well_formed_row(**overrides):
    base = {
        "metrics_json": {
            "label_version": "is_tp_4h_v1",
            "validation": {"roc_auc": 0.70, "precision": 0.40, "fpr": 0.30},
            "test": {"roc_auc": 0.62, "precision": 0.38, "fpr": 0.32, "samples": 300, "net_ev": 0.1},
        },
        "roc_auc": 0.70,
        "test_samples": 300,
        "feature_count": 48,
        "label_version": "is_tp_4h_v1",
        "model_lane": "L1_SPECTRUM",
        "source_filter": "L1_SPECTRUM",
        "dataset_contract_id": "abc123",
        "label_contract_id": "label123",
        "feature_contract_id": "feature123",
        "train_from": "2026-07-01T00:00:00+00:00",
        "train_to": "2026-07-10T00:00:00+00:00",
        "dataset_query_cutoff": "2026-07-14T00:00:00+00:00",
        "dataset_hash": "abc123",
    }
    base.update(overrides)
    return base


class TestAbsoluteFloorRule13:
    """test_roc_auc < 0.5 must NEVER be promotable, regardless of other thresholds."""

    def test_test_auc_below_0_5_is_rejected(self):
        row = _well_formed_row(metrics_json={
            "label_version": "is_tp_4h_v1",
            "validation": {"roc_auc": 0.70},
            "test": {"roc_auc": 0.49, "samples": 300, "fpr": 0.30},
        })
        result = _evaluate(row)
        assert result["status"] == REJECTED
        assert any("absolute_floor" in r for r in result["reasons"])

    def test_test_auc_exactly_0_5_is_rejected_by_min_threshold(self):
        """0.5 clears the absolute floor (>=) but still fails the 0.55 default minimum."""
        row = _well_formed_row(metrics_json={
            "label_version": "is_tp_4h_v1",
            "validation": {"roc_auc": 0.70},
            "test": {"roc_auc": 0.50, "samples": 300, "fpr": 0.30},
        })
        result = _evaluate(row)
        assert result["status"] == REJECTED
        assert any("below_min_threshold" in r for r in result["reasons"])

    def test_missing_test_auc_is_rejected(self):
        row = _well_formed_row(metrics_json={
            "label_version": "is_tp_4h_v1",
            "validation": {"roc_auc": 0.70},
            "test": {"samples": 300},
        })
        result = _evaluate(row)
        assert result["status"] == REJECTED
        assert "missing_test_roc_auc" in result["reasons"]

    def test_nan_auc_is_treated_as_missing(self):
        row = _well_formed_row(metrics_json={
            "label_version": "is_tp_4h_v1",
            "validation": {"roc_auc": float("nan")},
            "test": {
                "roc_auc": float("nan"),
                "samples": 300,
                "fpr": 0.30,
                "net_ev": 0.1,
            },
        })
        result = _evaluate(row)
        assert result["status"] == REJECTED
        assert "missing_test_roc_auc" in result["reasons"]


class TestRealProductionModelsV44V46:
    """Reproduce the exact metrics_json the audit captured for v44/v46 and confirm
    they would be rejected by this gate — this is the deliverable evidence that
    the audit explicitly asked for: 'Evidência de que v44/v46 não são elegíveis'."""

    def test_v46_lightgbm_l1_spectrum_is_rejected(self):
        row = {
            "metrics_json": {
                "test": {"f1": 0.28448, "fpr": 0.5107296137339056, "recall": 0.4125,
                          "roc_auc": 0.4545600858369099, "samples": 313, "precision": 0.2171},
                "validation": {"f1": 0.5036, "fpr": 0.4518, "recall": 0.5948,
                               "roc_auc": 0.6123315245930334, "samples": 313, "precision": 0.4367},
                "label_version": "is_tp_4h_v1",
                "target_window_seconds": 14400,
            },
            "roc_auc": 0.6123315245930334,
            "test_samples": 313,
            "feature_count": 48,
            "label_version": "is_tp_4h_v1",
            "model_lane": "L1_SPECTRUM",
            "source_filter": None,  # confirmed NULL in production for v46
            "dataset_contract_id": None,  # confirmed NULL in production for v46
        }
        result = _evaluate(row)
        assert result["status"] == REJECTED
        assert any("absolute_floor" in r or "below_min_threshold" in r for r in result["reasons"])

    def test_v44_catboost_l3_profile_is_rejected(self):
        row = {
            "metrics_json": {
                "test": {"f1": 0.28184, "fpr": 0.6575757575757576, "recall": 0.52,
                          "roc_auc": 0.42603030303030304, "samples": 430, "precision": 0.1933},
                "validation": {"f1": 0.3579, "fpr": 0.4568, "recall": 0.7286,
                               "roc_auc": 0.6912057302029446, "samples": 429, "precision": 0.2372},
                "label_version": "is_tp_4h_v1",
                "target_window_seconds": 14400,
            },
            "roc_auc": 0.6912057302029446,
            "test_samples": 430,
            "feature_count": 50,
            "label_version": "is_tp_4h_v1",
            "model_lane": "L3_PROFILE",
            "source_filter": None,
            "dataset_contract_id": None,
        }
        result = _evaluate(row)
        assert result["status"] == REJECTED
        assert any("absolute_floor" in r or "below_min_threshold" in r for r in result["reasons"])
        # also fails on test FPR (0.6576 > 0.55 default ceiling)
        assert any("test_fpr_exceeded" in r for r in result["reasons"])
        # and on generalization gap (|0.6912 - 0.4260| = 0.2652 > 0.15)
        assert any("generalization_gap_exceeded" in r for r in result["reasons"])


class TestGeneralizationGap:
    def test_large_val_test_gap_is_rejected(self):
        row = _well_formed_row(metrics_json={
            "label_version": "is_tp_4h_v1",
            "validation": {"roc_auc": 0.85},
            "test": {"roc_auc": 0.60, "samples": 300, "fpr": 0.30},
        })
        result = _evaluate(row)
        assert result["status"] == REJECTED
        assert any("generalization_gap_exceeded" in r for r in result["reasons"])

    def test_small_val_test_gap_passes_this_specific_rule(self):
        row = _well_formed_row(metrics_json={
            "label_version": "is_tp_4h_v1",
            "validation": {"roc_auc": 0.65},
            "test": {"roc_auc": 0.60, "samples": 300, "fpr": 0.30},
        })
        result = _evaluate(row)
        assert not any("generalization_gap_exceeded" in r for r in result["reasons"])


class TestSampleSizeRule:
    def test_below_minimum_test_samples_is_rejected(self):
        row = _well_formed_row(metrics_json={
            "label_version": "is_tp_4h_v1",
            "validation": {"roc_auc": 0.65},
            "test": {"roc_auc": 0.60, "samples": 50, "fpr": 0.30},
        }, test_samples=50)
        result = _evaluate(row)
        assert result["status"] == REJECTED
        assert any("test_samples_below_minimum" in r for r in result["reasons"])


class TestFprCeiling:
    def test_high_fpr_is_rejected(self):
        row = _well_formed_row(metrics_json={
            "label_version": "is_tp_4h_v1",
            "validation": {"roc_auc": 0.65},
            "test": {"roc_auc": 0.60, "samples": 300, "fpr": 0.90},
        })
        result = _evaluate(row)
        assert result["status"] == REJECTED
        assert any("test_fpr_exceeded" in r for r in result["reasons"])


class TestMissingLineageMetadataBlocks:
    def test_missing_model_lane_blocks_good_model(self):
        row = _well_formed_row(model_lane=None)
        result = _evaluate(row)
        assert result["status"] == BLOCKED
        assert "missing_model_lane" in result["reasons"]

    def test_missing_label_version_blocks_good_model(self):
        row = _well_formed_row(label_version=None)
        row["metrics_json"] = {**row["metrics_json"], "label_version": None}
        result = _evaluate(row)
        assert result["status"] == BLOCKED
        assert "missing_label_version" in result["reasons"]

    def test_missing_dataset_contract_id_blocks_good_model(self):
        row = _well_formed_row(dataset_contract_id=None)
        result = _evaluate(row)
        assert result["status"] == BLOCKED
        assert "missing_dataset_policy" in result["reasons"]

    def test_missing_source_filter_blocks_good_model(self):
        row = _well_formed_row(source_filter=None)
        result = _evaluate(row)
        assert result["status"] == BLOCKED
        assert "missing_train_sources" in result["reasons"]

    def test_missing_feature_count_blocks_good_model(self):
        row = _well_formed_row(feature_count=0)
        result = _evaluate(row)
        assert result["status"] == BLOCKED
        assert "missing_feature_count" in result["reasons"]

    def test_rejected_takes_precedence_over_blocked(self):
        """A model with both a bad AUC and missing metadata must be REJECTED,
        not BLOCKED — the stronger statement (the model itself is bad) wins."""
        row = _well_formed_row(
            model_lane=None,
            metrics_json={
                "label_version": "is_tp_4h_v1",
                "validation": {"roc_auc": 0.70},
                "test": {"roc_auc": 0.40, "samples": 300, "fpr": 0.30},
            },
        )
        result = _evaluate(row)
        assert result["status"] == REJECTED


class TestApprovedPath:
    def test_well_formed_good_model_is_approved(self):
        result = _evaluate(_well_formed_row())
        assert result["status"] == APPROVED
        assert result["reasons"] == []

    def test_is_eligible_helper_true_for_approved(self):
        assert is_eligible(
            _well_formed_row(), promotion_config=PROMOTION_CONFIG
        ) is True

    def test_is_eligible_helper_false_for_rejected(self):
        row = _well_formed_row(metrics_json={
            "label_version": "is_tp_4h_v1",
            "validation": {"roc_auc": 0.70},
            "test": {"roc_auc": 0.40, "samples": 300, "fpr": 0.30},
        })
        assert is_eligible(row, promotion_config=PROMOTION_CONFIG) is False


class TestResultShape:
    def test_result_has_required_keys(self):
        result = _evaluate(_well_formed_row())
        assert set(result.keys()) == {"status", "evaluated_at", "reasons", "thresholds", "metrics"}

    def test_thresholds_are_echoed_in_result(self):
        result = _evaluate(
            _well_formed_row(), ml_promotion_min_test_auc=0.60
        )
        assert result["thresholds"]["min_test_auc"] == 0.60

    def test_absolute_floor_is_always_0_5_even_if_min_test_auc_overridden_lower(self):
        """A caller cannot accidentally bypass rule #13 by passing a lower min_test_auc."""
        row = _well_formed_row(metrics_json={
            "label_version": "is_tp_4h_v1",
            "validation": {"roc_auc": 0.70},
            "test": {"roc_auc": 0.45, "samples": 300, "fpr": 0.30},
        })
        result = _evaluate(row, ml_promotion_min_test_auc=0.30)
        assert result["status"] == REJECTED
        assert any("absolute_floor" in r for r in result["reasons"])


class TestMergeHelper:
    def test_merge_preserves_other_keys(self):
        original = {"label_version": "is_tp_4h_v1", "test": {"roc_auc": 0.6}}
        gate_result = {"status": APPROVED}
        merged = merge_promotion_gate_into_metrics_json(original, gate_result)
        assert merged["label_version"] == "is_tp_4h_v1"
        assert merged["test"] == {"roc_auc": 0.6}
        assert merged["promotion_gate"] == {"status": APPROVED}

    def test_merge_does_not_mutate_input(self):
        original = {"label_version": "is_tp_4h_v1"}
        merge_promotion_gate_into_metrics_json(original, {"status": APPROVED})
        assert "promotion_gate" not in original

    def test_merge_handles_none_metrics_json(self):
        merged = merge_promotion_gate_into_metrics_json(None, {"status": BLOCKED})
        assert merged == {"promotion_gate": {"status": BLOCKED}}
