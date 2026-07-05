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

try:
    from app.ml.dataset_config import parse_required_ml_dataset_valid_from
except ModuleNotFoundError:
    from backend.app.ml.dataset_config import parse_required_ml_dataset_valid_from

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
CATBOOST_L3_LAB_ONLY_SOURCES: List[str] = ["L3_LAB"]
CATBOOST_L3_REJECTED_ONLY_SOURCES: List[str] = ["L3_REJECTED"]
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


def _json_default(obj):
    """JSON serializer for types not handled by the standard encoder (e.g. datetime)."""
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return str(obj)


def _apply_feature_contract(
    df,
    lane_contract: Optional[Dict[str, Any]],
    feature_ranges: Optional[Dict[str, Any]],
    lane_name: str = "",
):
    """E7: Apply per-lane feature contract to a training DataFrame.

    Rejects rows where any required feature is NaN and rows that violate
    configured range assertions (gt/gte/lt/lte). Returns (filtered_df, n_rejected).
    The returned df preserves original integer index so callers can re-align
    parallel lists (valid_records, created_at, etc.) by index before reset_index.
    """
    import numpy as np

    if not lane_contract and not feature_ranges:
        return df, 0

    mask = np.ones(len(df), dtype=bool)

    if lane_contract:
        for feat in lane_contract.get("required", []):
            if feat in df.columns:
                mask &= df[feat].notna().values

    if feature_ranges:
        for feat, rules in feature_ranges.items():
            if feat not in df.columns:
                continue
            col = df[feat]
            if "gt" in rules:
                mask &= (col > rules["gt"]).fillna(False).values
            if "gte" in rules:
                mask &= (col >= rules["gte"]).fillna(False).values
            if "lt" in rules:
                mask &= (col < rules["lt"]).fillna(False).values
            if "lte" in rules:
                mask &= (col <= rules["lte"]).fillna(False).values

    n_rejected = int((~mask).sum())
    if n_rejected > 0:
        import logging
        logging.getLogger(__name__).warning(
            "[MLChallenger] Feature contract rejected %d/%d rows (lane=%s)",
            n_rejected, len(df), lane_name,
        )
    return df[mask], n_rejected


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


def _calibrate_ev_threshold(
    proba,
    returns,
    grid_step: float,
    min_positives: int,
) -> tuple[float, list[dict[str, Any]]]:
    """Pick threshold on validation only by positive net EV.

    Ties intentionally choose the higher threshold.
    """
    import numpy as np

    if returns is None or len(returns) != len(proba):
        raise ValueError("threshold_calibration_requires_validation_returns")
    step = float(grid_step)
    if step <= 0.0 or step > 1.0:
        raise ValueError("invalid_threshold_grid_step")
    min_pos = int(min_positives)
    if min_pos <= 0:
        raise ValueError("invalid_threshold_min_positives")

    curve: list[dict[str, Any]] = []
    best_threshold: Optional[float] = None
    best_ev = -float("inf")
    thresholds = np.arange(0.0, 1.0 + step / 2.0, step)
    returns_arr = np.asarray(returns, dtype=float)
    for threshold in thresholds:
        mask = np.asarray(proba) >= threshold
        positives = int(mask.sum())
        if positives < min_pos:
            continue
        ev = float(np.nanmean(returns_arr[mask]))
        point = {"threshold": round(float(threshold), 6), "positives": positives, "net_ev": ev}
        curve.append(point)
        if ev > best_ev or (ev == best_ev and (best_threshold is None or threshold > best_threshold)):
            best_ev = ev
            best_threshold = float(threshold)
    if best_threshold is None:
        raise ValueError("threshold_calibration_no_eligible_threshold")
    return best_threshold, curve


# ---------------------------------------------------------------------------
# Sync training functions (run in thread pool — CPU-bound)
# ---------------------------------------------------------------------------

