from scripts.validate_gate_config import validate_config


def _config():
    return {
        "ml_catboost_retrain_min_eligible_rows": 1200,
        "ml_catboost_train_size_ratio": 0.60,
        "ml_catboost_validation_size_ratio": 0.20,
        "ml_catboost_test_size_ratio": 0.20,
        "ml_catboost_min_train_samples": 600,
        "ml_catboost_min_validation_samples": 200,
        "ml_catboost_min_test_samples": 200,
        "ml_promotion_min_test_samples": 300,
    }


def test_candidate_validation_gate_is_partition_coherent_but_not_promotable():
    result = validate_config(_config())

    assert result["valid"] is True
    assert result["partition_minima_total"] == 1000
    assert result["pre_purge_headroom"] == 200
    assert result["nominal_partition_sizes"] == {
        "train": 720,
        "validation": 240,
        "test": 240,
    }
    assert result["nominal_test_meets_promotion_minimum"] is False
    assert result["promotion_test_nominal_deficit"] == 60


def test_previous_partition_minima_are_incoherent_with_1200_total_gate():
    config = _config()
    config["ml_catboost_min_train_samples"] = 1000

    result = validate_config(config)

    assert result["valid"] is False
    assert "partition_minima_exceed_total_gate" in result["errors"]
    assert "train_minimum_exceeds_nominal_allocation" in result["errors"]
