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
    from app.ml.dataset_config import (
        BARRIER_CONTRACT_ATR_DYNAMIC_V2,
        MLDatasetConfigError,
        parse_required_ml_dataset_valid_from,
    )
except ModuleNotFoundError:
    from backend.app.ml.dataset_config import (
        BARRIER_CONTRACT_ATR_DYNAMIC_V2,
        MLDatasetConfigError,
        parse_required_ml_dataset_valid_from,
    )

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
CATBOOST_CONTEXTUAL_INTELLIGENCE_SOURCES: List[str] = ["L3", "L3_REJECTED"]
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


def _snapshot_group_key(record: Dict[str, Any]) -> str:
    """Stable identity for one market observation replicated across profiles."""
    raw = json.dumps(
        record.get("features_snapshot") or {},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(raw.encode()).hexdigest()


_BARRIER_MODE_ENCODING = {"FIXED": 0.0, "ATR_DYNAMIC": 1.0}


def _economic_contract_features(
    record: Dict[str, Any],
    fee_roundtrip_pct: float,
) -> tuple[float, float, float, float, float]:
    """Point-in-time exit contract features for advisory L3 analysis."""
    tp = float(record.get("tp_pct_applied") or 0.0)
    sl = float(record.get("sl_pct_applied") or 0.0)
    reward_risk = tp / sl if sl > 0 else float("nan")
    break_even = (sl + fee_roundtrip_pct) / (tp + sl) if tp + sl > 0 else float("nan")
    barrier = _BARRIER_MODE_ENCODING.get(str(record.get("barrier_mode") or "").upper(), 2.0)
    return tp, sl, reward_risk, break_even, barrier


def _json_default(obj):
    """JSON serializer for types not handled by the standard encoder (e.g. datetime)."""
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return str(obj)


def _json_finite(value: Any) -> Any:
    """Replace non-finite numeric values before PostgreSQL JSONB serialization."""
    if isinstance(value, dict):
        return {key: _json_finite(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_finite(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _finite_metric(value: Any) -> Optional[float]:
    if value is None:
        return None
    parsed = float(value)
    return parsed if math.isfinite(parsed) else None


def _require_positive_int_config(config: Dict[str, Any], key: str) -> int:
    """Read a required positive integer from config_profiles JSONB."""
    raw_value = config.get(key)
    if raw_value is None:
        raise ValueError(f"missing_{key}: gravar em config_profiles(config_type='ml') antes do treino")
    if isinstance(raw_value, bool) or not isinstance(raw_value, int):
        raise ValueError(f"invalid_{key}: expected positive integer, got {raw_value!r}")
    value = raw_value
    if value <= 0:
        raise ValueError(f"invalid_{key}: expected positive integer, got {raw_value!r}")
    return value


def _filter_l3_barrier_contract(
    records: List[Dict[str, Any]],
    *,
    expected_mode: str,
    expected_tp_pct: float,
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Keep rows produced under the active ML economic contract.

    Canonical L3 historically inherited the spot-engine TP while the ML config
    declared a different shadow_tp_pct. Mixing those policies changes both the
    target payoff and break-even inside one chronological split.
    """
    mode = str(expected_mode).upper()
    tp = float(expected_tp_pct)
    kept: List[Dict[str, Any]] = []
    mismatched_mode = 0
    mismatched_tp = 0
    missing_contract = 0
    for record in records:
        record_mode = record.get("barrier_mode")
        record_tp = record.get("tp_pct_applied")
        if record_mode is None or record_tp is None:
            missing_contract += 1
            continue
        if str(record_mode).upper() != mode:
            mismatched_mode += 1
            continue
        # Fase 1 (D1=A): sob shadow_atr_dynamic_v2 o TP é ATR-dinâmico por
        # linha — a paridade econômica é dada pela versão do contrato de
        # barreira, não pela igualdade com o TP do Strategies Module.
        if (
            mode == "ATR_DYNAMIC"
            and record.get("barrier_contract_version") == BARRIER_CONTRACT_ATR_DYNAMIC_V2
        ):
            kept.append(record)
            continue
        if not math.isclose(float(record_tp), tp, rel_tol=0.0, abs_tol=1e-9):
            mismatched_tp += 1
            continue
        kept.append(record)
    return kept, {
        "barrier_contract_expected_mode": mode,
        "barrier_contract_expected_tp_pct": tp,
        "barrier_contract_included": len(kept),
        "barrier_contract_missing": missing_contract,
        "barrier_contract_mode_mismatch": mismatched_mode,
        "barrier_contract_tp_mismatch": mismatched_tp,
    }


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


def _stable_train_feature_indices(
    X_train,
    feature_names: List[str],
    *,
    min_coverage: float,
    excluded: Optional[List[str]] = None,
) -> List[int]:
    """Select columns using train data only, preventing schema leakage."""
    import numpy as np

    excluded_set = set(excluded or [])
    keep: List[int] = []
    for index, name in enumerate(feature_names):
        if name in excluded_set:
            continue
        values = np.asarray(X_train[:, index], dtype=float)
        finite = np.isfinite(values)
        if float(finite.mean()) < float(min_coverage):
            continue
        if finite.any() and float(np.nanstd(values)) > 0.0:
            keep.append(index)
    return keep


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


def _validation_selection_score(
    predictions,
    labels,
    returns,
    grid_step: float,
    min_positives: int,
) -> float:
    """Optuna score aligned with the downstream economic threshold gate."""
    if returns is not None:
        _, curve = _calibrate_ev_threshold(
            predictions, returns, grid_step, min_positives
        )
        return max(point["net_ev"] for point in curve)
    from sklearn.metrics import roc_auc_score
    return float(roc_auc_score(labels, predictions))


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


def _bootstrap_auc_ci_low(
    y_true, y_pred, level: float, n_boot: int, seed: int
) -> Optional[float]:
    """Limite inferior do IC bootstrap (percentil) do ROC AUC de teste.

    Fase 1.5 P3 — rede de segurança da seleção em val fixo (Caso B): reamostra
    (y_true, y_pred) com reposição n_boot vezes, recalcula o AUC, e devolve o
    percentil inferior do IC bilateral no nível `level`. Determinístico (seed).
    Retorna None se não houver as duas classes (AUC indefinido).
    """
    import numpy as np
    from sklearn.metrics import roc_auc_score

    y_true = np.asarray(y_true)
    y_pred = np.asarray(y_pred)
    n = len(y_true)
    if n < 2 or len(np.unique(y_true)) < 2:
        return None
    rng = np.random.default_rng(int(seed))
    aucs: list[float] = []
    for _ in range(int(n_boot)):
        idx = rng.integers(0, n, size=n)
        yt = y_true[idx]
        if len(np.unique(yt)) < 2:
            continue
        aucs.append(float(roc_auc_score(yt, y_pred[idx])))
    if not aucs:
        return None
    lower_pct = (1.0 - float(level)) / 2.0 * 100.0
    return float(np.percentile(aucs, lower_pct))


def _train_lgbm_sync(
    X_train, y_train, X_val, y_val,
    n_trials: int = 30,
    X_test=None, y_test=None,
    val_returns=None, test_returns=None,
    threshold_grid_step: float = 0.01,
    threshold_min_positives: int = 10,
    search_space: Optional[Dict[str, Any]] = None,
    seed: int = 42,
    optuna_timeout_s: int = 180,
    auc_ci_level: float = 0.95,
    bootstrap_iterations: int = 2000,
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
        # Fase 1.5 P3 — determinismo: seed em todas as fontes de aleatoriedade
        # do LightGBM para tornar a seleção/treino reproduzíveis.
        "seed": int(seed),
        "bagging_seed": int(seed),
        "feature_fraction_seed": int(seed),
        "deterministic": True,
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

    # Fase 1.5 P3 — sampler com seed: a seleção de hiperparâmetros passa a ser
    # reproduzível (antes create_study() sem seed → TPE aleatório por execução).
    study = optuna.create_study(
        direction="maximize",
        sampler=optuna.samplers.TPESampler(seed=int(seed)),
    )
    study.optimize(
        objective, n_trials=n_trials,
        timeout=int(optuna_timeout_s), show_progress_bar=False,
    )

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
            # Fase 1.5 P3 — limite inferior do IC bootstrap do AUC de teste
            # (gate ml_approval_test_auc_ci_excludes_half): o AUC precisa ser
            # estatisticamente > 0.5, não só no ponto.
            "roc_auc_ci_low": _bootstrap_auc_ci_low(
                y_test, t_preds, auc_ci_level, bootstrap_iterations, seed
            ),
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
    train_weights=None, val_weights=None, test_weights=None,
    selection_objective: str = "net_ev",
    fixed_params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    from catboost import CatBoostClassifier, Pool
    import numpy as np
    import optuna
    from sklearn.metrics import (
        average_precision_score,
        brier_score_loss,
        f1_score,
        precision_score,
        recall_score,
        roc_auc_score,
    )

    import pandas as pd

    optuna.logging.set_verbosity(optuna.logging.WARNING)

    def _make_pool(X, y, weights=None):
        if cat_feature_indices:
            # CatBoost rejects float numpy arrays with cat_features — use a
            # DataFrame with categorical columns converted to string so CatBoost
            # can apply its ordered target statistics encoding.
            df = pd.DataFrame(X, columns=list(feature_names))
            cat_names = [list(feature_names)[i] for i in cat_feature_indices]
            for name in cat_names:
                df[name] = df[name].astype(int).astype(str)
            return Pool(df, label=y, weight=weights, cat_features=cat_names)
        return Pool(X, label=y, weight=weights, feature_names=list(feature_names))

    train_pool = _make_pool(X_train, y_train, train_weights)
    val_pool = _make_pool(X_val, y_val, val_weights)
    _trial_selection_objective = selection_objective

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
        if selection_objective == "weighted_roc_auc":
            return float(roc_auc_score(y_val, preds, sample_weight=val_weights))
        try:
            return _validation_selection_score(
                preds,
                y_val,
                val_returns,
                threshold_grid_step,
                threshold_min_positives,
            )
        except ValueError:
            return -float("inf")

    study = None
    if fixed_params is None:
        study = optuna.create_study(direction="maximize")
        study.optimize(objective, n_trials=n_trials, timeout=180, show_progress_bar=False)
        selected_params = study.best_params
    else:
        selected_params = dict(fixed_params)

    final_params = {
        **selected_params,
        "verbose": False,
        "eval_metric": "AUC",
        "nan_mode": "Min",
        "random_seed": 42,
        "allow_writing_files": False,
    }
    final_model = CatBoostClassifier(**final_params)
    final_model.fit(train_pool, eval_set=val_pool, early_stopping_rounds=40, verbose=False)

    val_preds = final_model.predict_proba(val_pool)[:, 1]
    roc_auc = float(roc_auc_score(y_val, val_preds, sample_weight=val_weights))
    pr_auc = float(average_precision_score(y_val, val_preds, sample_weight=val_weights))
    if selection_objective == "weighted_roc_auc":
        threshold, threshold_curve = 0.5, []
    else:
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

    validation_weighted = {
        "weighted_roc_auc": roc_auc,
        "weighted_brier": float(brier_score_loss(y_val, val_preds, sample_weight=val_weights)),
        "effective_snapshots": float(np.asarray(val_weights).sum()) if val_weights is not None else float(len(y_val)),
    }
    test_metrics: Dict[str, Any] = {}
    if X_test is not None and y_test is not None and len(y_test) >= 5:
        test_pool = _make_pool(X_test, y_test, test_weights)
        t_preds = final_model.predict_proba(test_pool)[:, 1]
        t_bin = (t_preds >= threshold).astype(int)
        t_tn = int(((t_bin == 0) & (np.asarray(y_test) == 0)).sum())
        t_fp = int(((t_bin == 1) & (np.asarray(y_test) == 0)).sum())
        test_metrics = {
            "roc_auc": float(roc_auc_score(y_test, t_preds, sample_weight=test_weights)),
            "pr_auc": float(average_precision_score(y_test, t_preds, sample_weight=test_weights)),
            "f1": float(f1_score(y_test, t_bin, zero_division=0)),
            "precision": float(precision_score(y_test, t_bin, zero_division=0)),
            "recall": float(recall_score(y_test, t_bin, zero_division=0)),
            "fpr": t_fp / (t_fp + t_tn) if (t_fp + t_tn) > 0 else 0.0,
            "samples": int(len(y_test)),
            "positive_rate": float(np.asarray(y_test).mean()),
            "net_ev": float(np.nanmean(np.asarray(test_returns)[t_bin == 1])) if test_returns is not None and int(t_bin.sum()) > 0 else None,
            "weighted_roc_auc": float(roc_auc_score(y_test, t_preds, sample_weight=test_weights)),
            "weighted_brier": float(brier_score_loss(y_test, t_preds, sample_weight=test_weights)),
            "effective_snapshots": float(np.asarray(test_weights).sum()) if test_weights is not None else float(len(y_test)),
        }

    return {
        "model": final_model,
        "model_type": "catboost",
        "best_params": selected_params,
        "metrics": {
            "roc_auc": roc_auc,
            "pr_auc": pr_auc,
            "f1": f1,
            "precision": prec,
            "recall": rec,
            "fpr": fpr,
            "n_trials": n_trials if study is not None else 0,
            "trial_selection_objective": _trial_selection_objective,
            "best_trial_number": study.best_trial.number if study is not None else None,
            "best_trial_value": study.best_trial.value if study is not None else roc_auc,
            "fixed_params": selected_params if study is None else None,
            "val_samples": int(len(y_val)),
            "train_samples": int(len(y_train)),
            "positive_rate": float(y_val.mean()) if hasattr(y_val, "mean") else 0.0,
            "threshold_curve": threshold_curve,
            **validation_weighted,
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

    async def _load_strategy_tp_pct(self, db: AsyncSession, user_id: UUID) -> float:
        """Read the active Strategies Module TP for dataset-contract parity."""
        row = (await db.execute(text("""
            SELECT config_json
            FROM config_profiles
            WHERE user_id = :uid
              AND config_type = 'spot_engine'
              AND is_active = true
            ORDER BY updated_at DESC
            LIMIT 1
        """), {"uid": str(user_id)})).fetchone()
        if not row or not row[0]:
            raise ValueError("missing_spot_engine_config_for_dataset_contract")
        payload = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        selling = payload.get("selling") or {}
        value = selling.get("take_profit_pct")
        if value in (None, ""):
            raise ValueError("missing_strategy_take_profit_pct_for_dataset_contract")
        return float(value)

    async def _load_shadow_data(
        self,
        db: AsyncSession,
        user_id: UUID,
        lookback_days: int,
        source_filter: Optional[List[str]] = None,
        require_profile_id: bool = False,
        dataset_valid_from: Optional[Any] = None,
        dataset_query_cutoff: Optional[datetime] = None,
        maturity_embargo_margin_minutes: Optional[int] = None,
        collect_diagnostics: bool = False,
    ) -> List[Dict[str, Any]]:
        if dataset_query_cutoff is None:
            raise ValueError("missing_dataset_query_cutoff")
        if dataset_query_cutoff.tzinfo is None:
            raise ValueError("invalid_dataset_query_cutoff_timezone")
        if maturity_embargo_margin_minutes is None:
            raise ValueError(
                "missing_ml_maturity_embargo_margin_minutes: gravar em "
                "config_profiles(config_type='ml') antes do treino"
            )
        if int(maturity_embargo_margin_minutes) < 0:
            raise ValueError("invalid_ml_maturity_embargo_margin_minutes")
        # Fase 1 B.2 — a fronteira temporal é obrigatória em TODO caminho de
        # montagem de dataset. Zero datas hardcoded; a chave vem da config
        # (ml_dataset_valid_from) e é parseada pelo caller.
        if dataset_valid_from is None:
            raise MLDatasetConfigError(
                "missing_dataset_valid_from: todo caminho de treino deve passar "
                "a fronteira ml_dataset_valid_from lida da config"
            )
        sources = source_filter if source_filter is not None else TRAIN_SOURCES
        # Build per-source placeholders to avoid any injection risk
        source_placeholders = ", ".join(f":src_{i}" for i in range(len(sources)))
        source_params = {f"src_{i}": s for i, s in enumerate(sources)}
        profile_clause = "AND profile_id IS NOT NULL" if require_profile_id else ""
        valid_from_clause = (
            "AND entry_timestamp IS NOT NULL AND entry_timestamp >= :valid_from"
        )
        valid_from_params = {"valid_from": dataset_valid_from}
        cutoff_clause = ""
        cutoff_params: dict = {}
        if _TRAIN_CUTOFF_AT:
            from datetime import datetime as _dt
            _cutoff_dt = _dt.fromisoformat(_TRAIN_CUTOFF_AT).replace(tzinfo=timezone.utc)
            cutoff_clause = "AND completed_at < :train_cutoff_at"
            cutoff_params = {"train_cutoff_at": _cutoff_dt}
            logger.info("[MLChallenger] _load_shadow_data cutoff: completed_at < %s", _TRAIN_CUTOFF_AT)
        params = {
            "uid": str(user_id),
            "cutoff": dataset_query_cutoff - timedelta(days=lookback_days),
            "dataset_query_cutoff": dataset_query_cutoff,
            "maturity_embargo_margin_minutes": int(maturity_embargo_margin_minutes),
            **source_params,
            **valid_from_params,
            **cutoff_params,
        }
        base_where = f"""
            user_id = :uid
              AND source IN ({source_placeholders})
              AND outcome IN ('TP_HIT', 'SL_HIT', 'TIMEOUT')
              AND pnl_pct IS NOT NULL
              AND features_snapshot IS NOT NULL
              AND features_snapshot::text <> '{{}}'
              AND capture_contract_version = 'point-in-time-v1'
              AND features_captured_at IS NOT NULL
              AND feature_extractor_version IS NOT NULL
              AND feature_schema_version IS NOT NULL
              AND feature_hash IS NOT NULL
              AND lineage_status = 'EXACT'
              AND eligible_for_training IS TRUE
              AND created_at >= :cutoff
              {valid_from_clause}
              {profile_clause}
              {cutoff_clause}
        """
        self._last_shadow_load_diagnostics = {}
        if collect_diagnostics:
            diagnostic = (await db.execute(text(f"""
                SELECT
                    COUNT(*)::int AS official_candidates,
                    COUNT(*) FILTER (
                        WHERE COALESCE(label_resolved_at, completed_at) IS NULL
                           OR COALESCE(label_resolved_at, completed_at) > :dataset_query_cutoff
                    )::int AS labels_unresolved_at_cutoff,
                    COUNT(*) FILTER (
                        WHERE COALESCE(label_resolved_at, completed_at) <= :dataset_query_cutoff
                          AND created_at > :dataset_query_cutoff - make_interval(
                                mins => COALESCE(ttt_timeout_minutes, 0)
                                      + :maturity_embargo_margin_minutes
                              )
                    )::int AS observations_immature_at_cutoff,
                    COUNT(*) FILTER (
                        WHERE COALESCE(label_resolved_at, completed_at) <= :dataset_query_cutoff
                          AND created_at <= :dataset_query_cutoff - make_interval(
                                mins => COALESCE(ttt_timeout_minutes, 0)
                                      + :maturity_embargo_margin_minutes
                              )
                    )::int AS records_mature
                FROM shadow_trades
                WHERE {base_where}
            """), params)).mappings().one()
            self._last_shadow_load_diagnostics = dict(diagnostic)
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
                config_snapshot,
                barrier_mode,
                barrier_contract_version,
                tp_pct_applied,
                sl_pct_applied,
                entry_timestamp,
                created_at,
                features_captured_at,
                timeframe,
                exchange,
                profile_id::text AS profile_id,
                event_id::text AS event_id,
                snapshot_id::text AS snapshot_id,
                profile_version_id::text AS profile_version_id,
                score_engine_version_id::text AS score_engine_version_id,
                label_resolved_at,
                completed_at,
                ttt_timeout_minutes
            FROM shadow_trades
            WHERE {base_where}
              AND COALESCE(label_resolved_at, completed_at) IS NOT NULL
              AND COALESCE(label_resolved_at, completed_at) <= :dataset_query_cutoff
              AND created_at <= :dataset_query_cutoff - make_interval(
                    mins => COALESCE(ttt_timeout_minutes, 0)
                          + :maturity_embargo_margin_minutes
                  )
            ORDER BY created_at ASC
        """), params)).fetchall()
        logger.info(
            "[MLChallenger] _load_shadow_data: sources=%s rows=%d require_profile_id=%s user=%s",
            sources, len(rows), require_profile_id, user_id,
        )
        records = [dict(r._mapping) for r in rows]
        # Fase 1 B.2 — guard de montagem: linha pré-fronteira presente no
        # dataset é exceção dura; o treino aborta em vez de treinar contaminado.
        _vf = dataset_valid_from
        for record in records:
            _ets = record.get("entry_timestamp")
            if _ets is None or _ets < _vf:
                raise MLDatasetConfigError(
                    "dataset_row_before_valid_from: shadow_id="
                    f"{record.get('shadow_id')} entry_timestamp={_ets} "
                    f"valid_from={_vf.isoformat()}"
                )
        return records

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
        label_objective: str = "fast_tp",
    ):
        """Constrói feature matrix e labels usando o feature_extractor canônico."""
        import numpy as np
        from app.ml.feature_extractor import (
            assert_no_operational_feature_leakage,
            build_training_dataframe,
        )

        assert_no_operational_feature_leakage(feature_columns)
        df = build_training_dataframe(
            records,
            fee_roundtrip_pct=0.0,
            label_net_of_fees=False,
            win_fast_threshold_s=win_fast_threshold_s,
            label_objective=label_objective,
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
        lane_name: str = "L3_PROFILE",
        lane_contract: Optional[Dict[str, Any]] = None,
        feature_ranges: Optional[Dict[str, Any]] = None,
        backfilled_feature_names: Optional[List[str]] = None,
        backfill_marker_key: Optional[str] = None,
        label_objective: str = "fast_tp",
        fee_roundtrip_pct: float = 0.0,
    ):
        """Constrói dataset L3 para CatBoost com features categóricas adicionais.

        Appends source_encoded (ordinal) e profile_id_encoded (hash) como
        features numéricas extras ao final do vector — CatBoost usa internamente
        para splitting por profile. As colunas categóricas ficam APÓS as base
        features para não perturbar o índice do modelo L1.
        """
        import numpy as np
        from app.ml.feature_extractor import (
            assert_no_operational_feature_leakage,
            build_training_dataframe,
        )

        assert_no_operational_feature_leakage(feature_columns)
        # Pre-filter para alinhar com o que build_training_dataframe vai manter.
        # build_training_dataframe faz `continue` em pnl_pct is None; mantendo
        # a mesma filtragem aqui garantimos que zip(valid, df.iterrows) é válido.
        valid_records = [r for r in records if r.get("pnl_pct") is not None]

        df = build_training_dataframe(
            valid_records,
            fee_roundtrip_pct=0.0,
            label_net_of_fees=False,
            win_fast_threshold_s=win_fast_threshold_s,
            label_objective=label_objective,
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

        # Stack: base features + source/profile context.  The approved advisory
        # lane also receives the immutable exit contract so historical FIXED
        # and current ATR_DYNAMIC outcomes are not treated as the same target.
        extra_columns = [source_enc, profile_enc]
        all_feature_names = available + ["source_encoded", "profile_id_encoded"]
        if lane_name == "L3_APPROVED_INTELLIGENCE":
            contract = np.asarray([
                _economic_contract_features(r, fee_roundtrip_pct)
                for r in valid_records
            ], dtype=float)
            extra_columns.extend(contract[:, index] for index in range(contract.shape[1]))
            all_feature_names.extend([
                "tp_pct_applied",
                "sl_pct_applied",
                "reward_risk_ratio",
                "break_even_probability",
                "barrier_mode_encoded",
            ])
        X = np.column_stack([X_base, *extra_columns])

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
        snapshot_keys = [_snapshot_group_key(r) for r in valid_records]
        return (
            X, y, all_feature_names, cat_feature_indices, returns, created_at, ids,
            holding_seconds, snapshot_keys,
        )

    @staticmethod
    def _chronological_split_with_embargo(
        X,
        y,
        metadata: list,
        created_at: Optional[list] = None,
        holding_seconds: Optional[list] = None,
        group_ids: Optional[list] = None,
        val_fraction: float = 0.20,
        test_fraction: float = 0.20,
        embargo_seconds: int = 0,
        min_train_size: int = 1,
        min_validation_size: int = 1,
        min_test_size: int = 1,
        max_boundary_candidates: Optional[int] = None,
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

        if group_ids is not None:
            from datetime import timedelta

            from app.ml.grouped_purged_split import (
                TemporalObservation,
                grouped_purged_split,
            )

            if len(group_ids) != n or not created_at or len(created_at) != n:
                raise ValueError("invalid_grouped_split_metadata")

            hs_list = [
                float(value) if value is not None else 0.0
                for value in (holding_seconds or [0.0] * n)
            ]
            observed_at = [pd.Timestamp(value).to_pydatetime() for value in created_at]
            observations = [
                TemporalObservation(
                    row_id=index,
                    group_id=group_ids[index],
                    observed_at=observed_at[index],
                    label_resolved_at=(
                        observed_at[index] + timedelta(seconds=hs_list[index])
                    ),
                )
                for index in range(n)
            ]
            unique_times = sorted(set(observed_at))
            all_candidate_positions = list(range(1, len(unique_times) - 1))
            if (
                max_boundary_candidates is not None
                and max_boundary_candidates > 0
                and len(all_candidate_positions) > max_boundary_candidates
            ):
                candidate_positions = sorted(set(
                    np.linspace(
                        1,
                        len(unique_times) - 2,
                        num=max(3, int(max_boundary_candidates)),
                        dtype=int,
                    ).tolist()
                ))
            else:
                candidate_positions = all_candidate_positions

            target_fractions = (
                1.0 - val_fraction - test_fraction,
                val_fraction,
                test_fraction,
            )
            embargo_delta = timedelta(seconds=embargo_seconds)
            ranked_boundaries = []
            for offset, validation_position in enumerate(candidate_positions[:-1]):
                for test_position in candidate_positions[offset + 1:]:
                    validation_boundary = unique_times[validation_position]
                    test_boundary = unique_times[test_position]
                    raw_counts = (
                        sum(value < validation_boundary for value in observed_at),
                        sum(
                            validation_boundary <= value < test_boundary
                            for value in observed_at
                        ),
                        sum(
                            value >= test_boundary + embargo_delta
                            for value in observed_at
                        ),
                    )
                    if (
                        raw_counts[0] < min_train_size
                        or raw_counts[1] < min_validation_size
                        or raw_counts[2] < min_test_size
                    ):
                        continue
                    fraction_error = sum(
                        abs((count / n) - target)
                        for count, target in zip(raw_counts, target_fractions)
                    )
                    rank = (
                        fraction_error,
                        -min(raw_counts[1], raw_counts[2]),
                        -sum(raw_counts),
                        -raw_counts[2],
                        validation_boundary,
                        test_boundary,
                    )
                    ranked_boundaries.append(
                        (rank, validation_boundary, test_boundary, raw_counts)
                    )

            best = None
            best_available_counts = (0, 0, 0)
            single_class_candidates = 0
            size_feasible_candidates = 0
            max_minority_labels = [0, 0, 0]
            for rank, validation_boundary, test_boundary, raw_counts in sorted(
                ranked_boundaries
            ):
                candidate = grouped_purged_split(
                    observations,
                    validation_start=validation_boundary,
                    test_start=test_boundary,
                    label_horizon=embargo_delta,
                    embargo=embargo_delta,
                )
                counts = (
                    len(candidate.train),
                    len(candidate.validation),
                    len(candidate.test),
                )
                label_sets = tuple(
                    {
                        int(y[row.row_id])
                        for row in partition
                    }
                    for partition in (
                        candidate.train,
                        candidate.validation,
                        candidate.test,
                    )
                )
                has_class_diversity = all(
                    len(labels) >= 2 for labels in label_sets
                )
                sizes_are_feasible = (
                    counts[0] >= min_train_size
                    and counts[1] >= min_validation_size
                    and counts[2] >= min_test_size
                )
                if sizes_are_feasible:
                    size_feasible_candidates += 1
                    for index, (partition, labels) in enumerate(
                        zip(
                            (
                                candidate.train,
                                candidate.validation,
                                candidate.test,
                            ),
                            label_sets,
                        )
                    ):
                        if len(labels) >= 2:
                            label_counts = {
                                label: sum(
                                    int(y[row.row_id]) == label
                                    for row in partition
                                )
                                for label in labels
                            }
                            max_minority_labels[index] = max(
                                max_minority_labels[index],
                                min(label_counts.values()),
                            )
                if (
                    counts[0] >= min_train_size
                    and counts[1] >= min_validation_size
                    and counts[2] > best_available_counts[2]
                ):
                    best_available_counts = counts
                if not has_class_diversity:
                    single_class_candidates += 1
                if sizes_are_feasible and has_class_diversity:
                    best = (
                        rank,
                        candidate,
                        validation_boundary,
                        test_boundary,
                        raw_counts,
                    )
                    break

            def _apply_grouped(arr, mask):
                if isinstance(arr, np.ndarray):
                    return arr[mask]
                return [value for value, keep in zip(arr, mask) if keep]

            if best is None:
                tr_mask = np.ones(n, dtype=bool)
                va_mask = np.zeros(n, dtype=bool)
                te_mask = np.zeros(n, dtype=bool)
                split_diagnostics = {
                    "split_strategy": "grouped_purged_no_feasible_boundaries",
                    "boundary_candidates": len(candidate_positions),
                    "evaluated_boundary_pairs": len(ranked_boundaries),
                    "required_train_samples": min_train_size,
                    "required_validation_samples": min_validation_size,
                    "required_test_samples": min_test_size,
                    "max_candidate_train_samples": best_available_counts[0],
                    "max_candidate_validation_samples": best_available_counts[1],
                    "max_candidate_test_samples": best_available_counts[2],
                    "test_sample_deficit": max(
                        0, min_test_size - best_available_counts[2]
                    ),
                    "single_class_candidates": single_class_candidates,
                    "size_feasible_candidates": size_feasible_candidates,
                    "max_train_minority_labels": max_minority_labels[0],
                    "max_validation_minority_labels": max_minority_labels[1],
                    "max_test_minority_labels": max_minority_labels[2],
                    "requires_class_diversity": True,
                    "block_reason": (
                        "single_class_partition"
                        if size_feasible_candidates > 0
                        else "insufficient_partition_samples"
                    ),
                }
                has_test = False
            else:
                selected = best[1]
                train_ids = {row.row_id for row in selected.train}
                validation_ids = {row.row_id for row in selected.validation}
                test_ids = {row.row_id for row in selected.test}
                tr_mask = np.asarray([index in train_ids for index in range(n)])
                va_mask = np.asarray([index in validation_ids for index in range(n)])
                te_mask = np.asarray([index in test_ids for index in range(n)])
                split_diagnostics = {
                    **selected.diagnostics,
                    "split_strategy": "grouped_purged_temporal_search",
                    "boundary_candidates": len(candidate_positions),
                    "evaluated_boundary_pairs": len(ranked_boundaries),
                    "raw_fraction_error": best[0][0],
                    "raw_train": best[4][0],
                    "raw_validation": best[4][1],
                    "raw_test": best[4][2],
                    "validation_boundary": best[2].isoformat(),
                    "test_boundary": best[3].isoformat(),
                    "test_effective_start": (best[3] + embargo_delta).isoformat(),
                    "required_train_samples": min_train_size,
                    "required_validation_samples": min_validation_size,
                    "required_test_samples": min_test_size,
                    "test_sample_deficit": 0,
                    "requires_class_diversity": True,
                }
                has_test = bool(te_mask.any())

            def _mask_window(mask):
                values = [
                    observed_at[index]
                    for index, keep in enumerate(mask)
                    if bool(keep)
                ]
                if not values:
                    return None, None
                return min(values).isoformat(), max(values).isoformat()

            dataset_from = min(observed_at).isoformat() if observed_at else None
            dataset_to = max(observed_at).isoformat() if observed_at else None
            if has_test:
                train_from, train_to = _mask_window(tr_mask)
                validation_from, validation_to = _mask_window(va_mask)
                test_from, test_to = _mask_window(te_mask)
            else:
                train_from = train_to = None
                validation_from = validation_to = None
                test_from = test_to = None
            split_diagnostics.update({
                "dataset_rows": n,
                "dataset_from": dataset_from,
                "dataset_to": dataset_to,
                "train_from": train_from,
                "train_to": train_to,
                "validation_from": validation_from,
                "validation_to": validation_to,
                "test_from": test_from,
                "test_to": test_to,
                "effective_train_samples": int(tr_mask.sum()) if has_test else 0,
                "effective_validation_samples": int(va_mask.sum()) if has_test else 0,
                "effective_test_samples": int(te_mask.sum()) if has_test else 0,
                "excluded_from_effective_split": (
                    n - int(tr_mask.sum()) - int(va_mask.sum()) - int(te_mask.sum())
                    if has_test else n
                ),
            })

            return {
                "X_tr": X[tr_mask], "y_tr": y[tr_mask],
                "X_va": X[va_mask], "y_va": y[va_mask],
                "X_te": X[te_mask] if has_test else None,
                "y_te": y[te_mask] if has_test else None,
                "meta_tr": [_apply_grouped(item, tr_mask) for item in metadata],
                "meta_va": [_apply_grouped(item, va_mask) for item in metadata],
                "meta_te": (
                    [_apply_grouped(item, te_mask) for item in metadata]
                    if has_test else [None] * len(metadata)
                ),
                "n_purged": split_diagnostics.get("label_purged_train", 0),
                "n_purged_val_test": split_diagnostics.get(
                    "label_purged_validation", 0
                ),
                "n_group_purged": (
                    split_diagnostics.get("group_purged_train", 0)
                    + split_diagnostics.get("group_purged_validation", 0)
                ),
                "n_embargoed": split_diagnostics.get("embargoed_test", 0),
                "has_test": has_test,
                "split_diagnostics": split_diagnostics,
            }

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
            "n_purged_val_test": 0,
            "n_group_purged": 0,
            "n_embargoed": n_embargoed,
            "has_test": has_test,
            "split_diagnostics": {
                "split_strategy": "legacy_index_train_boundary_only",
                "purged_train": n_purged,
                "embargoed_validation_or_test": n_embargoed,
            },
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
        dataset_stats: Optional[Dict[str, Any]] = None,
    ) -> UUID:
        """Persistência transacional de um treino (Fase 1 B.4).

        Grava, na MESMA transação do caller: ml_models + ml_model_registry +
        ml_training_dataset + ml_promotion_gate_results. Contract_ids nulos ou
        lane/source não registrados em ml_dataset_contracts são exceção dura —
        ou tudo é gravado, ou nada é gravado (rollback no caller).
        """
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

        roc_auc = _finite_metric(metrics.get("roc_auc"))
        f1 = _finite_metric(metrics.get("f1"))
        precision = _finite_metric(metrics.get("precision"))
        recall = _finite_metric(metrics.get("recall"))
        fpr = _finite_metric(metrics.get("fpr"))
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
        hyperparams_full = _json_finite({**metrics})
        if test_metrics:
            hyperparams_full["test_metrics"] = _json_finite(test_metrics)

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
        label_objective = str(metrics.get("label_objective") or "")
        label_contract_id = (
            _hashlib.sha256(
                f"{label_ver}|{int(win_fast_threshold_s)}|{label_objective}".encode()
            ).hexdigest()[:32]
            if label_ver and label_objective
            else None
        )
        feature_contract_id = (
            _hashlib.sha256(f"{fc_schema_ver}|{fc_hash}".encode()).hexdigest()[:32]
            if fc_schema_ver and fc_hash
            else None
        )
        if source_filter_str and label_ver and model_lane and fc_hash:
            dataset_contract_id = _hashlib.sha256(
                f"{label_ver}|{model_lane}|{source_filter_str}|{fc_hash}".encode()
            ).hexdigest()[:32]
        else:
            dataset_contract_id = None

        # Fase 1 B.4 — governança inviolável: nenhum modelo é persistido sem
        # os três contract_ids (v80 nasceu com label/feature nulos e zero
        # linhas em ml_training_dataset). Exceção dura ANTES de qualquer INSERT.
        if not (label_contract_id and feature_contract_id and dataset_contract_id):
            raise ValueError(
                "ml_governance_contract_ids_required: label_contract_id="
                f"{label_contract_id} feature_contract_id={feature_contract_id} "
                f"dataset_contract_id={dataset_contract_id} — treino abortado"
            )
        # Lane/source restritos aos contratos registrados em ml_dataset_contracts.
        # Lane nova exige contrato novo registrado antes (migration ou operador).
        _lane_registered = (await db.execute(text("""
            SELECT 1 FROM ml_dataset_contracts
            WHERE model_lane = :lane AND source_filter = :sf
            LIMIT 1
        """), {"lane": model_lane, "sf": source_filter_str})).first()
        if _lane_registered is None:
            raise ValueError(
                "training_lane_not_registered: lane="
                f"{model_lane} source_filter={source_filter_str} — registrar "
                "contrato em ml_dataset_contracts antes de treinar"
            )

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
                "weighted_roc_auc": metrics.get("weighted_roc_auc"),
                "weighted_brier": metrics.get("weighted_brier"),
                "effective_snapshots": metrics.get("effective_snapshots"),
            },
            "test": {
                "precision": (test_metrics or {}).get("precision"),
                "recall": (test_metrics or {}).get("recall"),
                "fpr": (test_metrics or {}).get("fpr"),
                "f1": (test_metrics or {}).get("f1"),
                "roc_auc": (test_metrics or {}).get("roc_auc"),
                "samples": n_test or None,
                "net_ev": (test_metrics or {}).get("net_ev"),
                "weighted_roc_auc": (test_metrics or {}).get("weighted_roc_auc"),
                "weighted_brier": (test_metrics or {}).get("weighted_brier"),
                "effective_snapshots": (test_metrics or {}).get("effective_snapshots"),
                # Fase 1.5 P3 — gates estatísticos de aprovação.
                "roc_auc_ci_low": (test_metrics or {}).get("roc_auc_ci_low"),
                "distinct_days": (test_metrics or {}).get("distinct_days"),
            } if test_metrics else None,
            "feature_importance": _feature_importance or None,
        }
        if metrics.get("intelligence_report") is not None:
            _metrics_json_dict["indicator_intelligence"] = metrics["intelligence_report"]

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
            "label_contract_id": label_contract_id,
            "feature_contract_id": feature_contract_id,
            "train_from": train_from,
            "train_to": train_to,
            "dataset_query_cutoff": dataset_query_cutoff,
            "dataset_hash": dataset_hash,
        }
        if model_lane in {"L3_INTELLIGENCE", "L3_APPROVED_INTELLIGENCE"}:
            from app.ml.intelligence_gate import (
                evaluate_indicator_intelligence_gate,
                evaluate_intelligence_gate,
            )
            if model_lane == "L3_APPROVED_INTELLIGENCE":
                _intelligence_gate = evaluate_indicator_intelligence_gate(
                    _metrics_json_dict, _ml_config
                )
            else:
                _intelligence_gate = evaluate_intelligence_gate(
                    _metrics_json_dict, _ml_config
                )
            _metrics_json_dict["intelligence_gate"] = _intelligence_gate
            _gate_result = {
                "status": "BLOCKED",
                "evaluated_at": datetime.now(timezone.utc).isoformat(),
                "reasons": ["advisory_only_no_execution_authority"],
                "thresholds": {},
                "metrics": {},
            }
            _metrics_json_dict = merge_promotion_gate_into_metrics_json(
                _metrics_json_dict, _gate_result
            )
        else:
            _gate_result = evaluate_promotion_gate(_gate_input, promotion_config=_ml_config)
            _metrics_json_dict = merge_promotion_gate_into_metrics_json(_metrics_json_dict, _gate_result)
        from app.ml.model_governance import governance_from_gate
        _governance = governance_from_gate(
            descriptive_gate=(
                _metrics_json_dict.get("intelligence_gate")
                if model_lane in {"L3_INTELLIGENCE", "L3_APPROVED_INTELLIGENCE"}
                else None
            ),
            predictive_gate=_gate_result,
        )
        _governance_reason = {
            "promotion_gate_status": _gate_result["status"],
            "promotion_gate_reasons": _gate_result["reasons"],
        }
        _metrics_json_dict = _json_finite(_metrics_json_dict)
        _metrics_json = json.dumps(
            _metrics_json_dict,
            default=_json_default,
            allow_nan=False,
        )
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
                source_filter, dataset_contract_id,
                label_contract_id, feature_contract_id,
                train_from, train_to, dataset_query_cutoff, dataset_hash,
                descriptive_status, predictive_status,
                calibration_authority, rule_generation_authority,
                autopilot_authority, execution_authority, governance_reason
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
                :source_filter, :dataset_contract_id,
                :label_contract_id, :feature_contract_id,
                :train_from, :train_to, :dataset_query_cutoff, :dataset_hash,
                :descriptive_status, :predictive_status,
                :calibration_authority, :rule_generation_authority,
                :autopilot_authority, :execution_authority, :governance_reason
            )
        """), {
            "id": str(model_uuid),
            "version": version,
            "hyperparams": json.dumps(
                hyperparams_full,
                default=_json_default,
                allow_nan=False,
            ),
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
                f"roc_auc={f'{roc_auc:.4f}' if roc_auc is not None else 'N/A'} | "
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
            "label_contract_id": label_contract_id,
            "feature_contract_id": feature_contract_id,
            "train_from": train_from,
            "train_to": train_to,
            "dataset_query_cutoff": dataset_query_cutoff,
            "dataset_hash": dataset_hash,
            **_governance,
            "governance_reason": json.dumps(_governance_reason),
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

        # Fase 1 B.4 — registro do dataset na MESMA transação. Sem linha em
        # ml_training_dataset o treino não existe (atomicidade com ml_models).
        if not dataset_stats:
            raise ValueError(
                "ml_training_dataset_stats_required: caller deve fornecer "
                "n_samples/n_positive/n_negative/positive_rate do dataset treinado"
            )
        await db.execute(text("""
            INSERT INTO ml_training_dataset (
                id, model_id, dataset_contract_id, source_filter,
                n_samples, n_positive, n_negative, positive_rate,
                cutoff_at, train_from, train_to, win_threshold_s
            ) VALUES (
                gen_random_uuid(), :mid, :dcid, :sf,
                :n_samples, :n_positive, :n_negative, :positive_rate,
                :cutoff_at, :train_from, :train_to, :win_threshold_s
            )
        """), {
            "mid": str(model_uuid),
            "dcid": dataset_contract_id,
            "sf": source_filter_str,
            "n_samples": int(dataset_stats["n_samples"]),
            "n_positive": int(dataset_stats["n_positive"]),
            "n_negative": int(dataset_stats["n_negative"]),
            "positive_rate": float(dataset_stats["positive_rate"]),
            "cutoff_at": dataset_query_cutoff,
            "train_from": train_from,
            "train_to": train_to,
            # B.3 — valor efetivamente usado, gravado também no registro de dataset.
            "win_threshold_s": int(win_fast_threshold_s),
        })

        # Fase 1 B.4/E.4 — corrida do promotion gate persistida com reason legível
        # (antes só existia dentro de metrics_json; ml_promotion_gate_results
        # tinha zero linhas para qualquer modelo).
        await db.execute(text("""
            INSERT INTO ml_promotion_gate_results (
                id, model_id, gate_version, status, reasons_json, input_json
            ) VALUES (
                gen_random_uuid(), :mid, :gate_version, :status,
                CAST(:reasons AS JSONB), CAST(:input AS JSONB)
            )
        """), {
            "mid": str(model_uuid),
            "gate_version": str(_gate_result.get("gate_version") or "promotion_gate_v1"),
            "status": str(_gate_result["status"]),
            "reasons": json.dumps(_gate_result.get("reasons") or [], default=_json_default),
            "input": json.dumps(_json_finite(_gate_input), default=_json_default),
        })

        logger.info(
            "[MLChallenger] Registered %s model_id=%s roc_auc=%s version=%s",
            model_type,
            model_uuid,
            f"{roc_auc:.4f}" if roc_auc is not None else "N/A",
            version_str,
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
    def _catboost_lane_for_sources(
        cb_sources: List[str],
        advisory_intelligence: bool = False,
    ) -> str:
        """Return the persisted model_lane for a CatBoost source policy."""
        if advisory_intelligence and set(cb_sources) == {"L3", "L3_REJECTED"}:
            return "L3_CONTEXTUAL_INTELLIGENCE"
        if cb_sources == ["L3"]:
            return "L3_APPROVED_INTELLIGENCE" if advisory_intelligence else "L3_PROFILE"
        if cb_sources == ["L3_LAB"]:
            return "L3_LAB_PROFILE"
        if cb_sources == ["L3_REJECTED"]:
            return "L3_INTELLIGENCE"
        return "L3_PROFILE"

    @staticmethod
    def _catboost_dataset_policy_for_sources(
        cb_sources: List[str],
        advisory_intelligence: bool = False,
    ) -> str:
        """Return the governance dataset_policy label for a CatBoost source policy."""
        if advisory_intelligence and set(cb_sources) == {"L3", "L3_REJECTED"}:
            return "ALL_CANDIDATES_CONTEXTUAL"
        if cb_sources == ["L3"]:
            return "L3_APPROVED_INTELLIGENCE" if advisory_intelligence else "L3_ONLY"
        if cb_sources == ["L3_LAB"]:
            return "L3_LAB_ONLY"
        if cb_sources == ["L3_REJECTED"]:
            return "L3_REJECTED_ONLY"
        return "L3_COMBINED"

    async def _prepare_catboost_gate_records(
        self,
        db: AsyncSession,
        user_id: UUID,
        *,
        lookback_days: int,
        cb_sources: List[str],
        dataset_query_cutoff: datetime,
        ml_config: Dict[str, Any],
        advisory_intelligence: bool = False,
        strategy_tp_pct: Optional[float] = None,
        collect_diagnostics: bool = False,
    ) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """Prepare the exact CatBoost gate population for train and dry-run."""
        cb_lane = self._catboost_lane_for_sources(
            cb_sources, advisory_intelligence=advisory_intelligence
        )
        cb_dataset_policy = self._catboost_dataset_policy_for_sources(
            cb_sources, advisory_intelligence=advisory_intelligence
        )
        dataset_valid_from = parse_required_ml_dataset_valid_from(ml_config)
        l3_dataset_valid_from = dataset_valid_from
        if ml_config.get("ml_l3_dataset_valid_from") not in (None, ""):
            l3_dataset_valid_from = parse_required_ml_dataset_valid_from({
                "ml_dataset_valid_from": ml_config["ml_l3_dataset_valid_from"]
            })
        if cb_lane == "L3_APPROVED_INTELLIGENCE":
            cb_dataset_valid_from = parse_required_ml_dataset_valid_from({
                "ml_dataset_valid_from": ml_config["ml_l3_intelligence_valid_from"]
            })
        elif cb_lane == "L3_INTELLIGENCE":
            cb_dataset_valid_from = dataset_valid_from
        else:
            cb_dataset_valid_from = l3_dataset_valid_from

        cb_all_records = await self._load_shadow_data(
            db,
            user_id,
            lookback_days,
            cb_sources,
            dataset_valid_from=cb_dataset_valid_from,
            dataset_query_cutoff=dataset_query_cutoff,
            maturity_embargo_margin_minutes=ml_config.get(
                "ml_maturity_embargo_margin_minutes"
            ),
            collect_diagnostics=collect_diagnostics,
        )
        maturity_diagnostics = dict(self._last_shadow_load_diagnostics)
        cb_profile_records = [r for r in cb_all_records if r.get("profile_id")]
        barrier_meta: Dict[str, Any] = {}
        if cb_sources in (["L3"], ["L3_REJECTED"]) and cb_lane != "L3_APPROVED_INTELLIGENCE":
            if ml_config.get("shadow_barrier_mode") in (None, ""):
                raise ValueError("missing_shadow_barrier_mode_for_l3_dataset_contract")
            if strategy_tp_pct is None:
                strategy_tp_pct = await self._load_strategy_tp_pct(db, user_id)
            cb_records, barrier_meta = _filter_l3_barrier_contract(
                cb_profile_records,
                expected_mode=str(ml_config["shadow_barrier_mode"]),
                expected_tp_pct=strategy_tp_pct,
            )
        else:
            cb_records = cb_profile_records

        l3_meta = self._l3_strict_meta(
            cb_all_records, cb_profile_records, cb_sources
        )
        l3_meta.update(barrier_meta)
        l3_meta["dataset_policy"] = cb_dataset_policy
        l3_meta["included_trade_count"] = len(cb_records)
        l3_meta["dataset_valid_from"] = cb_dataset_valid_from.isoformat()
        return cb_records, {
            "lane": cb_lane,
            "dataset_policy": cb_dataset_policy,
            "dataset_valid_from": cb_dataset_valid_from,
            "all_record_count": len(cb_all_records),
            "records_with_profile": len(cb_profile_records),
            "maturity_diagnostics": maturity_diagnostics,
            "barrier_contract": barrier_meta,
            "l3_strict_meta": l3_meta,
        }

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
        win_fast_threshold_s: Optional[float] = None,
        allow_mixed_source: bool = False,
        advisory_intelligence: bool = False,
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
        dataset_query_cutoff = datetime.now(timezone.utc)
        maturity_embargo_margin_minutes = ml_config.get(
            "ml_maturity_embargo_margin_minutes"
        )
        strategy_tp_pct = await self._load_strategy_tp_pct(db, user_id)
        # Fase 1 B.3 — fonte única do win threshold: SEMPRE a config ativa.
        # Chave ausente aborta o treino; parâmetro divergente passado por
        # chamada é exceção dura (v80 treinou com 14400 vs contrato 1800).
        if ml_config.get("ml_win_fast_threshold_seconds") in (None, ""):
            raise MLDatasetConfigError(
                "missing_ml_win_fast_threshold_seconds: gravar em "
                "config_profiles(config_type='ml') antes do treino"
            )
        _cfg_win_threshold_s = float(ml_config["ml_win_fast_threshold_seconds"])
        if (
            win_fast_threshold_s is not None
            and float(win_fast_threshold_s) != _cfg_win_threshold_s
        ):
            raise ValueError(
                "win_fast_threshold_divergent: caller="
                f"{win_fast_threshold_s} config={_cfg_win_threshold_s} — "
                "a config ml_win_fast_threshold_seconds é a única fonte"
            )
        win_fast_threshold_s = _cfg_win_threshold_s
        dataset_valid_from = parse_required_ml_dataset_valid_from(ml_config)
        min_lgbm_retrain_eligible = (
            _require_positive_int_config(ml_config, "ml_retrain_min_eligible_rows")
            if enable_lightgbm else None
        )
        threshold_grid_step = float(ml_config.get("ml_threshold_grid_step", 0.01))
        threshold_min_positives = int(ml_config.get("ml_threshold_min_positives", 10))
        promotion_min_test_samples = (
            _require_positive_int_config(
                ml_config, "ml_promotion_min_test_samples"
            )
            if enable_catboost
            else None
        )
        label_objective = str(ml_config.get("ml_label_objective") or "fast_tp")
        if label_objective not in {"fast_tp", "positive_net_return"}:
            raise ValueError(f"unsupported_ml_label_objective:{label_objective}")
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
                db, user_id, lookback_days, lgbm_sources,
                dataset_valid_from=dataset_valid_from,
                dataset_query_cutoff=dataset_query_cutoff,
                maturity_embargo_margin_minutes=maturity_embargo_margin_minutes,
            )
            lgbm_barrier_meta: Dict[str, Any] = {}
            if lgbm_sources == ["L1_SPECTRUM"]:
                lgbm_records, lgbm_barrier_meta = _filter_l3_barrier_contract(
                    lgbm_records,
                    expected_mode=str(ml_config.get("shadow_barrier_mode") or "FIXED"),
                    expected_tp_pct=strategy_tp_pct,
                )
            logger.info(
                "[MLChallenger] Lane1/LightGBM: sources=%s records=%d", lgbm_sources, len(lgbm_records),
            )
            if min_lgbm_retrain_eligible is not None and len(lgbm_records) < min_lgbm_retrain_eligible:
                results["lightgbm"] = {
                    "status": "skipped",
                    "reason": "insufficient_retrain_eligible_rows",
                    "records": len(lgbm_records),
                    "min_required": min_lgbm_retrain_eligible,
                    "sources": lgbm_sources,
                    "dataset_valid_from": dataset_valid_from.isoformat()
                    if hasattr(dataset_valid_from, "isoformat") else str(dataset_valid_from),
                    "dataset_query_cutoff": dataset_query_cutoff.isoformat(),
                    "maturity_embargo_margin_minutes": maturity_embargo_margin_minutes,
                    "barrier_contract": lgbm_barrier_meta,
                }
            elif len(lgbm_records) < MIN_RECORDS:
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
                    # Fase 1.5 P3 — determinismo + gates estatísticos, tudo de
                    # config (fail-closed). Reutiliza o helper do gate.
                    _train_seed = _require_positive_int_config(ml_config, "ml_training_seed")
                    _bootstrap_iters = _require_positive_int_config(
                        ml_config, "ml_approval_bootstrap_iterations"
                    )
                    if ml_config.get("ml_approval_auc_ci_level") is None:
                        raise ValueError("missing_ml_approval_auc_ci_level")
                    _auc_ci_level = float(ml_config["ml_approval_auc_ci_level"])
                    _optuna_timeout = int(ml_config.get("ml_optuna_timeout_seconds", 180))
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
                                seed=_train_seed,
                                optuna_timeout_s=_optuna_timeout,
                                auc_ci_level=_auc_ci_level,
                                bootstrap_iterations=_bootstrap_iters,
                            )
                            # Fase 1.5 P3 — cobertura temporal do test (gate
                            # ml_approval_min_distinct_days): dias UTC distintos
                            # entre os created_at do split de teste.
                            _lgbm_at_te = (
                                _lgbm_split["meta_te"][1]
                                if _lgbm_split.get("has_test") else None
                            )
                            if lgbm_result.get("test_metrics") and _lgbm_at_te:
                                lgbm_result["test_metrics"]["distinct_days"] = len({
                                    _ts.date() for _ts in _lgbm_at_te if _ts is not None
                                })
                            _lgbm_at_tr = _lgbm_split["meta_tr"][1]  # created_at post-purge train rows
                            _lgbm_n = (
                                len(y_tr) + len(y_va)
                                + (len(y_te) if y_te is not None else 0)
                            )
                            _lgbm_pos = int(
                                y_tr.sum() + y_va.sum()
                                + (y_te.sum() if y_te is not None else 0)
                            )
                            lgbm_dataset_stats = {
                                "n_samples": _lgbm_n,
                                "n_positive": _lgbm_pos,
                                "n_negative": _lgbm_n - _lgbm_pos,
                                "positive_rate": (_lgbm_pos / _lgbm_n) if _lgbm_n else 0.0,
                            }
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
                                    "dataset_query_cutoff": dataset_query_cutoff,
                                    "dataset_hash": hashlib.sha256(
                                        "|".join(sorted(str(x) for x in shadow_ids if x)).encode()
                                    ).hexdigest(),
                                },
                                threshold=lgbm_result["threshold"],
                                profile_id=profile_id,
                                model_lane="L1_SPECTRUM",
                                test_metrics=lgbm_result.get("test_metrics"),
                                win_fast_threshold_s=win_fast_threshold_s,
                                dataset_stats=lgbm_dataset_stats,
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
                    # B.4 — rollback comprovado: persistência parcial nunca sobrevive.
                    await db.rollback()
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
            min_catboost_retrain_eligible = _require_positive_int_config(
                ml_config, "ml_catboost_retrain_min_eligible_rows"
            )
            # L3_PROFILE_STRICT policy: load all records first for metadata, then filter.
            # L3 has 66%+ NULL profile_id — training without filter produces a "global/unknown"
            # model, defeating the purpose of the L3_PROFILE lane.
            cb_records, cb_gate_meta = await self._prepare_catboost_gate_records(
                db,
                user_id,
                lookback_days=lookback_days,
                cb_sources=cb_sources,
                dataset_query_cutoff=dataset_query_cutoff,
                ml_config=ml_config,
                advisory_intelligence=advisory_intelligence,
                strategy_tp_pct=strategy_tp_pct,
            )
            cb_lane = cb_gate_meta["lane"]
            cb_dataset_policy = cb_gate_meta["dataset_policy"]
            barrier_meta = cb_gate_meta["barrier_contract"]
            l3_meta = cb_gate_meta["l3_strict_meta"]
            logger.info(
                "[MLChallenger] Lane2/CatBoost: sources=%s all=%d strict=%d excluded_null=%d "
                "distinct_profiles=%d unknown_pct=%.1f%%",
                cb_sources, cb_gate_meta["all_record_count"], len(cb_records),
                l3_meta["excluded_null_profile_id"],
                l3_meta["distinct_profiles"],
                l3_meta["unknown_profile_pct"],
            )
            if (
                min_catboost_retrain_eligible is not None
                and len(cb_records) < min_catboost_retrain_eligible
            ):
                results["catboost"] = {
                    "status": "skipped",
                    "reason": "insufficient_retrain_eligible_rows",
                    "records": len(cb_records),
                    "min_required": min_catboost_retrain_eligible,
                    "deficit": min_catboost_retrain_eligible - len(cb_records),
                    "sources": cb_sources,
                    "dataset_query_cutoff": dataset_query_cutoff.isoformat(),
                    "maturity_embargo_margin_minutes": maturity_embargo_margin_minutes,
                    "maturity_diagnostics": cb_gate_meta["maturity_diagnostics"],
                    "barrier_contract": barrier_meta,
                    "l3_strict_meta": l3_meta,
                }
            elif _is_installed("catboost"):
                try:
                    (
                        X, y, all_cols, cat_indices, returns, created_at, shadow_ids,
                        holding_seconds, snapshot_keys,
                    ) = self._build_l3_dataset(
                        cb_records, feature_columns, win_fast_threshold_s,
                        lane_contract=cb_lane_contract, feature_ranges=feature_ranges,
                        backfilled_feature_names=backfilled_feature_names,
                        backfill_marker_key=backfill_marker_key,
                        lane_name=cb_lane,
                        label_objective=label_objective,
                        fee_roundtrip_pct=float(ml_config["ml_fee_roundtrip_pct"]),
                    )
                    if len(y) < min_catboost_retrain_eligible:
                        results["catboost"] = {
                            "status": "skipped",
                            "reason": "insufficient_labeled",
                            "records": len(y),
                            "min_required": min_catboost_retrain_eligible,
                        }
                    else:
                        _cb_split = self._chronological_split_with_embargo(
                            X, y,
                            metadata=[returns, created_at, shadow_ids, snapshot_keys],
                            created_at=created_at,
                            holding_seconds=holding_seconds,
                            group_ids=snapshot_keys,
                            val_fraction=VAL_FRACTION,
                            embargo_seconds=embargo_seconds,
                            min_train_size=min_catboost_retrain_eligible,
                            min_validation_size=threshold_min_positives,
                            min_test_size=promotion_min_test_samples,
                        )
                        X_tr, y_tr = _cb_split["X_tr"], _cb_split["y_tr"]
                        X_va, y_va = _cb_split["X_va"], _cb_split["y_va"]
                        X_te, y_te = _cb_split["X_te"], _cb_split["y_te"]
                        ret_va = _cb_split["meta_va"][0]
                        ret_te = _cb_split["meta_te"][0] if _cb_split["has_test"] else None
                        intelligence_lane = cb_lane in {
                            "L3_INTELLIGENCE", "L3_APPROVED_INTELLIGENCE"
                        }
                        train_weights = val_weights = test_weights = None
                        if intelligence_lane:
                            from app.ml.indicator_intelligence import inverse_group_frequency_weights
                            train_weights = inverse_group_frequency_weights(_cb_split["meta_tr"][3])
                            val_weights = inverse_group_frequency_weights(_cb_split["meta_va"][3])
                            test_weights = inverse_group_frequency_weights(_cb_split["meta_te"][3])
                        min_feature_coverage = float(ml_config.get("ml_feature_min_coverage_pct", 0.30))
                        l3_exclusions = [str(x) for x in (ml_config.get("ml_l3_feature_exclusions") or [])]
                        stable_indices = _stable_train_feature_indices(
                            X_tr, all_cols,
                            min_coverage=min_feature_coverage,
                            excluded=l3_exclusions,
                        )
                        if not stable_indices:
                            raise ValueError("no_stable_l3_features_after_train_filter")
                        all_cols = [all_cols[i] for i in stable_indices]
                        X_tr = X_tr[:, stable_indices]
                        X_va = X_va[:, stable_indices]
                        X_te = X_te[:, stable_indices] if X_te is not None else None
                        cat_indices = [
                            stable_indices.index(i) for i in cat_indices if i in stable_indices
                        ]
                        logger.info(
                            "[MLChallenger] CatBoost split: train=%d val=%d test=%d "
                            "purged_train=%d purged_validation=%d group_purged=%d "
                            "embargoed_test=%d embargo_s=%d strategy=%s",
                            len(y_tr), len(y_va),
                            len(y_te) if y_te is not None else 0,
                            _cb_split["n_purged"],
                            _cb_split["n_purged_val_test"],
                            _cb_split["n_group_purged"],
                            _cb_split["n_embargoed"],
                            embargo_seconds,
                            _cb_split["split_diagnostics"]["split_strategy"],
                        )
                        if not _cb_split["has_test"]:
                            results["catboost"] = {
                                "status": "skipped",
                                "reason": _cb_split["split_diagnostics"].get(
                                    "block_reason",
                                    "insufficient_promotion_holdout",
                                ),
                                "records": len(y),
                                "min_test_samples": promotion_min_test_samples,
                                "split_diagnostics": _cb_split["split_diagnostics"],
                            }
                        elif len(y_va) < threshold_min_positives:
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
                                train_weights, val_weights, test_weights,
                                "weighted_roc_auc" if intelligence_lane else "net_ev",
                                ml_config.get("ml_intelligence_catboost_params")
                                if intelligence_lane else None,
                            )
                            intelligence_report = None
                            if intelligence_lane:
                                from app.ml.indicator_intelligence import build_indicator_intelligence_report
                                intelligence_report = build_indicator_intelligence_report(
                                    X_tr, y_tr, X_va, y_va, X_te, y_te, all_cols,
                                    train_weights, val_weights, test_weights,
                                    ret_va, ret_te,
                                    min_effective_cases=float(
                                        ml_config["ml_intelligence_indicator_min_effective_cases"]
                                    ),
                                    min_abs_lift=float(
                                        ml_config["ml_intelligence_indicator_min_abs_lift"]
                                    ),
                                    label=label_objective,
                                )
                            _cb_n = (
                                len(y_tr) + len(y_va)
                                + (len(y_te) if y_te is not None else 0)
                            )
                            _cb_pos = int(
                                y_tr.sum() + y_va.sum()
                                + (y_te.sum() if y_te is not None else 0)
                            )
                            cb_dataset_stats = {
                                "n_samples": _cb_n,
                                "n_positive": _cb_pos,
                                "n_negative": _cb_n - _cb_pos,
                                "positive_rate": (_cb_pos / _cb_n) if _cb_n else 0.0,
                            }
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
                                    "dataset_query_cutoff": dataset_query_cutoff,
                                    "dataset_hash": hashlib.sha256(
                                        "|".join(sorted(str(x) for x in shadow_ids if x)).encode()
                                    ).hexdigest(),
                                    "split_diagnostics": _cb_split["split_diagnostics"],
                                    "dataset_policy": cb_dataset_policy,
                                    "cat_features": [all_cols[i] for i in cat_indices],
                                    "label_objective": label_objective,
                                    "intelligence_report": intelligence_report,
                                    **l3_meta,
                                },
                                threshold=cb_result["threshold"],
                                profile_id=profile_id,
                                model_lane=cb_lane,
                                cat_feature_indices=cat_indices,
                                test_metrics=cb_result.get("test_metrics"),
                                win_fast_threshold_s=win_fast_threshold_s,
                                dataset_stats=cb_dataset_stats,
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
                                "cat_features": [all_cols[i] for i in cat_indices],
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
                    # B.4 — rollback comprovado: persistência parcial nunca sobrevive.
                    await db.rollback()
                    results["catboost"] = {"status": "failed", "error": str(exc)}
            else:
                results["catboost"] = {"status": "not_installed"}

        return results
