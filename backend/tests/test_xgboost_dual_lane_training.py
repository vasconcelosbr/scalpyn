import numpy as np
import inspect

from app.services.ml_challenger_service import (
    MLChallengerService,
    _train_xgboost_sync,
)


def test_xgboost_trainer_emits_complete_promotion_evidence():
    rng = np.random.default_rng(42)
    features = rng.normal(size=(180, 4))
    labels = (
        features[:, 0]
        + 0.5 * features[:, 1]
        + rng.normal(scale=0.7, size=180)
        > 0
    ).astype(int)

    result = _train_xgboost_sync(
        features[:100],
        labels[:100],
        features[100:140],
        labels[100:140],
        ["f0", "f1", "f2", "f3"],
        n_trials=1,
        X_test=features[140:],
        y_test=labels[140:],
        val_returns=np.where(labels[100:140], 0.5, -0.3),
        test_returns=np.where(labels[140:], 0.5, -0.3),
        threshold_grid_step=0.1,
        threshold_min_positives=3,
        search_space={
            "n_estimators": {
                "type": "int",
                "low": 50,
                "high": 50,
            },
            "max_depth": {"type": "int", "low": 3, "high": 3},
            "learning_rate": {
                "type": "float",
                "low": 0.1,
                "high": 0.1,
            },
        },
        seed=42,
        optuna_timeout_s=30,
        auc_ci_level=0.95,
        bootstrap_iterations=20,
    )

    assert result["model_type"] == "xgboost"
    assert result["metrics"]["trial_selection_objective"] == "net_ev"
    assert result["test_metrics"]["samples"] == 40
    assert 0 <= result["test_metrics"]["roc_auc"] <= 1
    assert 0 <= result["test_metrics"]["roc_auc_ci_low"] <= 1
    assert result["test_metrics"]["net_ev"] is not None


def test_l1_training_propagates_canonical_label_objective_to_dataset_and_lineage():
    source = inspect.getsource(MLChallengerService.train_challengers)
    l1_source = source.split(
        "# \u2500\u2500 Lane 2:",
        maxsplit=1,
    )[0]

    assert "label_objective=label_objective" in l1_source
    assert '"label_objective": label_objective' in l1_source
