"""WinFast Trainer — XGBoost + Optuna trainer for Cloud Run Job."""

import logging
from typing import Optional

import mlflow
import mlflow.xgboost
import optuna
import pandas as pd
import xgboost as xgb
from sklearn.metrics import (
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)

from .feature_extractor import FEATURE_COLUMNS, train_val_test_split

logger = logging.getLogger(__name__)

# Suppress Optuna INFO noise in Cloud Run logs
optuna.logging.set_verbosity(optuna.logging.WARNING)


class WinFastTrainer:
    """
    XGBoost trainer with Optuna hyperparameter optimization.

    Zero Hardcode: all parameters found by Optuna.
    Threshold is set post-training via ml_models.decision_threshold in Cloud SQL.
    """

    def __init__(self, n_trials: int = 50):
        self.n_trials = n_trials
        self.model: Optional[xgb.XGBClassifier] = None

    def train(self, df: pd.DataFrame, optuna_storage_url: Optional[str] = None) -> dict:
        """
        Train XGBoost model with Optuna hyperparameter optimization.

        Args:
            df: Training DataFrame from build_training_dataframe()
            optuna_storage_url: PostgreSQL URL for Optuna study persistence

        Returns:
            Dict with: best_params, metrics, run_id, train_from, train_to,
                       n_train, n_val, n_test
        """
        feature_cols = [c for c in FEATURE_COLUMNS if c in df.columns]
        train_df, val_df, test_df = train_val_test_split(df)

        X_train = train_df[feature_cols].fillna(0.0)
        y_train = train_df["is_win_fast"]
        X_val = val_df[feature_cols].fillna(0.0)
        y_val = val_df["is_win_fast"]
        X_test = test_df[feature_cols].fillna(0.0)
        y_test = test_df["is_win_fast"]

        n_neg = int((y_train == 0).sum())
        n_pos = int((y_train == 1).sum())
        scale_pos_weight = n_neg / n_pos if n_pos > 0 else 1.0
        logger.info(
            f"Class balance: {n_pos} wins / {n_neg} losses "
            f"(scale_pos_weight={scale_pos_weight:.2f})"
        )

        def objective(trial: optuna.Trial) -> float:
            params = {
                "objective": "binary:logistic",
                "eval_metric": "auc",
                "tree_method": "hist",
                "device": "cpu",
                "random_state": 42,
                "scale_pos_weight": scale_pos_weight,
                "max_depth": trial.suggest_int("max_depth", 3, 8),
                "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.3, log=True),
                "n_estimators": trial.suggest_int("n_estimators", 100, 600),
                "subsample": trial.suggest_float("subsample", 0.6, 1.0),
                "colsample_bytree": trial.suggest_float("colsample_bytree", 0.6, 1.0),
                "min_child_weight": trial.suggest_int("min_child_weight", 1, 10),
                "reg_alpha": trial.suggest_float("reg_alpha", 0.0, 1.0),
                "reg_lambda": trial.suggest_float("reg_lambda", 0.5, 2.0),
            }
            m = xgb.XGBClassifier(**params)
            m.fit(
                X_train, y_train,
                eval_set=[(X_val, y_val)],
                early_stopping_rounds=20,
                verbose=False,
            )
            if y_val.nunique() < 2:
                return 0.0
            proba = m.predict_proba(X_val)[:, 1]
            return float(roc_auc_score(y_val, proba))

        study_kwargs: dict = {"direction": "maximize", "study_name": "win_fast_study"}
        if optuna_storage_url:
            study_kwargs["storage"] = optuna_storage_url
            study_kwargs["load_if_exists"] = True

        study = optuna.create_study(**study_kwargs)
        study.optimize(objective, n_trials=self.n_trials, show_progress_bar=False)

        best_params = {
            "objective": "binary:logistic",
            "eval_metric": "auc",
            "tree_method": "hist",
            "device": "cpu",
            "random_state": 42,
            "scale_pos_weight": scale_pos_weight,
            **study.best_params,
        }
        logger.info(
            f"Best trial: val_auc={study.best_value:.4f} | params={study.best_params}"
        )

        # Final training with MLflow logging
        with mlflow.start_run() as run:
            self.model = xgb.XGBClassifier(**best_params)
            self.model.fit(
                X_train, y_train,
                eval_set=[(X_val, y_val)],
                early_stopping_rounds=20,
                verbose=False,
            )

            proba_test = self.model.predict_proba(X_test)[:, 1]
            pred_test = (proba_test >= 0.5).astype(int)

            if y_test.nunique() >= 2:
                precision = float(precision_score(y_test, pred_test, zero_division=0))
                recall = float(recall_score(y_test, pred_test, zero_division=0))
                f1 = float(f1_score(y_test, pred_test, zero_division=0))
                roc_auc = float(roc_auc_score(y_test, proba_test))
            else:
                logger.warning("Test set has only one class — metrics defaulted to 0")
                precision = recall = f1 = roc_auc = 0.0

            win_mask = y_test == 1
            capture_rate = (
                float((pred_test[win_mask] == 1).mean()) if win_mask.sum() > 0 else 0.0
            )
            neg_mask = y_test == 0
            fpr = (
                float((pred_test[neg_mask] == 1).mean()) if neg_mask.sum() > 0 else 0.0
            )

            mlflow.log_params(best_params)
            mlflow.log_metrics({
                "precision": precision,
                "recall": recall,
                "f1": f1,
                "roc_auc": roc_auc,
                "win_fast_capture_rate": capture_rate,
                "false_positive_rate": fpr,
            })
            mlflow.xgboost.log_model(self.model, "model")
            run_id = run.info.run_id

        logger.info(
            f"Metrics: precision={precision:.4f} recall={recall:.4f} "
            f"f1={f1:.4f} roc_auc={roc_auc:.4f} capture={capture_rate:.4f}"
        )

        return {
            "best_params": best_params,
            "metrics": {
                "precision": precision,
                "recall": recall,
                "f1": f1,
                "roc_auc": roc_auc,
                "win_fast_capture_rate": capture_rate,
                "false_positive_rate": fpr,
            },
            "run_id": run_id,
            "train_from": df["_created_at"].min() if "_created_at" in df.columns else None,
            "train_to": df["_created_at"].max() if "_created_at" in df.columns else None,
            "n_train": len(X_train),
            "n_val": len(X_val),
            "n_test": len(X_test),
        }
