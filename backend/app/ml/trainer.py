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

from .feature_extractor import (
    FEATURE_COLUMNS,
    ML_EXCLUDED_FIELDS,
    filter_trainable_features,
    train_val_test_split,
)

logger = logging.getLogger(__name__)

# Suppress Optuna INFO noise in Cloud Run logs
optuna.logging.set_verbosity(optuna.logging.WARNING)


def _calibrate_threshold(
    y_true: np.ndarray,
    proba: np.ndarray,
    default: float = 0.5,
    pnl_values: np.ndarray | None = None,
) -> float:
    """Pick the decision threshold on the test set.

    Strategy is selectable via the ``THRESHOLD_CALIBRATION`` env var:

    * ``pnl_max`` (default): argmax of mean PnL among approved trades at each
      threshold. Economically optimal — directly maximises expected return.
      Requires ``pnl_values`` array; falls back to ``f1_max`` when absent.
    * ``f1_max``: argmax of F1 across the precision-recall curve.
    * ``max_precision_at_recall``: highest precision threshold whose recall
      is >= ``MIN_RECALL`` (env, default 0.30).

    Falls back to ``default`` when the test set is degenerate (single class).
    """
    if len(y_true) == 0 or len(np.unique(y_true)) < 2:
        return default

    strategy = os.getenv("THRESHOLD_CALIBRATION", "pnl_max").lower()

    # pnl_max — maximise expected PnL of approved trades across all thresholds,
    # subject to a minimum recall floor to avoid rejecting too many good trades.
    if strategy == "pnl_max" and pnl_values is not None and len(pnl_values) == len(proba):
        # Evaluate a grid of candidate thresholds (quantiles of score distribution
        # for efficiency; avoids iterating all N unique values).
        candidates = np.unique(np.percentile(proba, np.arange(10, 95, 2)))
        min_approved = int(os.getenv("THRESHOLD_MIN_APPROVED", "10"))
        # Recall floor: don't let pnl_max push threshold so high that recall
        # collapses. Default 0.30 = at least 30% of true positives captured.
        min_recall_floor = float(os.getenv("THRESHOLD_MIN_RECALL", "0.30"))
        best_thresh = default
        best_pnl = -np.inf
        n_positives = int(y_true.sum())
        for t in candidates:
            mask = proba >= t
            if mask.sum() < min_approved:
                continue
            # Recall check: how many actual positives does this threshold capture?
            if n_positives > 0:
                tp = int((y_true[mask] == 1).sum())
                thresh_recall = tp / n_positives
                if thresh_recall < min_recall_floor:
                    continue
            mean_pnl = float(np.mean(pnl_values[mask]))
            if mean_pnl > best_pnl:
                best_pnl = mean_pnl
                best_thresh = float(t)
        # Log final choice
        final_mask = proba >= best_thresh
        final_recall = int((y_true[final_mask] == 1).sum()) / max(n_positives, 1)
        logger.info(
            "pnl_max calibration: threshold=%.4f expected_pnl=%.4f%% "
            "approved=%d/%d recall=%.2f%% (floor=%.0f%%)",
            best_thresh, best_pnl * 100, int(final_mask.sum()), len(proba),
            final_recall * 100, min_recall_floor * 100,
        )
        if math.isnan(best_thresh) or best_thresh <= 0.0 or best_thresh >= 1.0:
            return default
        return best_thresh

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

    # F1-max
    denom = precision + recall
    f1 = np.where(denom > 0, 2 * precision * recall / np.where(denom > 0, denom, 1), 0.0)
    best_idx = int(np.argmax(f1))
    chosen = float(thresholds[best_idx])
    # Clamp to a sane operating range so we never approve everything / nothing.
    if math.isnan(chosen) or chosen <= 0.0 or chosen >= 1.0:
        return default
    return chosen


