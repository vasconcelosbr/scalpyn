"""ML Challenger Service — LightGBM and CatBoost challengers para o XGBoost champion.

Treina LightGBM e CatBoost no mesmo dataset de shadow trades usado pelo XGBoost,
usando Optuna para otimização de hiperparâmetros. Registra resultados em ml_models
(BYTEA) e ml_model_registry.

Integração com o PI Engine:
- Chamado pelo profile_intelligence_job quando enable_lightgbm=True ou enable_catboost=True
- Resultados aparecem em GET /profile-intelligence/settings → ml_challengers
- Modelos registrados podem ser promovidos via ml_model_registry
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import math
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

# Q2 council 2026-06-29: hard ceiling on completed_at to exclude shadow monitor
# bug window (2026-06-25 19:45) and regime-change contamination.
# Set TRAIN_CUTOFF_AT="YYYY-MM-DD HH:MM:SS" in the service env. Empty = no cutoff.
_TRAIN_CUTOFF_AT: str = os.getenv("TRAIN_CUTOFF_AT", "")

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_TRAINER_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ml_challenger")

MIN_RECORDS = int(__import__("os").getenv("ML_CHALLENGER_MIN_RECORDS", "200"))
LOOKBACK_DAYS = int(__import__("os").getenv("ML_CHALLENGER_LOOKBACK_DAYS", "60"))
VAL_FRACTION = float(__import__("os").getenv("ML_CHALLENGER_VAL_FRACTION", "0.20"))
N_TRIALS_LGBM = int(__import__("os").getenv("ML_CHALLENGER_N_TRIALS_LGBM", "30"))
N_TRIALS_CB = int(__import__("os").getenv("ML_CHALLENGER_N_TRIALS_CB", "20"))

# Lane 1 (XGBoost challenger): global opportunity signal, no profile bias.
LGBM_TRAIN_SOURCES: List[str] = ["L1_SPECTRUM"]
# Lane 2 (CatBoost validator): L3 only.
CATBOOST_TRAIN_SOURCES: List[str] = ["L3"]
CATBOOST_L3_ONLY_SOURCES: List[str] = ["L3"]
# Backwards-compat alias — callers that pass source_filter still work.
TRAIN_SOURCES: List[str] = LGBM_TRAIN_SOURCES  # was ["L3", "L1_SPECTRUM"] — deprecated

# Error code emitted when combined L3+L3_LAB is requested but gate is closed.
MIXED_SOURCE_BLOCKED_REASON = "MIXED_SOURCE_DATASET_BLOCKED"


_PROFILE_NULL_BUCKET = 9999  # reserved bucket for NULL profile_id


def _stable_profile_bucket(profile_id: Optional[str]) -> int:
    """Deterministic, PYTHONHASHSEED-independent bucket for profile_id (0-9998).

    Python's built-in hash() is randomised per-process (PYTHONHASHSEED).
    Using it for CatBoost categorical encoding causes train/serve mismatch
    across worker restarts — the same profile_id would land in a different
    bucket at inference time. hashlib.md5 is deterministic and process-stable.

    NULL profile_id → _PROFILE_NULL_BUCKET (9999), never overlaps with hashed range.
    """
    if not profile_id:
        return _PROFILE_NULL_BUCKET
    return int(hashlib.md5(profile_id.encode()).hexdigest()[:8], 16) % 9999


def _is_installed(package: str) -> bool:
    try:
        __import__(package)
        return True
    except (ImportError, OSError):
        return False


def get_challenger_status() -> Dict[str, Any]:
    """Retorna status real de LightGBM e CatBoost baseado em imports reais."""
    sklearn_ok = _is_installed("sklearn") or _is_installed("sklearn.metrics")
    pandas_ok = _is_installed("pandas")

    def _status(package: str) -> Dict[str, Any]:
        installed = _is_installed(package)
        operational = installed and sklearn_ok and pandas_ok
        return {
            "available": installed,
            "implemented": True,
            "installed": installed,
            "operational": operational,
            "status": "operational" if operational else ("not_installed" if not installed else "dependency_missing"),
            "effective_contribution": 1 if operational else 0,
            "can_train": operational,
            "can_infer": operational,
            "can_generate_suggestions": operational,
            "influences_autopilot": operational,
        }

    return {
        "lightgbm": _status("lightgbm"),
        "catboost": _status("catboost"),
    }


# ---------------------------------------------------------------------------
# Sync training functions (run in thread pool — CPU-bound)
# ---------------------------------------------------------------------------

def _train_lgbm_sync(
    X_train, y_train, X_val, y_val,
    n_trials: int = 30,
    X_test=None, y_test=None,
) -> Dict[str, Any]:
    import lightgbm as lgb
    import numpy as np
    import optuna
    from sklearn.metrics import roc_auc_score, average_precision_score, f1_score, precision_score, recall_score

    optuna.logging.set_verbosity(optuna.logging.WARNING)

    dtrain = lgb.Dataset(X_train, label=y_train)
    dval = lgb.Dataset(X_val, label=y_val, reference=dtrain)

    best_params: Dict[str, Any] = {}

    def objective(trial: optuna.Trial) -> float:
        params = {
            "objective": "binary",
            "metric": "auc",
            "verbosity": -1,
            "boosting_type": "gbdt",
            "feature_pre_filter": False,
            "n_estimators": trial.suggest_int("n_estimators", 100, 600),
            "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.3, log=True),
            "num_leaves": trial.suggest_int("num_leaves", 15, 127),
            "min_child_samples": trial.suggest_int("min_child_samples", 10, 100),
            "feature_fraction": trial.suggest_float("feature_fraction", 0.4, 1.0),
            "bagging_fraction": trial.suggest_float("bagging_fraction", 0.4, 1.0),
            "bagging_freq": trial.suggest_int("bagging_freq", 1, 7),
            "reg_alpha": trial.suggest_float("reg_alpha", 1e-8, 10.0, log=True),
            "reg_lambda": trial.suggest_float("reg_lambda", 1e-8, 10.0, log=True),
        }
        callbacks = [lgb.early_stopping(20, verbose=False), lgb.log_evaluation(-1)]
        model = lgb.train(params, dtrain, valid_sets=[dval], callbacks=callbacks)
        preds = model.predict(X_val)
        return float(roc_auc_score(y_val, preds))

    study = optuna.create_study(direction="maximize")
    study.optimize(objective, n_trials=n_trials, timeout=180, show_progress_bar=False)

    best_params = {
        **study.best_params,
        "objective": "binary",
        "metric": "auc",
        "verbosity": -1,
    }
    callbacks = [lgb.early_stopping(40, verbose=False), lgb.log_evaluation(-1)]
    final_model = lgb.train(best_params, dtrain, valid_sets=[dval], callbacks=callbacks)

    val_preds = final_model.predict(X_val)
    roc_auc = float(roc_auc_score(y_val, val_preds))
    pr_auc = float(average_precision_score(y_val, val_preds))
    threshold = float(np.median(val_preds))
    binary_preds = (val_preds >= threshold).astype(int)
    f1 = float(f1_score(y_val, binary_preds, zero_division=0))
    prec = float(precision_score(y_val, binary_preds, zero_division=0))
    rec = float(recall_score(y_val, binary_preds, zero_division=0))
    tn = int(((binary_preds == 0) & (np.asarray(y_val) == 0)).sum())
    fp = int(((binary_preds == 1) & (np.asarray(y_val) == 0)).sum())
    fpr = fp / (fp + tn) if (fp + tn) > 0 else 0.0

    test_metrics: Dict[str, Any] = {}
    if X_test is not None and y_test is not None and len(y_test) >= 5:
        t_preds = final_model.predict(X_test)
        t_bin = (t_preds >= threshold).astype(int)
        t_tn = int(((t_bin == 0) & (np.asarray(y_test) == 0)).sum())
        t_fp = int(((t_bin == 1) & (np.asarray(y_test) == 0)).sum())
        test_metrics = {
            "roc_auc": float(roc_auc_score(y_test, t_preds)),
            "pr_auc": float(average_precision_score(y_test, t_preds)),
            "f1": float(f1_score(y_test, t_bin, zero_division=0)),
            "precision": float(precision_score(y_test, t_bin, zero_division=0)),
            "recall": float(recall_score(y_test, t_bin, zero_division=0)),
            "fpr": t_fp / (t_fp + t_tn) if (t_fp + t_tn) > 0 else 0.0,
            "samples": int(len(y_test)),
            "positive_rate": float(np.asarray(y_test).mean()),
        }

    return {
        "model": final_model,
        "model_type": "lightgbm",
        "best_params": study.best_params,
        "metrics": {
            "roc_auc": roc_auc,
            "pr_auc": pr_auc,
            "f1": f1,
            "precision": prec,
            "recall": rec,
            "fpr": fpr,
            "n_trials": n_trials,
            "best_trial_number": study.best_trial.number,
            "best_trial_value": study.best_trial.value,
            "val_samples": int(len(y_val)),
            "train_samples": int(len(y_train)),
            "positive_rate": float(y_val.mean()) if hasattr(y_val, "mean") else 0.0,
        },
        "test_metrics": test_metrics,
        "threshold": threshold,
    }


def _train_catboost_sync(
    X_train, y_train, X_val, y_val,
    feature_names: List[str],
    n_trials: int = 20,
    cat_feature_indices: Optional[List[int]] = None,
    X_test=None, y_test=None,
) -> Dict[str, Any]:
    from catboost import CatBoostClassifier, Pool
    import numpy as np
    import optuna
    from sklearn.metrics import roc_auc_score, average_precision_score, f1_score, precision_score, recall_score

    import pandas as pd

    optuna.logging.set_verbosity(optuna.logging.WARNING)

    def _make_pool(X, y):
        if cat_feature_indices:
            # CatBoost rejects float numpy arrays with cat_features — use a
            # DataFrame with categorical columns converted to string so CatBoost
            # can apply its ordered target statistics encoding.
            df = pd.DataFrame(X, columns=list(feature_names))
            cat_names = [list(feature_names)[i] for i in cat_feature_indices]
            for name in cat_names:
                df[name] = df[name].astype(int).astype(str)
            return Pool(df, label=y, cat_features=cat_names)
        return Pool(X, label=y, feature_names=list(feature_names))

    train_pool = _make_pool(X_train, y_train)
    val_pool = _make_pool(X_val, y_val)

    def objective(trial: optuna.Trial) -> float:
        params = {
            "iterations": trial.suggest_int("iterations", 200, 800),
            "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.3, log=True),
            "depth": trial.suggest_int("depth", 4, 10),
            "l2_leaf_reg": trial.suggest_float("l2_leaf_reg", 1.0, 10.0),
            "min_data_in_leaf": trial.suggest_int("min_data_in_leaf", 1, 50),
            "random_strength": trial.suggest_float("random_strength", 0.1, 10.0),
            "verbose": False,
            "eval_metric": "AUC",
            "random_seed": 42,
            "allow_writing_files": False,
        }
        model = CatBoostClassifier(**params)
        model.fit(train_pool, eval_set=val_pool, early_stopping_rounds=20, verbose=False)
        preds = model.predict_proba(val_pool)[:, 1]
        return float(roc_auc_score(y_val, preds))

    study = optuna.create_study(direction="maximize")
    study.optimize(objective, n_trials=n_trials, timeout=180, show_progress_bar=False)

    final_params = {
        **study.best_params,
        "verbose": False,
        "eval_metric": "AUC",
        "random_seed": 42,
        "allow_writing_files": False,
    }
    final_model = CatBoostClassifier(**final_params)
    final_model.fit(train_pool, eval_set=val_pool, early_stopping_rounds=40, verbose=False)

    val_preds = final_model.predict_proba(val_pool)[:, 1]
    roc_auc = float(roc_auc_score(y_val, val_preds))
    pr_auc = float(average_precision_score(y_val, val_preds))
    threshold = float(np.median(val_preds))
    binary_preds = (val_preds >= threshold).astype(int)
    f1 = float(f1_score(y_val, binary_preds, zero_division=0))
    prec = float(precision_score(y_val, binary_preds, zero_division=0))
    rec = float(recall_score(y_val, binary_preds, zero_division=0))
    tn = int(((binary_preds == 0) & (np.asarray(y_val) == 0)).sum())
    fp = int(((binary_preds == 1) & (np.asarray(y_val) == 0)).sum())
    fpr = fp / (fp + tn) if (fp + tn) > 0 else 0.0

    test_metrics: Dict[str, Any] = {}
    if X_test is not None and y_test is not None and len(y_test) >= 5:
        test_pool = _make_pool(X_test, y_test)
        t_preds = final_model.predict_proba(test_pool)[:, 1]
        t_bin = (t_preds >= threshold).astype(int)
        t_tn = int(((t_bin == 0) & (np.asarray(y_test) == 0)).sum())
        t_fp = int(((t_bin == 1) & (np.asarray(y_test) == 0)).sum())
        test_metrics = {
            "roc_auc": float(roc_auc_score(y_test, t_preds)),
            "pr_auc": float(average_precision_score(y_test, t_preds)),
            "f1": float(f1_score(y_test, t_bin, zero_division=0)),
            "precision": float(precision_score(y_test, t_bin, zero_division=0)),
            "recall": float(recall_score(y_test, t_bin, zero_division=0)),
            "fpr": t_fp / (t_fp + t_tn) if (t_fp + t_tn) > 0 else 0.0,
            "samples": int(len(y_test)),
            "positive_rate": float(np.asarray(y_test).mean()),
        }

    return {
        "model": final_model,
        "model_type": "catboost",
        "best_params": study.best_params,
        "metrics": {
            "roc_auc": roc_auc,
            "pr_auc": pr_auc,
            "f1": f1,
            "precision": prec,
            "recall": rec,
            "fpr": fpr,
            "n_trials": n_trials,
            "best_trial_number": study.best_trial.number,
            "best_trial_value": study.best_trial.value,
            "val_samples": int(len(y_val)),
            "train_samples": int(len(y_train)),
            "positive_rate": float(y_val.mean()) if hasattr(y_val, "mean") else 0.0,
        },
        "test_metrics": test_metrics,
        "threshold": threshold,
    }


# ---------------------------------------------------------------------------
# Main service
# ---------------------------------------------------------------------------

class MLChallengerService:
    """
    Treina LightGBM e CatBoost challengers em shadow trades do usuário.

    Os modelos são serializados com joblib e armazenados em ml_models (BYTEA).
    O model_id fica registrado em ml_model_registry para tracking de champion/challenger.
    """

    async def _load_shadow_data(
        self,
        db: AsyncSession,
        user_id: UUID,
        lookback_days: int,
        source_filter: Optional[List[str]] = None,
        require_profile_id: bool = False,
    ) -> List[Dict[str, Any]]:
        sources = source_filter if source_filter is not None else TRAIN_SOURCES
        # Build per-source placeholders to avoid any injection risk
        source_placeholders = ", ".join(f":src_{i}" for i in range(len(sources)))
        source_params = {f"src_{i}": s for i, s in enumerate(sources)}
        profile_clause = "AND profile_id IS NOT NULL" if require_profile_id else ""
        cutoff_clause = ""
        cutoff_params: dict = {}
        if _TRAIN_CUTOFF_AT:
            from datetime import datetime as _dt
            _cutoff_dt = _dt.fromisoformat(_TRAIN_CUTOFF_AT).replace(tzinfo=timezone.utc)
            cutoff_clause = "AND completed_at < :train_cutoff_at"
            cutoff_params = {"train_cutoff_at": _cutoff_dt}
            logger.info("[MLChallenger] _load_shadow_data cutoff: completed_at < %s", _TRAIN_CUTOFF_AT)
        rows = (await db.execute(text(f"""
            SELECT
                id::text          AS shadow_id,
                symbol,
                source,
                pnl_pct,
                net_return_pct,
                holding_seconds,
                outcome,
                features_snapshot,
                created_at,
                ttt_fast_win_bucket,
                profile_id::text  AS profile_id
            FROM shadow_trades
            WHERE user_id = :uid
              AND source IN ({source_placeholders})
              AND outcome IN ('TP_HIT', 'SL_HIT', 'TIMEOUT')
              AND pnl_pct IS NOT NULL
              AND features_snapshot IS NOT NULL
              AND features_snapshot::text <> '{{}}'
              AND created_at >= :cutoff
              {profile_clause}
              {cutoff_clause}
            ORDER BY created_at ASC
        """), {"uid": str(user_id), "cutoff": datetime.now(timezone.utc) - timedelta(days=lookback_days),
               **source_params, **cutoff_params})).fetchall()
        logger.info(
            "[MLChallenger] _load_shadow_data: sources=%s rows=%d require_profile_id=%s user=%s",
            sources, len(rows), require_profile_id, user_id,
        )
        return [dict(r._mapping) for r in rows]

    @staticmethod
    def _l3_strict_meta(
        all_records: List[Dict[str, Any]],
        strict_records: List[Dict[str, Any]],
        sources: List[str],
    ) -> Dict[str, Any]:
        """Audit metadata for the L3_PROFILE_STRICT dataset policy."""
        excluded = len(all_records) - len(strict_records)
        distinct = len({r["profile_id"] for r in strict_records if r.get("profile_id")})
        unknown = sum(1 for r in strict_records if not r.get("profile_id"))
        src_breakdown = {s: sum(1 for r in strict_records if r.get("source") == s) for s in sources}
        return {
            "dataset_policy": "L3_PROFILE_STRICT",
            "included_trade_count": len(strict_records),
            "excluded_null_profile_id": excluded,
            "unknown_profile_count": unknown,
            "unknown_profile_pct": round(100.0 * unknown / max(len(strict_records), 1), 2),
            "distinct_profiles": distinct,
            "source_breakdown": src_breakdown,
        }

    # Ordinal encoding for shadow trade source (stable across versions)
    _SOURCE_ENCODING: Dict[str, int] = {
        "L1_SPECTRUM": 0,
        "L3": 1,
        "L3_REJECTED": 3,
    }

    def _build_dataset(
        self,
        records: List[Dict[str, Any]],
        feature_columns: List[str],
        win_fast_threshold_s: float = 1800.0,
    ):
        """Constrói feature matrix e labels usando o feature_extractor canônico."""
        import numpy as np
        from app.ml.feature_extractor import build_training_dataframe

        df = build_training_dataframe(
            records,
            fee_roundtrip_pct=0.0,
            label_net_of_fees=False,
            win_fast_threshold_s=win_fast_threshold_s,
        )

        available = [c for c in feature_columns if c in df.columns]
        X = df[available].fillna(0.0).values.astype(float)

        if "label" in df.columns:
            y = df["label"].values.astype(int)
        elif "is_win_fast" in df.columns:
            y = df["is_win_fast"].values.astype(int)
        else:
            y = np.zeros(len(df), dtype=int)

        return X, y, available

    def _build_l3_dataset(
        self,
        records: List[Dict[str, Any]],
        feature_columns: List[str],
        win_fast_threshold_s: float = 1800.0,
    ):
        """Constrói dataset L3 para CatBoost com features categóricas adicionais.

        Appends source_encoded (ordinal) e profile_id_encoded (hash) como
        features numéricas extras ao final do vector — CatBoost usa internamente
        para splitting por profile. As colunas categóricas ficam APÓS as base
        features para não perturbar o índice do modelo L1.
        """
        import numpy as np
        from app.ml.feature_extractor import build_training_dataframe

        # Pre-filter para alinhar com o que build_training_dataframe vai manter.
        # build_training_dataframe faz `continue` em pnl_pct is None; mantendo
        # a mesma filtragem aqui garantimos que zip(valid, df.iterrows) é válido.
        valid_records = [r for r in records if r.get("pnl_pct") is not None]

        df = build_training_dataframe(
            valid_records,
            fee_roundtrip_pct=0.0,
            label_net_of_fees=False,
            win_fast_threshold_s=win_fast_threshold_s,
        )

        available = [c for c in feature_columns if c in df.columns]
        X_base = df[available].fillna(0.0).values.astype(float)

        if "is_win_fast" in df.columns:
            y = df["is_win_fast"].values.astype(int)
        elif "label" in df.columns:
            y = df["label"].values.astype(int)
        else:
            y = np.zeros(len(df), dtype=int)

        # Categorical encoding — dois scalars por row.
        # profile_id_encoded usa _stable_profile_bucket (hashlib.md5, determinístico)
        # em vez de hash() (PYTHONHASHSEED-randomised). NULL profile_id → bucket 9999.
        source_enc = np.array(
            [self._SOURCE_ENCODING.get(r.get("source", "L3"), 1) for r in valid_records],
            dtype=float,
        )
        profile_enc = np.array(
            [_stable_profile_bucket(r.get("profile_id")) for r in valid_records],
            dtype=float,
        )

        # Stack: base features + source_encoded + profile_id_encoded
        X = np.column_stack([X_base, source_enc, profile_enc])
        all_feature_names = available + ["source_encoded", "profile_id_encoded"]

        # Índices das colunas categóricas — usados por Pool(cat_features=...) no CatBoost.
        # Q3 (council 2026-06-29): profile_id_encoded removido de cat_features — permanece
        # como feature numérica (hash bucket 0-9999). Com 75 profiles e win_rates homogêneas
        # (13-17%), tratá-lo como categoria gera memorização por target encoding de alta
        # cardinalidade sem sinal discriminativo real. source_encoded permanece categórico.
        n_base = X_base.shape[1]
        cat_feature_indices = [n_base]  # source_encoded only; profile_id_encoded is numeric

        return X, y, all_feature_names, cat_feature_indices

    def _chronological_split(self, X, y, val_fraction: float = 0.20):
        n = len(y)
        split = max(1, int(n * (1.0 - val_fraction)))
        return X[:split], y[:split], X[split:], y[split:]

    def _chronological_split_with_test(
        self, X, y, val_fraction: float = 0.20, test_fraction: float = 0.20
    ):
        """60/20/20 temporal split. Test set is the most-recent slice, never seen
        during Optuna hyper-param optimisation — provides unbiased evaluation.

        Returns (X_train, y_train, X_val, y_val, X_test, y_test).
        If the dataset is too small to populate all three sets with at least
        MIN_SET_SIZE samples, falls back to an empty test set rather than
        crashing. Optuna always sees only X_val.
        """
        MIN_SET_SIZE = 5
        n = len(y)
        train_end = max(1, int(n * (1.0 - val_fraction - test_fraction)))
        val_end = max(train_end + MIN_SET_SIZE, int(n * (1.0 - test_fraction)))
        val_end = min(val_end, n - MIN_SET_SIZE)
        if val_end <= train_end or n - val_end < MIN_SET_SIZE:
            # Not enough data for test — degrade gracefully to no test split
            split = max(1, int(n * (1.0 - val_fraction)))
            return X[:split], y[:split], X[split:], y[split:], None, None
        return (
            X[:train_end], y[:train_end],
            X[train_end:val_end], y[train_end:val_end],
            X[val_end:], y[val_end:],
        )

    async def _next_version(self, db: AsyncSession) -> str:
        row = (await db.execute(
            text("SELECT COALESCE(MAX(version::integer), 0) + 1 FROM ml_models WHERE version ~ '^[0-9]+$'")
        )).scalar()
        return str(row or 1)

    async def _save_to_db(
        self,
        db: AsyncSession,
        model_type: str,
        model_obj: Any,
        feature_columns: List[str],
        metrics: Dict[str, Any],
        threshold: float,
        profile_id: Optional[UUID],
        user_id: UUID,
        model_lane: Optional[str] = None,
        cat_feature_indices: Optional[List[int]] = None,
        test_metrics: Optional[Dict[str, Any]] = None,
        win_fast_threshold_s: float = 1800.0,
    ) -> UUID:
        """Serializa e salva em ml_models + ml_model_registry. Retorna model_id."""
        import joblib as _joblib

        buf = io.BytesIO()
        payload = {
            "model": model_obj,
            "feature_columns": feature_columns,
            "metadata": {
                "trained_at": datetime.now(timezone.utc).isoformat(),
                "model_type": model_type,
                "n_features": len(feature_columns),
                "metrics": metrics,
                "threshold": threshold,
                "trained_by": "MLChallengerService",
                "cat_feature_indices": cat_feature_indices,
            },
        }
        _joblib.dump(payload, buf)
        model_blob = buf.getvalue()

        model_uuid = uuid4()
        now = datetime.now(timezone.utc)
        version = await self._next_version(db)

        roc_auc = metrics.get("roc_auc", 0.0)
        f1 = metrics.get("f1", 0.0)
        precision = metrics.get("precision", None)
        recall = metrics.get("recall", None)
        fpr = metrics.get("fpr", None)
        n_train = metrics.get("train_samples", 0)
        n_val = metrics.get("val_samples", 0)
        n_test = (test_metrics or {}).get("samples", 0)
        # Full hyperparams blob: val metrics + test metrics side-by-side
        hyperparams_full = {**metrics}
        if test_metrics:
            hyperparams_full["test_metrics"] = test_metrics

        # Infere lane a partir do model_type se não fornecida explicitamente
        if model_lane is None:
            model_lane = "L3_PROFILE" if model_type == "catboost" else "L1_SPECTRUM"

        test_roc = (test_metrics or {}).get("roc_auc")
        test_prec = (test_metrics or {}).get("precision")
        test_rec = (test_metrics or {}).get("recall")

        # Compute stable feature contract identifiers
        try:
            from app.ml.feature_extractor import (
                feature_columns_hash as _fc_hash,
                FEATURE_SCHEMA_VERSION as _FSV,
                label_version_for_threshold as _lv_for_threshold,
            )
            fc_hash = _fc_hash(feature_columns)
            fc_schema_ver = _FSV
            label_ver = _lv_for_threshold(win_fast_threshold_s)
        except Exception:
            fc_hash = None
            fc_schema_ver = None
            label_ver = "is_win_fast_v1"

        # train_sources is injected into `metrics` by train_challengers() callers
        # (e.g. metrics={**lgbm_result["metrics"], "train_sources": lgbm_sources}).
        # Audit P1-9 fix: source_filter/dataset_contract_id were columns that
        # existed since migration 101 but were never populated by any INSERT.
        _train_sources = metrics.get("train_sources") or []
        source_filter_str = ",".join(sorted(_train_sources)) if _train_sources else None

        import hashlib as _hashlib
        if source_filter_str and label_ver and model_lane and fc_hash:
            dataset_contract_id = _hashlib.sha256(
                f"{label_ver}|{model_lane}|{source_filter_str}|{fc_hash}".encode()
            ).hexdigest()[:32]
        else:
            dataset_contract_id = None

        # Feature importance — extracted from trained model object, persisted in
        # metrics_json for drift analysis and feature selection audits.
        _feature_importance: dict = {}
        try:
            _fi_raw = None
            if hasattr(model_obj, "feature_importances_"):
                _fi_raw = list(model_obj.feature_importances_)
            elif hasattr(model_obj, "feature_importance"):
                _fi_raw = list(model_obj.feature_importance(importance_type="gain"))
            elif hasattr(model_obj, "get_feature_importance"):
                _fi_raw = list(model_obj.get_feature_importance())
            if _fi_raw and len(_fi_raw) == len(feature_columns):
                _fi_total = sum(_fi_raw) or 1.0
                _feature_importance = {
                    feature_columns[i]: round(float(_fi_raw[i]) / _fi_total, 6)
                    for i in range(len(feature_columns))
                }
        except Exception as _fi_exc:
            logger.debug("[MLChallenger] feature_importance extraction failed: %s", _fi_exc)

        # Structured metrics_json — separates validation from test set
        _metrics_json_dict = {
            "label_version": label_ver,
            "target_window_seconds": int(win_fast_threshold_s),
            "validation": {
                "precision": precision,
                "recall": recall,
                "fpr": fpr,
                "f1": f1,
                "roc_auc": roc_auc,
                "samples": n_val,
            },
            "test": {
                "precision": (test_metrics or {}).get("precision"),
                "recall": (test_metrics or {}).get("recall"),
                "fpr": (test_metrics or {}).get("fpr"),
                "f1": (test_metrics or {}).get("f1"),
                "roc_auc": (test_metrics or {}).get("roc_auc"),
                "samples": n_test or None,
            } if test_metrics else None,
            "feature_importance": _feature_importance or None,
        }

        # Promotion Gate — evaluate eligibility at creation time (audit P0-1 fix).
        # A model is born 'candidate' regardless of gate outcome (no auto-promotion
        # happens here); the gate result is persisted so it's visible immediately
        # and so the eligibility filter used by inference/ranking can rely on it
        # without requiring a separate backfill step for newly trained models.
        from app.ml.promotion_gate import evaluate_promotion_gate, merge_promotion_gate_into_metrics_json
        _gate_input = {
            "metrics_json": _metrics_json_dict,
            "roc_auc": roc_auc,
            "test_samples": n_test or None,
            "feature_count": len(feature_columns),
            "label_version": label_ver,
            "model_lane": model_lane,
            "source_filter": source_filter_str,
            "dataset_contract_id": dataset_contract_id,
        }
        _gate_result = evaluate_promotion_gate(_gate_input)
        _metrics_json_dict = merge_promotion_gate_into_metrics_json(_metrics_json_dict, _gate_result)
        _metrics_json = json.dumps(_metrics_json_dict)
        logger.info(
            "[MLChallenger] PromotionGate model_type=%s lane=%s status=%s reasons=%s",
            model_type, model_lane, _gate_result["status"], _gate_result["reasons"],
        )

        # Armazena em ml_models (storage BYTEA canônico)
        await db.execute(text("""
            INSERT INTO ml_models (
                id, version, status,
                hyperparams, train_samples, val_samples, test_samples,
                f1_score, roc_auc, precision_score, recall_score, false_positive_rate,
                feature_columns_json, feature_count,
                feature_columns_hash, feature_schema_version,
                model_path, decision_threshold,
                notes, model_blob,
                model_scope, profile_id,
                label_version, model_lane,
                metrics_json, target_window_seconds,
                source_filter, dataset_contract_id
            ) VALUES (
                :id, :version, 'candidate',
                :hyperparams, :n_train, :n_val, :n_test,
                :f1, :roc_auc, :precision, :recall, :fpr,
                :feature_columns_json, :feature_count,
                :feature_columns_hash, :feature_schema_version,
                :model_path, :threshold,
                :notes, :blob,
                :scope, :pid,
                :label_version, :model_lane,
                :metrics_json, :target_window_seconds,
                :source_filter, :dataset_contract_id
            )
        """), {
            "id": str(model_uuid),
            "version": version,
            "hyperparams": json.dumps(hyperparams_full),
            "n_train": n_train,
            "n_val": n_val,
            "n_test": n_test or None,
            "f1": f1,
            "roc_auc": roc_auc,
            "precision": precision,
            "recall": recall,
            "fpr": fpr,
            "feature_columns_json": json.dumps(feature_columns),
            "feature_count": len(feature_columns),
            "feature_columns_hash": fc_hash,
            "feature_schema_version": fc_schema_ver,
            "model_path": f"db://ml_models/{model_type}_v{version}",
            "threshold": threshold,
            "notes": (
                f"Challenger {model_type} | lane={model_lane} | user_id={user_id} | "
                f"label={label_ver} | win_threshold_s={int(win_fast_threshold_s)} | "
                f"roc_auc={roc_auc:.4f} | "
                f"prec={f'{precision:.4f}' if precision is not None else 'N/A'} | "
                f"rec={f'{recall:.4f}' if recall is not None else 'N/A'} | "
                f"fpr={f'{fpr:.4f}' if fpr is not None else 'N/A'} | "
                f"test_roc={f'{test_roc:.4f}' if test_roc is not None else 'N/A'} | "
                f"test_prec={f'{test_prec:.4f}' if test_prec is not None else 'N/A'} | "
                f"test_rec={f'{test_rec:.4f}' if test_rec is not None else 'N/A'} | "
                f"n_test={n_test} | v{version} | trained_by=MLChallengerService"
            ),
            "blob": model_blob,
            "label_version": label_ver,
            "model_lane": model_lane,
            "scope": "profile" if profile_id else "global",
            "pid": str(profile_id) if profile_id else None,
            "metrics_json": _metrics_json,
            "target_window_seconds": int(win_fast_threshold_s),
            "source_filter": source_filter_str,
            "dataset_contract_id": dataset_contract_id,
        })

        # Registra em ml_model_registry (champion/challenger tracking)
        version_str = f"{model_type}_v{now.strftime('%Y%m%d_%H%M')}"
        await db.execute(text("""
            INSERT INTO ml_model_registry (
                model_id, source_ml_model_id,
                model_type, model_version,
                profile_id, profile_name,
                strategy_skill, market_regime,
                metrics_json, threshold,
                status,
                created_at, updated_at
            ) VALUES (
                :mid, :mid,
                :model_type, :version,
                :pid, NULL,
                'win_fast', 'all',
                :metrics, :threshold,
                'candidate',
                :now, :now
            )
        """), {
            "mid": str(model_uuid),
            "model_type": model_type,
            "version": version_str,
            "pid": str(profile_id) if profile_id else None,
            "metrics": json.dumps(metrics),
            "threshold": threshold,
            "now": now,
        })

        logger.info(
            "[MLChallenger] Registered %s model_id=%s roc_auc=%.4f version=%s",
            model_type, model_uuid, roc_auc, version_str,
        )
        return model_uuid

    @staticmethod
    def _check_mixed_source_gate(cb_sources: List[str]) -> Optional[str]:
        """Return a blocked reason string if combined L3+L3_LAB is detected.

        The L3+L3_LAB combination is blocked by default because of the
        source-composition shift documented in the v42 audit: train=79.7% L3_LAB,
        test=91.4% L3 → AUC inversion.  Callers must pass a single-source list.
        """
        has_l3      = "L3" in cb_sources
        has_l3_lab  = "L3_LAB" in cb_sources
        if has_l3 and has_l3_lab:
            return MIXED_SOURCE_BLOCKED_REASON
        return None

    async def train_challengers(
        self,
        db: AsyncSession,
        user_id: UUID,
        enable_lightgbm: bool = True,
        enable_catboost: bool = True,
        lookback_days: int = LOOKBACK_DAYS,
        n_trials_lgbm: int = N_TRIALS_LGBM,
        n_trials_cb: int = N_TRIALS_CB,
        profile_id: Optional[UUID] = None,
        source_filter: Optional[List[str]] = None,
        lgbm_source_filter: Optional[List[str]] = None,
        catboost_source_filter: Optional[List[str]] = None,
        win_fast_threshold_s: float = 1800.0,
        allow_mixed_source: bool = False,
    ) -> Dict[str, Any]:
        """
        Treina challengers habilitados e registra no banco.

        Arquitetura 2-lanes:
          - LightGBM (Lane 1): L1_SPECTRUM — global opportunity filter
          - CatBoost  (Lane 2): L3_ONLY ou L3_LAB_ONLY — policy separada por source

        IMPORTANTE: combinar L3+L3_LAB no CatBoost está BLOQUEADO por padrão.
        A auditoria do v42 mostrou que o source composition shift (80% L3_LAB treino
        → 91% L3 test) causou inversão de AUC no hold-out. Use catboost_source_filter
        com apenas um source, ou passe allow_mixed_source=True explicitamente
        (não recomendado).

        Parâmetros:
            catboost_source_filter: ['L3'] ou ['L3_LAB'] (nunca ambos sem allow_mixed_source)
            lgbm_source_filter: override para fontes do LightGBM (default: LGBM_TRAIN_SOURCES)
            source_filter: override legacy (aplicado a ambos se lgbm/catboost não fornecidos)
            allow_mixed_source: bypass do gate L3+L3_LAB (False por padrão)
        """
        if not enable_lightgbm and not enable_catboost:
            return {"skipped": "no_challengers_enabled"}

        # Mixed-source gate: run BEFORE any expensive imports so the block
        # is returned even when feature_extractor is unavailable.
        if enable_catboost and not allow_mixed_source:
            _early_cb_sources = catboost_source_filter or (
                source_filter if source_filter else CATBOOST_L3_ONLY_SOURCES
            )
            _early_block = self._check_mixed_source_gate(_early_cb_sources)
            if _early_block:
                logger.warning(
                    "[MLChallenger] CatBoost BLOCKED (early): %s (sources=%s user=%s)",
                    _early_block, _early_cb_sources, user_id,
                )
                return {
                    "catboost": {
                        "status": "blocked",
                        "reason": _early_block,
                        "sources": _early_cb_sources,
                        "message": (
                            "CatBoost L3+L3_LAB combined training is disabled. "
                            "v42 audit: source composition shift (train=79.7% L3_LAB → "
                            "test=91.4% L3) caused AUC inversion (val=0.707 → test=0.422). "
                            "Pass catboost_source_filter=['L3'] or ['L3_LAB'], "
                            "or allow_mixed_source=True to override (not recommended)."
                        ),
                    }
                }

        try:
            from app.ml.feature_extractor import FEATURE_COLUMNS as _FC
            feature_columns = list(_FC)
        except ImportError:
            logger.warning("[MLChallenger] feature_extractor não disponível")
            return {"skipped": "feature_extractor_unavailable"}

        results: Dict[str, Any] = {}

        # ── Lane 1: LightGBM em L1_SPECTRUM ─────────────────────────────────────
        if enable_lightgbm:
            lgbm_sources = lgbm_source_filter or (source_filter if source_filter else LGBM_TRAIN_SOURCES)
            lgbm_records = await self._load_shadow_data(db, user_id, lookback_days, lgbm_sources)
            logger.info(
                "[MLChallenger] Lane1/LightGBM: sources=%s records=%d", lgbm_sources, len(lgbm_records),
            )
            if len(lgbm_records) < MIN_RECORDS:
                results["lightgbm"] = {
                    "status": "skipped",
                    "reason": "insufficient_data",
                    "records": len(lgbm_records),
                    "min_required": MIN_RECORDS,
                    "sources": lgbm_sources,
                }
            elif _is_installed("lightgbm"):
                try:
                    X, y, available_cols = self._build_dataset(lgbm_records, feature_columns, win_fast_threshold_s)
                    if len(y) < MIN_RECORDS:
                        results["lightgbm"] = {"status": "skipped", "reason": "insufficient_labeled"}
                    else:
                        X_tr, y_tr, X_va, y_va, X_te, y_te = self._chronological_split_with_test(X, y, VAL_FRACTION)
                        if len(y_va) < 10:
                            results["lightgbm"] = {"status": "skipped", "reason": "val_too_small"}
                        else:
                            logger.info(
                                "[MLChallenger] Treinando LightGBM (n_train=%d n_val=%d n_test=%d n_trials=%d)",
                                len(y_tr), len(y_va), len(y_te) if y_te is not None else 0, n_trials_lgbm,
                            )
                            lgbm_result = await asyncio.to_thread(
                                _train_lgbm_sync,
                                X_tr, y_tr, X_va, y_va, n_trials_lgbm, X_te, y_te,
                            )
                            model_id = await self._save_to_db(
                                db, user_id=user_id,
                                model_type="lightgbm",
                                model_obj=lgbm_result["model"],
                                feature_columns=available_cols,
                                metrics={**lgbm_result["metrics"], "train_sources": lgbm_sources},
                                threshold=lgbm_result["threshold"],
                                profile_id=profile_id,
                                model_lane="L1_SPECTRUM",
                                test_metrics=lgbm_result.get("test_metrics"),
                                win_fast_threshold_s=win_fast_threshold_s,
                            )
                            await db.commit()
                            results["lightgbm"] = {
                                "status": "trained",
                                "model_id": str(model_id),
                                "lane": "L1_SPECTRUM",
                                "sources": lgbm_sources,
                                "metrics": lgbm_result["metrics"],
                                "test_metrics": lgbm_result.get("test_metrics"),
                                "threshold": lgbm_result["threshold"],
                            }
                            logger.info(
                                "[MLChallenger] LightGBM OK: roc_auc=%.4f prec=%.4f rec=%.4f model_id=%s",
                                lgbm_result["metrics"]["roc_auc"],
                                lgbm_result["metrics"].get("precision", 0),
                                lgbm_result["metrics"].get("recall", 0),
                                model_id,
                            )
                except Exception as exc:
                    logger.exception("[MLChallenger] LightGBM falhou: %s", exc)
                    results["lightgbm"] = {"status": "failed", "error": str(exc)}
            else:
                results["lightgbm"] = {"status": "not_installed"}

        # ── Lane 2: CatBoost — L3_ONLY ou L3_LAB_ONLY (combinado BLOQUEADO) ────
        if enable_catboost:
            cb_sources = catboost_source_filter or (source_filter if source_filter else CATBOOST_L3_ONLY_SOURCES)
            # Mixed source gate: block L3+L3_LAB combined unless explicitly overridden.
            if not allow_mixed_source:
                blocked_reason = self._check_mixed_source_gate(cb_sources)
                if blocked_reason:
                    results["catboost"] = {
                        "status": "blocked",
                        "reason": blocked_reason,
                        "sources": cb_sources,
                        "message": (
                            "CatBoost L3+L3_LAB combined training is disabled. "
                            "v42 audit: source composition shift (train=79.7% L3_LAB → "
                            "test=91.4% L3) caused AUC inversion (0.707 val → 0.422 test). "
                            "Pass catboost_source_filter=['L3'] or ['L3_LAB'] instead, "
                            "or allow_mixed_source=True to override (not recommended)."
                        ),
                    }
                    logger.warning(
                        "[MLChallenger] CatBoost BLOCKED: %s (sources=%s user=%s)",
                        blocked_reason, cb_sources, user_id,
                    )
                    return results  # skip further processing
            # L3_PROFILE_STRICT policy: load all records first for metadata, then filter.
            # L3 has 66%+ NULL profile_id — training without filter produces a "global/unknown"
            # model, defeating the purpose of the L3_PROFILE lane.
            cb_all_records = await self._load_shadow_data(db, user_id, lookback_days, cb_sources)
            cb_records = [r for r in cb_all_records if r.get("profile_id")]
            l3_meta = self._l3_strict_meta(cb_all_records, cb_records, cb_sources)
            logger.info(
                "[MLChallenger] Lane2/CatBoost: sources=%s all=%d strict=%d excluded_null=%d "
                "distinct_profiles=%d unknown_pct=%.1f%%",
                cb_sources, len(cb_all_records), len(cb_records),
                l3_meta["excluded_null_profile_id"],
                l3_meta["distinct_profiles"],
                l3_meta["unknown_profile_pct"],
            )
            if len(cb_records) < MIN_RECORDS:
                results["catboost"] = {
                    "status": "skipped",
                    "reason": "insufficient_data",
                    "records": len(cb_records),
                    "min_required": MIN_RECORDS,
                    "sources": cb_sources,
                    "l3_strict_meta": l3_meta,
                }
            elif _is_installed("catboost"):
                try:
                    X, y, all_cols, cat_indices = self._build_l3_dataset(cb_records, feature_columns, win_fast_threshold_s)
                    if len(y) < MIN_RECORDS:
                        results["catboost"] = {"status": "skipped", "reason": "insufficient_labeled"}
                    else:
                        X_tr, y_tr, X_va, y_va, X_te, y_te = self._chronological_split_with_test(X, y, VAL_FRACTION)
                        if len(y_va) < 10:
                            results["catboost"] = {"status": "skipped", "reason": "val_too_small"}
                        else:
                            logger.info(
                                "[MLChallenger] Treinando CatBoost (n_train=%d n_val=%d n_test=%d n_trials=%d features=%d cat=%s)",
                                len(y_tr), len(y_va), len(y_te) if y_te is not None else 0,
                                n_trials_cb, len(all_cols), cat_indices,
                            )
                            cb_result = await asyncio.to_thread(
                                _train_catboost_sync,
                                X_tr, y_tr, X_va, y_va, all_cols, n_trials_cb, cat_indices, X_te, y_te,
                            )
                            # Derive lane from sources: single-source policies get distinct lanes.
                            if cb_sources == ["L3"]:
                                cb_lane = "L3_PROFILE"
                            elif cb_sources == ["L3_LAB"]:
                                cb_lane = "L3_LAB_PROFILE"
                            else:
                                cb_lane = "L3_PROFILE"  # combined (only if allow_mixed_source)
                            model_id = await self._save_to_db(
                                db, user_id=user_id,
                                model_type="catboost",
                                model_obj=cb_result["model"],
                                feature_columns=all_cols,
                                metrics={
                                    **cb_result["metrics"],
                                    "train_sources": cb_sources,
                                    "dataset_policy": (
                                        "L3_ONLY" if cb_sources == ["L3"]
                                        else "L3_LAB_ONLY" if cb_sources == ["L3_LAB"]
                                        else "L3_COMBINED"
                                    ),
                                    "cat_features": ["source_encoded"],
                                    **l3_meta,
                                },
                                threshold=cb_result["threshold"],
                                profile_id=profile_id,
                                model_lane=cb_lane,
                                cat_feature_indices=cat_indices,
                                test_metrics=cb_result.get("test_metrics"),
                                win_fast_threshold_s=win_fast_threshold_s,
                            )
                            await db.commit()
                            results["catboost"] = {
                                "status": "trained",
                                "model_id": str(model_id),
                                "lane": cb_lane,
                                "sources": cb_sources,
                                "metrics": cb_result["metrics"],
                                "test_metrics": cb_result.get("test_metrics"),
                                "threshold": cb_result["threshold"],
                                "cat_features": ["source_encoded"],
                                "l3_strict_meta": l3_meta,
                            }
                            logger.info(
                                "[MLChallenger] CatBoost OK: roc_auc=%.4f prec=%.4f rec=%.4f "
                                "distinct_profiles=%d excluded_null=%d model_id=%s",
                                cb_result["metrics"]["roc_auc"],
                                cb_result["metrics"].get("precision", 0),
                                cb_result["metrics"].get("recall", 0),
                                l3_meta["distinct_profiles"],
                                l3_meta["excluded_null_profile_id"],
                                model_id,
                            )
                except Exception as exc:
                    logger.exception("[MLChallenger] CatBoost falhou: %s", exc)
                    results["catboost"] = {"status": "failed", "error": str(exc)}
            else:
                results["catboost"] = {"status": "not_installed"}

        return results
