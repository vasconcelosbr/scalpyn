import numpy as np

from app.ml.indicator_intelligence import (
    build_indicator_intelligence_report,
    inverse_group_frequency_weights,
)


def test_duplicate_profiles_have_one_snapshot_total_weight():
    weights = inverse_group_frequency_weights(["a", "a", "a", "b"])
    assert np.isclose(weights[:3].sum(), 1.0)
    assert np.isclose(weights[3], 1.0)


def test_prioritize_requires_same_direction_in_validation_and_test():
    train = np.arange(40, dtype=float).reshape(-1, 1)
    val = np.arange(40, dtype=float).reshape(-1, 1)
    test = np.arange(40, dtype=float).reshape(-1, 1)
    labels = np.asarray([0] * 20 + [1] * 20)
    weights = np.ones(40)
    report = build_indicator_intelligence_report(
        train, labels, val, labels, test, labels, ["signal"],
        weights, weights, weights, np.zeros(40), np.zeros(40),
        min_effective_cases=5, min_abs_lift=0.20,
    )
    assert any(
        item["indicator"] == "signal" and item["action"] == "PRIORITIZE"
        for item in report["findings"]
    )
    assert report["execution_authority"] is False