def _report_bad_approval_drivers(
    model: "xgb.XGBClassifier",
    X_test: pd.DataFrame,
    df_test: pd.DataFrame,
    feature_cols: list,
) -> list:
    """SHAP analysis: quais features empurram aprovações ruins (SL_HIT).

    Retorna lista de (feature, mean_abs_shap) ordenada por impacto desc.
    Loga BAD_APPROVAL_DRIVERS|top5=[...] para Cloud Run + MLflow notes.
    Falha silenciosa se SHAP não instalado ou dataset insuficiente.
    """
    try:
        import shap  # type: ignore
        explainer = shap.TreeExplainer(model)
        sv = explainer.shap_values(X_test)
        loss_mask = (df_test["is_win_fast"] == 0).values
        if loss_mask.sum() < 5:
            logger.info("BAD_APPROVAL_DRIVERS|skipped (n_bad=%d < 5)", loss_mask.sum())
            return []
        impact = {
            col: float(abs(sv[loss_mask, i]).mean())
            for i, col in enumerate(feature_cols)
        }
        ranked = sorted(impact.items(), key=lambda x: x[1], reverse=True)
        logger.info("BAD_APPROVAL_DRIVERS|top5=%s", ranked[:5])
        return ranked
    except ImportError:
        logger.info("BAD_APPROVAL_DRIVERS|shap not installed — skipping")
        return []
    except Exception as exc:
        logger.warning("BAD_APPROVAL_DRIVERS|error: %s", exc)
        return []


def _compute_psi(
    train_series: pd.Series,
    test_series: pd.Series,
    n_bins: int = 10,
) -> float:
    """Population Stability Index between train and test distributions.

    PSI < 0.10  → stable
    PSI 0.10–0.25 → moderate shift (monitor)
    PSI > 0.25  → REGIME_DRIFT_WARNING — model may not generalise

    Uses equal-width bins on the union range. NaN values are excluded.
    Returns 0.0 when either series is empty or single-valued.
    """
    a = train_series.dropna().values
    b = test_series.dropna().values
    if len(a) == 0 or len(b) == 0:
        return 0.0
    _min, _max = min(a.min(), b.min()), max(a.max(), b.max())
    if _min == _max:
        return 0.0
    bins = np.linspace(_min, _max, n_bins + 1)
    e_counts, _ = np.histogram(a, bins=bins)
    a_counts, _ = np.histogram(b, bins=bins)
    # Smooth zeros with 0.5 to avoid log(0)
    e_pct = (e_counts + 0.5) / (e_counts + 0.5).sum()
    a_pct = (a_counts + 0.5) / (a_counts + 0.5).sum()
    return float(np.sum((e_pct - a_pct) * np.log(e_pct / a_pct)))


