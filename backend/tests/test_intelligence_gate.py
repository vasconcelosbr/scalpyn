from app.ml.intelligence_gate import evaluate_intelligence_gate


CONFIG = {
    "ml_intelligence_min_test_auc": 0.60,
    "ml_intelligence_min_effective_test_snapshots": 300,
    "ml_intelligence_max_val_test_gap": 0.05,
    "ml_intelligence_max_test_brier": 0.25,
}


def test_approves_stable_advisory_model_without_execution_authority():
    result = evaluate_intelligence_gate({
        "validation": {"weighted_roc_auc": 0.63},
        "test": {
            "weighted_roc_auc": 0.61,
            "weighted_brier": 0.24,
            "effective_snapshots": 1200,
        },
    }, CONFIG)
    assert result["status"] == "APPROVED"
    assert result["execution_authority"] is False


def test_rejects_unstable_or_uncalibrated_model():
    result = evaluate_intelligence_gate({
        "validation": {"weighted_roc_auc": 0.72},
        "test": {
            "weighted_roc_auc": 0.59,
            "weighted_brier": 0.27,
            "effective_snapshots": 1200,
        },
    }, CONFIG)
    assert result["status"] == "REJECTED"
    assert "test_auc_below_intelligence_minimum" in result["reasons"]
    assert "intelligence_generalization_gap_exceeded" in result["reasons"]
    assert "test_brier_exceeded" in result["reasons"]
