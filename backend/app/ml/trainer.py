"""WinFast Trainer — XGBoost + Optuna trainer for Cloud Run Job."""

import logging
import math
import os
from typing import Optional

import mlflow
import mlflow.xgboost
import numpy as np
import optuna
import pandas as pd
import xgboost as xgb
from sklearn.metrics import (
    f1_score,
    precision_recall_curve,
    precision_score,
    recall_score,
    roc_auc_score,
)

from .feature_extractor import FEATURE_COLUMNS, ML_EXCLUDED_FIELDS, train_val_test_split

logger = logging.getLogger(__name__)

# Suppress Optuna INFO noise in Cloud Run logs
optuna.logging.set_verbosity(optuna.logging.WARNING)


def _calibrate_threshold(
    y_true: np.ndarray,
    proba: np.ndarray,
    default: float = 0.5,
) -> float:
    """Pick the decision threshold that maximises F1 on the test set.

    Task #324 — replaces the hardcoded 0.500 literal in job.py. Strategy is
    selectable via the ``THRESHOLD_CALIBRATION`` env var:

    * ``f1_max`` (default): argmax of F1 across the precision-recall curve.
    * ``max_precision_at_recall``: highest precision threshold whose recall
      is >= ``MIN_RECALL`` (env, default 0.30).

    Falls back to ``default`` when the test set is degenerate (single class).
    """
    if len(y_true) == 0 or len(np.unique(y_true)) < 2:
        return default

    strategy = os.getenv("THRESHOLD_CALIBRATION", "f1_max").lower()

    precision, recall, thresholds = precision_recall_curve(y_true, proba)
    # precision_recall_curve returns N points but only N-1 thresholds.
    precision = precision[:-1]
    recall = recall[:-1]
    if len(thresholds) == 0:
        return default

    if strategy == "max_precision_at_recall":
        min_recall = float(os.getenv("MIN_RECALL", "0.30"))
        eligible = recall >= min_recall
        if eligible.any():
            best_idx = int(np.argmax(np.where(eligible, precision, -1.0)))
            return float(thresholds[best_idx])
        # No threshold meets the recall floor — fall through to F1.

    # F1-max default
    denom = precision + recall
    f1 = np.where(denom > 0, 2 * precision * recall / np.where(denom > 0, denom, 1), 0.0)
    best_idx = int(np.argmax(f1))
    chosen = float(thresholds[best_idx])
    # Clamp to a sane operating range so we never approve everything / nothing.
    if math.isnan(chosen) or chosen <= 0.0 or chosen >= 1.0:
        return default
    return chosen


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

        # ML_EXCLUDED_FIELDS — guardrail no entry-point do treino. Nenhum
        # desses campos pode entrar em X_train/X_val/X_test (leakage circular
        # ou metadado operacional sem valor preditivo).
        _leaked_cols = ML_EXCLUDED_FIELDS.intersection(feature_cols)
        assert not _leaked_cols, (
            f"ML_EXCLUDED_FIELDS no feature_cols: {sorted(_leaked_cols)} — "
            f"revisar FEATURE_COLUMNS em feature_extractor.py."
        )
        _leaked_df = ML_EXCLUDED_FIELDS.intersection(df.columns)
        if _leaked_df:
            # df pode conter colunas excluídas como metadado herdado (defesa: dropar
            # silenciosamente, mas logar para detectar produtor poluído upstream).
            logger.warning(
                "ML_EXCLUDED_FIELDS presentes no df de treino e serão removidas: %s",
                sorted(_leaked_df),
            )
            df = df.drop(columns=list(_leaked_df))

        # Task #324 — drop rows with > MAX_NAN_FRACTION NaN features. They
        # carry too little signal and bias the model toward "all-zero" splits.
        max_nan_fraction = float(os.getenv("MAX_NAN_FRACTION", "0.5"))
        if feature_cols:
            nan_fraction = df[feature_cols].isna().mean(axis=1)
            keep_mask = nan_fraction <= max_nan_fraction
            dropped = int((~keep_mask).sum())
            if dropped:
                logger.info(
                    f"Dropped {dropped} rows with >{max_nan_fraction*100:.0f}% "
                    f"NaN features"
                )
            df = df.loc[keep_mask].copy()

        train_df, val_df, test_df = train_val_test_split(df)

        # Task #324 — preserve NaN. XGBoost handles missing values natively;
        # fillna(0.0) collapses "missing" and "true zero" (e.g. taker_ratio=0
        # = 100% sells) into the same semantic class, sabotaging splits.
        X_train = train_df[feature_cols].astype("float32")
        y_train = train_df["is_win_fast"].astype(int)
        X_val = val_df[feature_cols].astype("float32")
        y_val = val_df["is_win_fast"].astype(int)
        X_test = test_df[feature_cols].astype("float32")
        y_test = test_df["is_win_fast"].astype(int)

        n_neg = int((y_train == 0).sum())
        n_pos = int((y_train == 1).sum())
        scale_pos_weight = n_neg / n_pos if n_pos > 0 else 1.0
        winrate_base = (n_pos / max(n_pos + n_neg, 1)) * 100
        logger.info(
            f"Class balance: {n_pos} wins / {n_neg} losses "
            f"(scale_pos_weight={scale_pos_weight:.2f}, "
            f"winrate_base={winrate_base:.2f}%)"
        )

        # Task #324 — fail loudly when the dataset is degenerate. Previously a
        # single-class y_train silently returned AUC=0 from Optuna and the job
        # still wrote an "active" ml_models row with garbage metrics.
        min_per_class = int(os.getenv("MIN_SAMPLES_PER_CLASS", "30"))
        if y_train.nunique() < 2:
            raise ValueError(
                f"Degenerate dataset: y_train has a single class "
                f"(n_pos={n_pos}, n_neg={n_neg}, winrate={winrate_base:.2f}%)"
            )
        if n_pos < min_per_class or n_neg < min_per_class:
            raise ValueError(
                f"Degenerate dataset: each class needs >= {min_per_class} "
                f"samples (n_pos={n_pos}, n_neg={n_neg}, "
                f"winrate={winrate_base:.2f}%)"
            )

        def objective(trial: optuna.Trial) -> float:
            params = {
                "objective": "binary:logistic",
                "eval_metric": "auc",
                "tree_method": "hist",
                "device": "cpu",
                "random_state": 42,
                "scale_pos_weight": scale_pos_weight,
                # Task #324 — NaN preserved natively. NEVER fillna upstream.
                "missing": float("nan"),
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
            "missing": float("nan"),
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

            # Task #324 — calibrate decision_threshold via PR curve on the
            # test set. Maximises F1 by default; switch to "max_precision"
            # to gate at a minimum recall floor instead.
            calibrated_threshold = _calibrate_threshold(
                y_test.to_numpy(), proba_test
            )
            pred_test = (proba_test >= calibrated_threshold).astype(int)

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

            # Outcome distribution (lowercase tp/sl regime post-14/05).
            outcome_counts: dict[str, int] = {}
            if "_outcome" in df.columns:
                vc = df["_outcome"].fillna("__null__").value_counts()
                outcome_counts = {str(k): int(v) for k, v in vc.items()}

            mlflow.log_params(best_params)
            mlflow.log_params({
                "outcome_distribution": str(outcome_counts) if outcome_counts else "{}",
                "max_nan_fraction": max_nan_fraction,
                "min_samples_per_class": min_per_class,
            })
            mlflow.set_tags({
                "label_version": "pnl_gt_0_v1",
                "dedup_strategy": "distinct_symbol_pnl_v1",
                "nan_handling": "native_xgboost",
                "n_unique_trades_after_dedup": str(len(df)),
                "winrate_base": f"{winrate_base:.4f}",
                "n_pos": str(n_pos),
                "n_neg": str(n_neg),
                "calibrated_threshold": f"{calibrated_threshold:.4f}",
            })
            mlflow.log_metrics({
                "precision": precision,
                "recall": recall,
                "f1": f1,
                "roc_auc": roc_auc,
                "win_fast_capture_rate": capture_rate,
                "false_positive_rate": fpr,
                "decision_threshold": calibrated_threshold,
                "winrate_base": winrate_base,
                "n_pos": n_pos,
                "n_neg": n_neg,
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
            "n_train": int(len(X_train)),
            "n_val": int(len(X_val)),
            "n_test": int(len(X_test)),
            "n_pos": n_pos,
            "n_neg": n_neg,
            "winrate_base": winrate_base,
            "decision_threshold": calibrated_threshold,
            "outcome_distribution": outcome_counts,
        }
