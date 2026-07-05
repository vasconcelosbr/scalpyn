"""R1 (retreino nº 2) — Optuna via config: fail-closed + espaço da config + seleção por EV.

Smoke SEM tocar test set de produção: dados sintéticos, val apenas.
"""

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("lightgbm")
pytest.importorskip("optuna")

from app.services.ml_challenger_service import (  # noqa: E402
    _suggest_params_from_space,
    _train_lgbm_sync,
)

# Espaço mínimo — só para o smoke; o espaço real vive em config_profiles
# (ml_optuna_search_space.lightgbm), nunca em código.
SMOKE_SPACE = {
    "n_estimators": {"type": "int", "low": 10, "high": 20},
    "learning_rate": {"type": "float", "low": 0.05, "high": 0.2, "log": True},
    "num_leaves": {"type": "int", "low": 7, "high": 15},
}


def _synth(n=300, seed=7):
    rng = np.random.default_rng(seed)
    X = pd.DataFrame({
        "f1": rng.normal(size=n),
        "f2": rng.normal(size=n),
        "f3": rng.normal(size=n),
    })
    y = pd.Series((X["f1"] + rng.normal(scale=0.5, size=n) > 0).astype(int))
    returns = np.where(y == 1, 1.0, -1.0) + rng.normal(scale=0.1, size=n)
    return X, y, returns


class TestFailClosed:
    def test_missing_search_space_aborts(self):
        X, y, r = _synth()
        with pytest.raises(ValueError, match="missing_ml_optuna_search_space_lightgbm"):
            _train_lgbm_sync(X, y, X, y, 2, val_returns=r)

    def test_invalid_type_in_space_aborts(self):
        class _FakeTrial:
            def suggest_int(self, *a, **k):
                return 1

            def suggest_float(self, *a, **k):
                return 0.1

        with pytest.raises(ValueError, match="type inválido"):
            _suggest_params_from_space(
                _FakeTrial(), {"x": {"type": "categorical", "low": 0, "high": 1}}
            )


class TestSmokeConfigDriven:
    def test_n_trials_and_space_from_config_ev_selection(self):
        X, y, r = _synth()
        res = _train_lgbm_sync(
            X.iloc[:200], y.iloc[:200], X.iloc[200:], y.iloc[200:],
            2,  # n_trials — simula ml_optuna_max_trials lido de config
            None, None,  # X_test/y_test: test set NÃO consumido no smoke
            r[200:], None,
            0.01, 5,
            SMOKE_SPACE,
        )
        # n_trials propagado da "config"
        assert res["metrics"]["n_trials"] == 2
        # seleção do trial por EV líquido de validação, não val AUC
        assert res["metrics"]["trial_selection_objective"] == "net_ev"
        # espaço registrado na proveniência é exatamente o da config
        assert res["metrics"]["optuna_search_space"] == SMOKE_SPACE
        # nenhum hiperparâmetro sugerido fora do espaço da config
        assert set(res["best_params"]).issubset(set(SMOKE_SPACE))
        for name, value in res["best_params"].items():
            spec = SMOKE_SPACE[name]
            assert spec["low"] <= value <= spec["high"]
        # test set não avaliado
        assert res["test_metrics"] == {}