class WinFastTrainer:
    """
    XGBoost trainer with Optuna hyperparameter optimization.

    Zero Hardcode: all parameters found by Optuna.
    Threshold is set post-training via ml_models.decision_threshold in Cloud SQL.
    """

    def __init__(self, n_trials: int = 50):
        self.n_trials = n_trials
        self.model: Optional[xgb.XGBClassifier] = None

    def train(
        self,
        df: pd.DataFrame,
        optuna_storage_url: Optional[str] = None,
        ml_target: str = "binary",
    ) -> dict:
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

        # BLOCO C — filtro dinâmico de features (coverage + std).
        # Exclui macro features com cobertura < 30% ou std=0 (constante quando presente).
        # Macro features vivas (MDH consertado) entram sozinhas quando coverage subir.
        _min_cov = float(os.getenv("ML_MIN_FEATURE_COVERAGE", "0.30"))
        feature_cols, _features_excluded = filter_trainable_features(
            df, feature_cols, min_coverage=_min_cov
        )

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

        # BLOCO C — PSI entre train e test para detecção de regime drift.
        # Se PSI > 0.25 em qualquer feature-chave, loga REGIME_DRIFT_WARNING.
        _psi_threshold = float(os.getenv("ML_PSI_THRESHOLD", "0.25"))
        _regime_drift_warning = False
        _psi_flagged: list = []
        for _fc in feature_cols[:10]:  # top-10 features para evitar overhead
            _psi = _compute_psi(
                train_df[_fc] if _fc in train_df.columns else pd.Series(dtype=float),
                test_df[_fc]  if _fc in test_df.columns  else pd.Series(dtype=float),
            )
            if _psi > _psi_threshold:
                _psi_flagged.append((_fc, round(_psi, 4)))
                _regime_drift_warning = True
        if _regime_drift_warning:
            logger.warning(
                "REGIME_DRIFT_WARNING|psi_threshold=%.2f|flagged=%s",
                _psi_threshold, _psi_flagged,
            )
        else:
            logger.info("PSI_CHECK|stable|checked_features=%d", min(len(feature_cols), 10))

        # BLOCO C — alvo agnóstico: binary (default) ou regression via ML_TARGET_TYPE.
        # binary:     is_win_fast (0/1) — classificação, AUC como métrica
        # regression: _pnl_pct (contínuo) — regressão, RMSE como métrica
        # Decisão do alvo adiada para após teste de separabilidade WATCHLIST_SPOT.
        is_regression = (ml_target == "regression")

        # Task #324 — preserve NaN. XGBoost handles missing values natively;
        # fillna(0.0) collapses "missing" and "true zero" (e.g. taker_ratio=0
        # = 100% sells) into the same semantic class, sabotaging splits.
        X_train = train_df[feature_cols].astype("float32")
        X_val   = val_df[feature_cols].astype("float32")
        X_test  = test_df[feature_cols].astype("float32")

        if is_regression:
            # Regressão sobre PnL — usa _pnl_pct como target contínuo.
            # Guarda is_win_fast apenas como metadado para winrate logging.
            y_train = train_df["_pnl_pct"].astype("float32")
            y_val   = val_df["_pnl_pct"].astype("float32")
            y_test  = test_df["_pnl_pct"].astype("float32")
        else:
            y_train = train_df["is_win_fast"].astype(int)
            y_val   = val_df["is_win_fast"].astype(int)
            y_test  = test_df["is_win_fast"].astype(int)

        # Parâmetros XGBoost dependentes do alvo
        if is_regression:
            _xgb_objective = "reg:squarederror"
            _xgb_eval_metric = "rmse"
            n_neg = 0
            n_pos = int(len(y_train))
            scale_pos_weight = 1.0
            winrate_base = float(y_train.mean()) if len(y_train) else 0.0
            logger.info(
                f"Regression target: n_samples={n_pos} | mean_pnl={winrate_base:.4f}%"
            )
        else:
            _xgb_objective = "binary:logistic"
            _xgb_eval_metric = "auc"
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
        if not is_regression:
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
        else:
            if len(y_train) < min_per_class * 2:
                raise ValueError(
                    f"Degenerate dataset: regression needs >= {min_per_class * 2} "
                    f"samples (got {len(y_train)})"
                )

        def objective(trial: optuna.Trial) -> float:
            params = {
                "objective": _xgb_objective,
                "eval_metric": _xgb_eval_metric,
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
            # Audit P0-05: Regression branch in Optuna objective — optimise RMSE
            # instead of AUC when ML_TARGET_TYPE=regression.
            if is_regression:
                m = xgb.XGBRegressor(**params, early_stopping_rounds=20)
                m.fit(
                    X_train, y_train,
                    eval_set=[(X_val, y_val)],
                    verbose=False,
                )
                preds = m.predict(X_val)
                from sklearn.metrics import mean_squared_error
                rmse = mean_squared_error(y_val, preds, squared=False)
                return -rmse  # Optuna maximises; negative RMSE → lower is better
            # XGBoost 2.1+: early_stopping_rounds moved from fit() to constructor
            m = xgb.XGBClassifier(**params, early_stopping_rounds=20)
            m.fit(
                X_train, y_train,
                eval_set=[(X_val, y_val)],
                verbose=False,
            )
            if y_val.nunique() < 2:
                return 0.0
            proba = m.predict_proba(X_val)[:, 1]
            val_auc = float(roc_auc_score(y_val, proba))
            # Guard: val_auc >= 0.95 on a small val set strongly indicates
            # memorisation rather than generalisation — trivially achievable
            # with max_depth >= 5 and n_estimators >= 200 on < 30 samples.
            # Penalise to 0.0 so Optuna steers away from overfit configs.
            if val_auc >= 0.95:
                return 0.0
            return val_auc

        study_kwargs: dict = {"direction": "maximize", "study_name": "win_fast_study"}
        if optuna_storage_url:
            study_kwargs["storage"] = optuna_storage_url
            study_kwargs["load_if_exists"] = True

        study = optuna.create_study(**study_kwargs)
        study.optimize(objective, n_trials=self.n_trials, show_progress_bar=False)

        best_params = {
            "objective": _xgb_objective,
            "eval_metric": _xgb_eval_metric,
            "tree_method": "hist",
            "device": "cpu",
            "random_state": 42,
            "scale_pos_weight": scale_pos_weight,
            "missing": float("nan"),
            **study.best_params,
        }
        logger.info(
            f"Best trial: val_metric={study.best_value:.4f} | params={study.best_params}"
        )

        # Final training with MLflow logging
        with mlflow.start_run() as run:
            if is_regression:
                self.model = xgb.XGBRegressor(**best_params, early_stopping_rounds=20)
            else:
                self.model = xgb.XGBClassifier(**best_params, early_stopping_rounds=20)
            self.model.fit(
                X_train, y_train,
                eval_set=[(X_val, y_val)],
                verbose=False,
            )

            if is_regression:
                # Regressão: score = EV previsto (pnl_pct). Métricas de classificação=0.
                pred_test_raw = self.model.predict(X_test)
                proba_test = pred_test_raw  # alias semântico (score contínuo)
                calibrated_threshold = 0.0  # threshold não aplicável
                precision = recall = f1 = roc_auc = 0.0
                win_mask = y_test > 0
                capture_rate = float(win_mask.mean()) if len(win_mask) else 0.0
                fpr = 0.0
            else:
                proba_test = self.model.predict_proba(X_test)[:, 1]

                # Audit P0-06: Calibrate threshold on VALIDATION set, not test set.
                # Calibrating on test data causes data leakage — the threshold
                # would be tuned on the same data used to report final metrics.
                proba_val = self.model.predict_proba(X_val)[:, 1]
                pnl_val: np.ndarray | None = None
                if "_pnl_pct" in val_df.columns:
                    pnl_val = val_df["_pnl_pct"].to_numpy(dtype="float32")
                calibrated_threshold = _calibrate_threshold(
                    y_val.to_numpy(), proba_val, pnl_values=pnl_val
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

            # Outcome distribution (shadow_trades: 'TP_HIT' / 'SL_HIT').
            outcome_counts: dict[str, int] = {}
            if "_outcome" in df.columns:
                vc = df["_outcome"].fillna("__null__").value_counts()
                outcome_counts = {str(k): int(v) for k, v in vc.items()}

            shap_drivers = _report_bad_approval_drivers(
                self.model, X_test, test_df, feature_cols
            )

            mlflow.log_params(best_params)
            mlflow.log_params({
                "outcome_distribution": str(outcome_counts) if outcome_counts else "{}",
                "max_nan_fraction": max_nan_fraction,
                "min_samples_per_class": min_per_class,
                "ml_target": ml_target,
                "features_excluded_count": len(_features_excluded),
                "regime_drift_warning": str(_regime_drift_warning),
            })
            mlflow.set_tags({
                "label_version": "ttt_aware_v2",
                "data_source": f"shadow_trades_{ml_target}",
                "nan_handling": "native_xgboost",
                "n_unique_trades": str(len(df)),
                "winrate_base": f"{winrate_base:.4f}",
                "n_pos": str(n_pos),
                "n_neg": str(n_neg),
                "calibrated_threshold": f"{calibrated_threshold:.4f}",
                "shap_top1": str(shap_drivers[0][0]) if shap_drivers else "n/a",
                "regime_drift_warning": str(_regime_drift_warning),
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
            f"f1={f1:.4f} roc_auc={roc_auc:.4f} capture={capture_rate:.4f} "
            f"regime_drift={_regime_drift_warning} features_excluded={len(_features_excluded)}"
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
            "shap_bad_approval_drivers": shap_drivers[:5] if shap_drivers else [],
            "regime_drift_warning": _regime_drift_warning,
            "features_excluded": [col for col, _ in _features_excluded],
        }