def _suggest_params_from_space(trial, search_space: Dict[str, Any]) -> Dict[str, Any]:
    """R1: espaço de busca Optuna vem 100% da config (`ml_optuna_search_space`).

    Cada entrada declara ``{"type": "int"|"float", "low": x, "high": y, "log": bool}``.
    Zero range hardcoded em código — mudar o espaço é mudar config, não deploy.
    """
    params: Dict[str, Any] = {}
    for name, spec in search_space.items():
        ptype = str((spec or {}).get("type") or "")
        if ptype == "int":
            params[name] = trial.suggest_int(name, int(spec["low"]), int(spec["high"]))
        elif ptype == "float":
            params[name] = trial.suggest_float(
                name, float(spec["low"]), float(spec["high"]),
                log=bool(spec.get("log", False)),
            )
        else:
            raise ValueError(
                f"ml_optuna_search_space: type inválido '{ptype}' em '{name}'"
            )
    return params


def _train_lgbm_sync(
    X_train, y_train, X_val, y_val,
    n_trials: int = 30,
    X_test=None, y_test=None,
    val_returns=None, test_returns=None,
    threshold_grid_step: float = 0.01,
    threshold_min_positives: int = 10,
    search_space: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    import lightgbm as lgb
    import numpy as np
    import optuna
    from sklearn.metrics import roc_auc_score, average_precision_score, f1_score, precision_score, recall_score

    # R1 fail-closed: espaço de busca obrigatório via config — sem fallback
    # hardcoded (mesmo padrão da fronteira ml_dataset_valid_from).
    if not search_space:
        raise ValueError("missing_ml_optuna_search_space_lightgbm")

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    logger.info(
        "[MLChallenger] Optuna(R1): n_trials=%d search_space=%s",
        n_trials, json.dumps(search_space, sort_keys=True),
    )

    dtrain = lgb.Dataset(X_train, label=y_train)
    dval = lgb.Dataset(X_val, label=y_val, reference=dtrain)

    best_params: Dict[str, Any] = {}
    _fixed_params = {
        "objective": "binary",
        "metric": "auc",
        "verbosity": -1,
        "boosting_type": "gbdt",
        "feature_pre_filter": False,
    }
    _trial_selection_objective = "net_ev" if val_returns is not None else "roc_auc"

    def objective(trial: optuna.Trial) -> float:
        params = {**_fixed_params, **_suggest_params_from_space(trial, search_space)}
        callbacks = [lgb.early_stopping(20, verbose=False), lgb.log_evaluation(-1)]
        model = lgb.train(params, dtrain, valid_sets=[dval], callbacks=callbacks)
        preds = model.predict(X_val)
        # R1.3: seleção do trial por EV LÍQUIDO de validação — mesmo critério do
        # threshold final (_calibrate_ev_threshold). Selecionar por val AUC e
        # decidir por EV cria otimismo de seleção; aqui o trial otimiza o que
        # o gate cobra. Test set jamais é lido aqui.
        if val_returns is not None:
            try:
                _, _trial_curve = _calibrate_ev_threshold(
                    preds, val_returns, threshold_grid_step, threshold_min_positives
                )
                return max(point["net_ev"] for point in _trial_curve)
            except ValueError:
                # Nenhum threshold elegível (min_positives) — trial ruim,
                # não aborta o study.
                return -float("inf")
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
    threshold, threshold_curve = _calibrate_ev_threshold(
        val_preds, val_returns, threshold_grid_step, threshold_min_positives
    )
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
            "net_ev": float(np.nanmean(np.asarray(test_returns)[t_bin == 1])) if test_returns is not None and int(t_bin.sum()) > 0 else None,
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
            "trial_selection_objective": _trial_selection_objective,
            "optuna_search_space": search_space,
            "best_trial_number": study.best_trial.number,
            "best_trial_value": study.best_trial.value,
            "val_samples": int(len(y_val)),
            "train_samples": int(len(y_train)),
            "positive_rate": float(y_val.mean()) if hasattr(y_val, "mean") else 0.0,
            "threshold_curve": threshold_curve,
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
    val_returns=None, test_returns=None,
    threshold_grid_step: float = 0.01,
    threshold_min_positives: int = 10,
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
            "nan_mode": "Min",
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
        "nan_mode": "Min",
        "random_seed": 42,
        "allow_writing_files": False,
    }
    final_model = CatBoostClassifier(**final_params)
    final_model.fit(train_pool, eval_set=val_pool, early_stopping_rounds=40, verbose=False)

    val_preds = final_model.predict_proba(val_pool)[:, 1]
    roc_auc = float(roc_auc_score(y_val, val_preds))
    pr_auc = float(average_precision_score(y_val, val_preds))
    threshold, threshold_curve = _calibrate_ev_threshold(
        val_preds, val_returns, threshold_grid_step, threshold_min_positives
    )
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
            "net_ev": float(np.nanmean(np.asarray(test_returns)[t_bin == 1])) if test_returns is not None and int(t_bin.sum()) > 0 else None,
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
            "threshold_curve": threshold_curve,
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

    async def _load_ml_config(self, db: AsyncSession) -> Dict[str, Any]:
        row = (await db.execute(text("""
            SELECT config_json
            FROM config_profiles
            WHERE config_type = 'ml' AND is_active = true
            LIMIT 1
        """))).fetchone()
        if not row or not row[0]:
            return {}
        return row[0] if isinstance(row[0], dict) else json.loads(row[0])

    async def _load_shadow_data(
        self,
        db: AsyncSession,
        user_id: UUID,
        lookback_days: int,
        source_filter: Optional[List[str]] = None,
        require_profile_id: bool = False,
        dataset_valid_from: Optional[Any] = None,
    ) -> List[Dict[str, Any]]:
        sources = source_filter if source_filter is not None else TRAIN_SOURCES
        # Build per-source placeholders to avoid any injection risk
        source_placeholders = ", ".join(f":src_{i}" for i in range(len(sources)))
        source_params = {f"src_{i}": s for i, s in enumerate(sources)}
        profile_clause = "AND profile_id IS NOT NULL" if require_profile_id else ""
        valid_from_clause = "AND created_at >= :valid_from" if dataset_valid_from else ""
        valid_from_params = {"valid_from": dataset_valid_from} if dataset_valid_from else {}
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
                profile_id::text  AS profile_id
            FROM shadow_trades
            WHERE user_id = :uid
              AND source IN ({source_placeholders})
              AND outcome IN ('TP_HIT', 'SL_HIT', 'TIMEOUT')
              AND pnl_pct IS NOT NULL
              AND features_snapshot IS NOT NULL
              AND features_snapshot::text <> '{{}}'
              AND created_at >= :cutoff
              {valid_from_clause}
              {profile_clause}
              {cutoff_clause}
            ORDER BY created_at ASC
        """), {"uid": str(user_id), "cutoff": datetime.now(timezone.utc) - timedelta(days=lookback_days),
               **source_params, **valid_from_params, **cutoff_params})).fetchall()
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
        if sources == ["L3_REJECTED"]:
            policy = "L3_REJECTED_PROFILE_STRICT"
        elif sources == ["L3_LAB"]:
            policy = "L3_LAB_PROFILE_STRICT"
        else:
            policy = "L3_PROFILE_STRICT"
        return {
            "dataset_policy": policy,
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
        lane_contract: Optional[Dict[str, Any]] = None,
        feature_ranges: Optional[Dict[str, Any]] = None,
        backfilled_feature_names: Optional[List[str]] = None,
        backfill_marker_key: Optional[str] = None,
        lane_name: str = "L3_PROFILE",
    ):
        """Constrói feature matrix e labels usando o feature_extractor canônico."""
        import numpy as np
        from app.ml.feature_extractor import build_training_dataframe

        df = build_training_dataframe(
            records,
            fee_roundtrip_pct=0.0,
            label_net_of_fees=False,
            win_fast_threshold_s=win_fast_threshold_s,
            backfilled_feature_names=backfilled_feature_names,
            backfill_marker_key=backfill_marker_key,
        )
        self._last_rows_with_backfill_neutralized = int(
            df.attrs.get("rows_with_backfill_neutralized", 0)
        )

        # E7: Row-level contract validation (required features + range checks)
        df, rows_rejected_by_contract = _apply_feature_contract(
            df, lane_contract, feature_ranges, lane_name="L1_SPECTRUM"
        )

        available = [c for c in feature_columns if c in df.columns]
        X = df[available].values.astype(float)
        nan_counts = df[available].isna().sum()
        logger.info(
            "[MLChallenger] Native NaN matrix: rows=%d max_nan_col=%s max_nan_count=%d rows_rejected_by_contract=%d",
            len(df),
            str(nan_counts.idxmax()) if len(nan_counts) else "n/a",
            int(nan_counts.max()) if len(nan_counts) else 0,
            rows_rejected_by_contract,
        )

        if "label" in df.columns:
            y = df["label"].values.astype(int)
        elif "is_win_fast" in df.columns:
            y = df["is_win_fast"].values.astype(int)
        else:
            y = np.zeros(len(df), dtype=int)

        # Re-align auxiliary lists using df's index (preserved after contract filter)
        pnl_records = [r for r in records if r.get("pnl_pct") is not None]
        valid_idx = list(df.index)
        returns = [
            float(pnl_records[i].get("net_return_pct") if pnl_records[i].get("net_return_pct") is not None else pnl_records[i].get("pnl_pct"))
            for i in valid_idx
        ]
        created_at = [pnl_records[i].get("created_at") for i in valid_idx]
        ids = [pnl_records[i].get("shadow_id") for i in valid_idx]
        holding_seconds = (
            list(df["_holding_seconds"]) if "_holding_seconds" in df.columns
            else [0.0] * len(df)
        )
        return X, y, available, returns, created_at, ids, holding_seconds

    def _build_l3_dataset(
        self,
        records: List[Dict[str, Any]],
        feature_columns: List[str],
        win_fast_threshold_s: float = 1800.0,
        lane_contract: Optional[Dict[str, Any]] = None,
        feature_ranges: Optional[Dict[str, Any]] = None,
        backfilled_feature_names: Optional[List[str]] = None,
        backfill_marker_key: Optional[str] = None,
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
            backfilled_feature_names=backfilled_feature_names,
            backfill_marker_key=backfill_marker_key,
        )
        self._last_rows_with_backfill_neutralized = int(
            df.attrs.get("rows_with_backfill_neutralized", 0)
        )

        # E7: Row-level contract validation (required features + range checks)
        df, rows_rejected_by_contract = _apply_feature_contract(
            df, lane_contract, feature_ranges, lane_name=lane_name
        )
        # Re-align valid_records to match filtered df rows
        valid_records = [valid_records[i] for i in df.index]
        df = df.reset_index(drop=True)

        available = [c for c in feature_columns if c in df.columns]
        X_base = df[available].values.astype(float)
        nan_counts = df[available].isna().sum()
        logger.info(
            "[MLChallenger] Native NaN matrix L3: rows=%d max_nan_col=%s max_nan_count=%d rows_rejected_by_contract=%d",
            len(df),
            str(nan_counts.idxmax()) if len(nan_counts) else "n/a",
            int(nan_counts.max()) if len(nan_counts) else 0,
            rows_rejected_by_contract,
        )

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

        returns = [
            float(r.get("net_return_pct") if r.get("net_return_pct") is not None else r.get("pnl_pct"))
            for r in valid_records
        ]
        created_at = [r.get("created_at") for r in valid_records]
        ids = [r.get("shadow_id") for r in valid_records]
        holding_seconds = (
            list(df["_holding_seconds"]) if "_holding_seconds" in df.columns
            else [0.0] * len(df)
        )
        return X, y, all_feature_names, cat_feature_indices, returns, created_at, ids, holding_seconds

    @staticmethod
    def _chronological_split_with_embargo(
        X,
        y,
        metadata: list,
        created_at: Optional[list] = None,
        holding_seconds: Optional[list] = None,
        val_fraction: float = 0.20,
        test_fraction: float = 0.20,
        embargo_seconds: int = 0,
    ) -> dict:
        """Temporal 60/20/20 split with purge (from train) and embargo (from val/test).

        Purge: training rows where (created_at + holding_seconds) >= val_start_time
            are dropped — the trade was still open when validation begins so its
            label would straddle the temporal boundary (leakage by resolution).
        Embargo: val/test rows within embargo_seconds of the last training timestamp
            are dropped — smooths label autocorrelation at the cut-point.

        Args:
            metadata: list of lists/arrays to split with the same boolean mask as X/y.
                Each element is split in parallel (meta_tr, meta_va, meta_te).
            created_at: timestamp per row (parallel to X/y, same ordering as DB query).
            holding_seconds: holding duration per row for purge calculation.
            embargo_seconds: gap window (read from config ml_split_embargo_seconds).

        Returns dict with keys: X_tr, y_tr, X_va, y_va, X_te, y_te,
            meta_tr, meta_va, meta_te (each a list parallel to metadata input),
            n_purged, n_embargoed, has_test.
        """
        import numpy as np
        import pandas as pd

        MIN_SET_SIZE = 5
        n = len(y)
        train_end = max(1, int(n * (1.0 - val_fraction - test_fraction)))
        val_end = max(train_end + MIN_SET_SIZE, int(n * (1.0 - test_fraction)))
        val_end = min(val_end, n - MIN_SET_SIZE)

        has_test = val_end > train_end and n - val_end >= MIN_SET_SIZE
        if not has_test:
            train_end = max(1, int(n * (1.0 - val_fraction)))
            val_end = n

        tr_mask = np.zeros(n, dtype=bool)
        va_mask = np.zeros(n, dtype=bool)
        te_mask = np.zeros(n, dtype=bool)
        tr_mask[:train_end] = True
        va_mask[train_end:val_end] = True
        if has_test:
            te_mask[val_end:] = True

        n_purged = 0
        n_embargoed = 0

        if embargo_seconds > 0 and created_at and va_mask.sum() > 0:
            try:
                hs_list = [float(h) if h is not None else 0.0 for h in (holding_seconds or [0] * n)]

                def _ts(t):
                    if t is None:
                        return None
                    try:
                        return pd.Timestamp(t)
                    except Exception:
                        return None

                at_list = [_ts(t) for t in created_at]
                val_times = [at_list[i] for i in range(n) if va_mask[i] and at_list[i] is not None]

                if val_times:
                    val_start = min(val_times)

                    # Purge: train rows whose label resolves after val_start
                    for i in range(n):
                        if tr_mask[i] and at_list[i] is not None:
                            resolve_time = at_list[i] + pd.Timedelta(seconds=hs_list[i])
                            if resolve_time >= val_start:
                                tr_mask[i] = False
                                n_purged += 1

                    # Embargo: val/test rows too close to the last surviving train row
                    tr_live_times = [at_list[i] for i in range(n) if tr_mask[i] and at_list[i] is not None]
                    if tr_live_times:
                        train_max = max(tr_live_times)
                        embargo_end = train_max + pd.Timedelta(seconds=embargo_seconds)

                        for i in range(n):
                            if (va_mask[i] or te_mask[i]) and at_list[i] is not None:
                                if at_list[i] <= embargo_end:
                                    if va_mask[i]:
                                        va_mask[i] = False
                                    else:
                                        te_mask[i] = False
                                    n_embargoed += 1
            except Exception as _emb_exc:
                logger.warning(
                    "[MLChallenger] embargo calc failed — using raw index split: %s", _emb_exc
                )
                n_purged = 0
                n_embargoed = 0

        def _apply(arr, mask):
            if isinstance(arr, np.ndarray):
                return arr[mask]
            return [v for v, m in zip(arr, mask) if m]

        return {
            "X_tr": X[tr_mask], "y_tr": y[tr_mask],
            "X_va": X[va_mask], "y_va": y[va_mask],
            "X_te": X[te_mask] if has_test else None,
            "y_te": y[te_mask] if has_test else None,
            "meta_tr": [_apply(m, tr_mask) for m in metadata],
            "meta_va": [_apply(m, va_mask) for m in metadata],
            "meta_te": [_apply(m, te_mask) for m in metadata] if has_test else [None] * len(metadata),
            "n_purged": n_purged,
            "n_embargoed": n_embargoed,
            "has_test": has_test,
        }

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

    def _split_metadata_with_test(
        self, values: list, val_fraction: float = 0.20, test_fraction: float = 0.20
    ):
        n = len(values)
        train_end = max(1, int(n * (1.0 - val_fraction - test_fraction)))
        val_end = max(train_end + 5, int(n * (1.0 - test_fraction)))
        val_end = min(val_end, n - 5)
        if val_end <= train_end or n - val_end < 5:
            split = max(1, int(n * (1.0 - val_fraction)))
            return values[:split], values[split:], None
        return values[:train_end], values[train_end:val_end], values[val_end:]

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
        train_from = metrics.get("train_from")
        train_to = metrics.get("train_to")
        dataset_query_cutoff = metrics.get("dataset_query_cutoff")
        dataset_hash = metrics.get("dataset_hash")
        if not all([train_from, train_to, dataset_query_cutoff, dataset_hash]):
            raise ValueError("missing_required_model_provenance")
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
        _ml_config = await self._load_ml_config(db)
        label_ver = str(_ml_config.get("ml_label_version", label_ver))

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
                "net_ev": (test_metrics or {}).get("net_ev"),
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
            "train_from": train_from,
            "train_to": train_to,
            "dataset_query_cutoff": dataset_query_cutoff,
            "dataset_hash": dataset_hash,
        }
        _gate_result = evaluate_promotion_gate(_gate_input, promotion_config=_ml_config)
        _metrics_json_dict = merge_promotion_gate_into_metrics_json(_metrics_json_dict, _gate_result)
        _metrics_json = json.dumps(_metrics_json_dict, default=_json_default)
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
                , train_from, train_to, dataset_query_cutoff, dataset_hash
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
                , :train_from, :train_to, :dataset_query_cutoff, :dataset_hash
            )
        """), {
            "id": str(model_uuid),
            "version": version,
            "hyperparams": json.dumps(hyperparams_full, default=_json_default),
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
            "train_from": train_from,
            "train_to": train_to,
            "dataset_query_cutoff": dataset_query_cutoff,
            "dataset_hash": dataset_hash,
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
            "metrics": json.dumps(metrics, default=_json_default),
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

    @staticmethod
    def _catboost_lane_for_sources(cb_sources: List[str]) -> str:
        """Return the persisted model_lane for a CatBoost source policy."""
        if cb_sources == ["L3"]:
            return "L3_PROFILE"
        if cb_sources == ["L3_LAB"]:
            return "L3_LAB_PROFILE"
        if cb_sources == ["L3_REJECTED"]:
            return "L3_REJECTED_PROFILE"
        return "L3_PROFILE"

    @staticmethod
    def _catboost_dataset_policy_for_sources(cb_sources: List[str]) -> str:
        """Return the governance dataset_policy label for a CatBoost source policy."""
        if cb_sources == ["L3"]:
            return "L3_ONLY"
        if cb_sources == ["L3_LAB"]:
            return "L3_LAB_ONLY"
        if cb_sources == ["L3_REJECTED"]:
            return "L3_REJECTED_ONLY"
        return "L3_COMBINED"

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

        ml_config = await self._load_ml_config(db)
        if "ml_win_fast_threshold_seconds" in ml_config:
            win_fast_threshold_s = float(ml_config["ml_win_fast_threshold_seconds"])
        dataset_valid_from = parse_required_ml_dataset_valid_from(ml_config)
        threshold_grid_step = float(ml_config.get("ml_threshold_grid_step", 0.01))
        threshold_min_positives = int(ml_config.get("ml_threshold_min_positives", 10))
        # Embargo window = label horizon + 1h margin. Config key: ml_split_embargo_seconds.
        embargo_seconds = int(ml_config.get("ml_split_embargo_seconds", int(win_fast_threshold_s) + 3600))
        # E7: Per-lane feature contract + range assertions (from config, never hardcoded)
        _feature_contract_all = ml_config.get("ml_feature_contract", {})
        feature_ranges = ml_config.get("ml_feature_ranges")
        lgbm_lane_contract = _feature_contract_all.get("L1_SPECTRUM")
        cb_lane_contract = _feature_contract_all.get("L3_PROFILE")
        backfilled_feature_names = [
            str(item) for item in (ml_config.get("ml_backfilled_feature_names") or []) if item
        ]
        backfill_marker_key = str(ml_config.get("ml_backfill_marker_key") or "")
        # F3 (encerramento fase ML 2026-07): exclusão reversível de features com
        # inversão de AUC junho→julho (evidência H4, lane L1 apenas). Nomes vivem
        # em config — decisão desligável via ml_feature_exclusion_apply=false.
        _feat_excl_proposed = [
            str(item)
            for item in (ml_config.get("ml_feature_exclusion_candidates_proposed") or [])
            if item
        ]
        lgbm_feature_columns = feature_columns
        if bool(ml_config.get("ml_feature_exclusion_apply")) and _feat_excl_proposed:
            lgbm_feature_columns = [
                c for c in feature_columns if c not in set(_feat_excl_proposed)
            ]
            logger.info(
                "[MLChallenger] ml_feature_exclusion_apply=true: excluídas %s (%d→%d features)",
                _feat_excl_proposed, len(feature_columns), len(lgbm_feature_columns),
            )

        results: Dict[str, Any] = {}

        # ── Lane 1: LightGBM em L1_SPECTRUM ─────────────────────────────────────
        if enable_lightgbm:
            lgbm_sources = lgbm_source_filter or (source_filter if source_filter else LGBM_TRAIN_SOURCES)
            lgbm_records = await self._load_shadow_data(
                db, user_id, lookback_days, lgbm_sources, dataset_valid_from=dataset_valid_from
            )
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
                    # R1 fail-closed: n_trials e espaço de busca do Optuna vêm da
                    # config — ausência aborta a lane com mensagem (mesmo padrão
                    # da fronteira ml_dataset_valid_from). Zero fallback.
                    _raw_optuna_trials = ml_config.get("ml_optuna_max_trials")
                    if _raw_optuna_trials is None:
                        raise ValueError(
                            "missing_ml_optuna_max_trials: gravar em config_profiles"
                            "(config_type='ml') antes do treino"
                        )
                    n_trials_lgbm = int(_raw_optuna_trials)
                    lgbm_search_space = (
                        ml_config.get("ml_optuna_search_space") or {}
                    ).get("lightgbm")
                    if not lgbm_search_space:
                        raise ValueError(
                            "missing_ml_optuna_search_space_lightgbm: gravar em "
                            "config_profiles(config_type='ml') antes do treino"
                        )
                    X, y, available_cols, returns, created_at, shadow_ids, holding_seconds = self._build_dataset(
                        lgbm_records, lgbm_feature_columns, win_fast_threshold_s,
                        lane_contract=lgbm_lane_contract, feature_ranges=feature_ranges,
                        backfilled_feature_names=backfilled_feature_names,
                        backfill_marker_key=backfill_marker_key,
                    )
                    if len(y) < MIN_RECORDS:
                        results["lightgbm"] = {"status": "skipped", "reason": "insufficient_labeled"}
                    else:
                        _lgbm_split = self._chronological_split_with_embargo(
                            X, y,
                            metadata=[returns, created_at, shadow_ids],
                            created_at=created_at,
                            holding_seconds=holding_seconds,
                            val_fraction=VAL_FRACTION,
                            embargo_seconds=embargo_seconds,
                        )
                        X_tr, y_tr = _lgbm_split["X_tr"], _lgbm_split["y_tr"]
                        X_va, y_va = _lgbm_split["X_va"], _lgbm_split["y_va"]
                        X_te, y_te = _lgbm_split["X_te"], _lgbm_split["y_te"]
                        ret_va = _lgbm_split["meta_va"][0]
                        ret_te = _lgbm_split["meta_te"][0] if _lgbm_split["has_test"] else None
                        logger.info(
                            "[MLChallenger] LightGBM split: train=%d val=%d test=%d "
                            "purged=%d embargoed=%d embargo_s=%d",
                            len(y_tr), len(y_va),
                            len(y_te) if y_te is not None else 0,
                            _lgbm_split["n_purged"], _lgbm_split["n_embargoed"],
                            embargo_seconds,
                        )
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
                                ret_va, ret_te, threshold_grid_step, threshold_min_positives,
                                lgbm_search_space,
                            )
                            _lgbm_at_tr = _lgbm_split["meta_tr"][1]  # created_at post-purge train rows
                            model_id = await self._save_to_db(
                                db, user_id=user_id,
                                model_type="lightgbm",
                                model_obj=lgbm_result["model"],
                                feature_columns=available_cols,
                                metrics={
                                    **lgbm_result["metrics"],
                                    "train_sources": lgbm_sources,
                                    "train_from": min(_lgbm_at_tr) if _lgbm_at_tr else None,
                                    "train_to": max(_lgbm_at_tr) if _lgbm_at_tr else None,
                                    "dataset_query_cutoff": datetime.now(timezone.utc),
                                    "dataset_hash": hashlib.sha256(
                                        "|".join(sorted(str(x) for x in shadow_ids if x)).encode()
                                    ).hexdigest(),
                                },
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
                                "rows_with_backfill_neutralized": int(
                                    getattr(self, "_last_rows_with_backfill_neutralized", 0)
                                ),
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
            cb_lane = self._catboost_lane_for_sources(cb_sources)
            cb_dataset_policy = self._catboost_dataset_policy_for_sources(cb_sources)
            cb_all_records = await self._load_shadow_data(
                db, user_id, lookback_days, cb_sources, dataset_valid_from=dataset_valid_from
            )
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
                    X, y, all_cols, cat_indices, returns, created_at, shadow_ids, holding_seconds = self._build_l3_dataset(
                        cb_records, feature_columns, win_fast_threshold_s,
                        lane_contract=cb_lane_contract, feature_ranges=feature_ranges,
                        backfilled_feature_names=backfilled_feature_names,
                        backfill_marker_key=backfill_marker_key,
                        lane_name=cb_lane,
                    )
                    if len(y) < MIN_RECORDS:
                        results["catboost"] = {"status": "skipped", "reason": "insufficient_labeled"}
                    else:
                        _cb_split = self._chronological_split_with_embargo(
                            X, y,
                            metadata=[returns, created_at, shadow_ids],
                            created_at=created_at,
                            holding_seconds=holding_seconds,
                            val_fraction=VAL_FRACTION,
                            embargo_seconds=embargo_seconds,
                        )
                        X_tr, y_tr = _cb_split["X_tr"], _cb_split["y_tr"]
                        X_va, y_va = _cb_split["X_va"], _cb_split["y_va"]
                        X_te, y_te = _cb_split["X_te"], _cb_split["y_te"]
                        ret_va = _cb_split["meta_va"][0]
                        ret_te = _cb_split["meta_te"][0] if _cb_split["has_test"] else None
                        logger.info(
                            "[MLChallenger] CatBoost split: train=%d val=%d test=%d "
                            "purged=%d embargoed=%d embargo_s=%d",
                            len(y_tr), len(y_va),
                            len(y_te) if y_te is not None else 0,
                            _cb_split["n_purged"], _cb_split["n_embargoed"],
                            embargo_seconds,
                        )
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
                                ret_va, ret_te, threshold_grid_step, threshold_min_positives,
                            )
                            model_id = await self._save_to_db(
                                db, user_id=user_id,
                                model_type="catboost",
                                model_obj=cb_result["model"],
                                feature_columns=all_cols,
                                metrics={
                                    **cb_result["metrics"],
                                    "train_sources": cb_sources,
                                    "train_from": min(_cb_split["meta_tr"][1]) if _cb_split["meta_tr"][1] else None,
                                    "train_to": max(_cb_split["meta_tr"][1]) if _cb_split["meta_tr"][1] else None,
                                    "dataset_query_cutoff": datetime.now(timezone.utc),
                                    "dataset_hash": hashlib.sha256(
                                        "|".join(sorted(str(x) for x in shadow_ids if x)).encode()
                                    ).hexdigest(),
                                    "dataset_policy": cb_dataset_policy,
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
                                "rows_with_backfill_neutralized": int(
                                    getattr(self, "_last_rows_with_backfill_neutralized", 0)
                                ),
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
