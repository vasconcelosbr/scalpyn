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
import io
import json
import logging
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("scalpyn.services.ml_challenger")

_TRAINER_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ml_challenger")

MIN_RECORDS = int(__import__("os").getenv("ML_CHALLENGER_MIN_RECORDS", "200"))
LOOKBACK_DAYS = int(__import__("os").getenv("ML_CHALLENGER_LOOKBACK_DAYS", "60"))
VAL_FRACTION = float(__import__("os").getenv("ML_CHALLENGER_VAL_FRACTION", "0.20"))
N_TRIALS_LGBM = int(__import__("os").getenv("ML_CHALLENGER_N_TRIALS_LGBM", "30"))
N_TRIALS_CB = int(__import__("os").getenv("ML_CHALLENGER_N_TRIALS_CB", "20"))

# Lane 1 (XGBoost challenger): global opportunity signal, no profile bias.
LGBM_TRAIN_SOURCES: List[str] = ["L1_SPECTRUM"]
# Lane 2 (CatBoost validator): profile-scoped decisions only.
CATBOOST_TRAIN_SOURCES: List[str] = ["L3", "L3_LAB"]
# Backwards-compat alias — callers that pass source_filter still work.
TRAIN_SOURCES: List[str] = LGBM_TRAIN_SOURCES  # was ["L3", "L1_SPECTRUM"] — deprecated


def _is_installed(package: str) -> bool:
    try:
        __import__(package)
        return True
    except ImportError:
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
) -> Dict[str, Any]:
    import lightgbm as lgb
    import numpy as np
    import optuna
    from sklearn.metrics import roc_auc_score, average_precision_score, f1_score

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
    binary_preds = (val_preds >= 0.5).astype(int)
    f1 = float(f1_score(y_val, binary_preds, zero_division=0))
    threshold = float(np.median(val_preds))

    return {
        "model": final_model,
        "model_type": "lightgbm",
        "best_params": study.best_params,
        "metrics": {
            "roc_auc": roc_auc,
            "pr_auc": pr_auc,
            "f1": f1,
            "n_trials": n_trials,
            "best_trial_number": study.best_trial.number,
            "best_trial_value": study.best_trial.value,
            "val_samples": int(len(y_val)),
            "train_samples": int(len(y_train)),
            "positive_rate": float(y_val.mean()) if hasattr(y_val, "mean") else 0.0,
        },
        "threshold": threshold,
    }


def _train_catboost_sync(
    X_train, y_train, X_val, y_val,
    feature_names: List[str],
    n_trials: int = 20,
    cat_feature_indices: Optional[List[int]] = None,
) -> Dict[str, Any]:
    from catboost import CatBoostClassifier, Pool
    import numpy as np
    import optuna
    from sklearn.metrics import roc_auc_score, average_precision_score, f1_score

    optuna.logging.set_verbosity(optuna.logging.WARNING)

    # cat_feature_indices: colunas de source_encoded e profile_id_encoded.
    # Pool com cat_features ativa o encoding interno CatBoost (ordered target statistics)
    # em vez de tratar os IDs como scalars contínuos.
    # Valores inteiros são válidos — CatBoost converte internamente para string-category.
    train_pool = Pool(
        X_train, label=y_train, feature_names=list(feature_names),
        cat_features=cat_feature_indices,
    )
    val_pool = Pool(
        X_val, label=y_val, feature_names=list(feature_names),
        cat_features=cat_feature_indices,
    )

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
    binary_preds = (val_preds >= 0.5).astype(int)
    f1 = float(f1_score(y_val, binary_preds, zero_division=0))
    threshold = float(np.median(val_preds))

    return {
        "model": final_model,
        "model_type": "catboost",
        "best_params": study.best_params,
        "metrics": {
            "roc_auc": roc_auc,
            "pr_auc": pr_auc,
            "f1": f1,
            "n_trials": n_trials,
            "best_trial_number": study.best_trial.number,
            "best_trial_value": study.best_trial.value,
            "val_samples": int(len(y_val)),
            "train_samples": int(len(y_train)),
            "positive_rate": float(y_val.mean()) if hasattr(y_val, "mean") else 0.0,
        },
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
    ) -> List[Dict[str, Any]]:
        sources = source_filter if source_filter is not None else TRAIN_SOURCES
        # Build per-source placeholders to avoid any injection risk
        source_placeholders = ", ".join(f":src_{i}" for i in range(len(sources)))
        source_params = {f"src_{i}": s for i, s in enumerate(sources)}
        rows = (await db.execute(text(f"""
            SELECT
                id::text          AS shadow_id,
                symbol,
                source,
                pnl_pct,
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
              AND created_at >= NOW() - CAST(:days AS interval)
            ORDER BY created_at ASC
        """), {"uid": str(user_id), "days": f"{lookback_days} days", **source_params})).fetchall()
        logger.info(
            "[MLChallenger] _load_shadow_data: sources=%s rows=%d user=%s",
            sources, len(rows), user_id,
        )
        return [dict(r._mapping) for r in rows]

    # Ordinal encoding for shadow trade source (stable across versions)
    _SOURCE_ENCODING: Dict[str, int] = {
        "L1_SPECTRUM": 0,
        "L3": 1,
        "L3_LAB": 2,
        "L3_REJECTED": 3,
        "L3_SIMULATED": 4,
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
        else:
            y = (df["outcome"] == "TP_HIT").astype(int).values

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

        # Categorical encoding — dois scalars por row
        source_enc = np.array(
            [self._SOURCE_ENCODING.get(r.get("source", "L3"), 1) for r in valid_records],
            dtype=float,
        )
        profile_enc = np.array(
            [abs(hash(str(r.get("profile_id") or ""))) % 10000 for r in valid_records],
            dtype=float,
        )

        # Stack: base features + source_encoded + profile_id_encoded
        X = np.column_stack([X_base, source_enc, profile_enc])
        all_feature_names = available + ["source_encoded", "profile_id_encoded"]

        # Índices das colunas categóricas — usados por Pool(cat_features=...) no CatBoost.
        # source_encoded e profile_id_encoded são as ÚLTIMAS duas colunas.
        n_base = X_base.shape[1]
        cat_feature_indices = [n_base, n_base + 1]  # source_encoded, profile_id_encoded

        return X, y, all_feature_names, cat_feature_indices

    def _chronological_split(self, X, y, val_fraction: float = 0.20):
        n = len(y)
        split = max(1, int(n * (1.0 - val_fraction)))
        return X[:split], y[:split], X[split:], y[split:]

    async def _next_version(self, db: AsyncSession) -> str:
        row = (await db.execute(
            text("SELECT COALESCE(MAX(version::integer), 0) + 1 FROM ml_models")
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
        n_train = metrics.get("train_samples", 0)
        n_val = metrics.get("val_samples", 0)

        # Infere lane a partir do model_type se não fornecida explicitamente
        if model_lane is None:
            model_lane = "L3_PROFILE" if model_type == "catboost" else "L1_SPECTRUM"

        # Armazena em ml_models (storage BYTEA canônico)
        await db.execute(text("""
            INSERT INTO ml_models (
                id, version, status,
                hyperparams, train_samples, val_samples,
                f1_score, roc_auc,
                model_path, decision_threshold,
                notes, model_blob,
                model_scope, profile_id,
                label_version, model_lane
            ) VALUES (
                :id, :version, 'candidate',
                :hyperparams::jsonb, :n_train, :n_val,
                :f1, :roc_auc,
                :model_path, :threshold,
                :notes, :blob,
                :scope, :pid::uuid,
                :label_version, :model_lane
            )
        """), {
            "id": str(model_uuid),
            "version": version,
            "hyperparams": json.dumps(metrics),
            "n_train": n_train,
            "n_val": n_val,
            "f1": f1,
            "roc_auc": roc_auc,
            "model_path": f"db://ml_models/{model_type}_v{version}",
            "threshold": threshold,
            "notes": (
                f"Challenger {model_type} | lane={model_lane} | user_id={user_id} | "
                f"roc_auc={roc_auc:.4f} | v{version} | trained_by=MLChallengerService"
            ),
            "blob": model_blob,
            "label_version": "is_win_fast_v1",
            "model_lane": model_lane,
            "scope": "profile" if profile_id else "global",
            "pid": str(profile_id) if profile_id else None,
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
                :pid::uuid, NULL,
                'win_fast', 'all',
                :metrics::jsonb, :threshold,
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
    ) -> Dict[str, Any]:
        """
        Treina challengers habilitados e registra no banco.

        Arquitetura 2-lanes:
          - LightGBM (Lane 1): L1_SPECTRUM — global opportunity filter
          - CatBoost  (Lane 2): L3 + L3_LAB — profile validator com categóricas

        Parâmetros:
            lgbm_source_filter: override para fontes do LightGBM (default: LGBM_TRAIN_SOURCES)
            catboost_source_filter: override para fontes do CatBoost (default: CATBOOST_TRAIN_SOURCES)
            source_filter: override legacy (aplicado a ambos se lgbm/catboost não fornecidos)
        """
        if not enable_lightgbm and not enable_catboost:
            return {"skipped": "no_challengers_enabled"}

        try:
            from app.ml.feature_extractor import FEATURE_COLUMNS as _FC
            feature_columns = list(_FC)
        except ImportError:
            logger.warning("[MLChallenger] feature_extractor não disponível")
            return {"skipped": "feature_extractor_unavailable"}

        results: Dict[str, Any] = {}
        loop = asyncio.get_event_loop()

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
                    X, y, available_cols = self._build_dataset(lgbm_records, feature_columns)
                    if len(y) < MIN_RECORDS:
                        results["lightgbm"] = {"status": "skipped", "reason": "insufficient_labeled"}
                    else:
                        X_tr, y_tr, X_va, y_va = self._chronological_split(X, y, VAL_FRACTION)
                        if len(y_va) < 10:
                            results["lightgbm"] = {"status": "skipped", "reason": "val_too_small"}
                        else:
                            logger.info(
                                "[MLChallenger] Treinando LightGBM (n_train=%d n_trials=%d)",
                                len(y_tr), n_trials_lgbm,
                            )
                            lgbm_result = await loop.run_in_executor(
                                _TRAINER_POOL,
                                _train_lgbm_sync,
                                X_tr, y_tr, X_va, y_va, n_trials_lgbm,
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
                            )
                            await db.commit()
                            results["lightgbm"] = {
                                "status": "trained",
                                "model_id": str(model_id),
                                "lane": "L1_SPECTRUM",
                                "sources": lgbm_sources,
                                "metrics": lgbm_result["metrics"],
                                "threshold": lgbm_result["threshold"],
                            }
                            logger.info(
                                "[MLChallenger] LightGBM OK: roc_auc=%.4f model_id=%s",
                                lgbm_result["metrics"]["roc_auc"], model_id,
                            )
                except Exception as exc:
                    logger.exception("[MLChallenger] LightGBM falhou: %s", exc)
                    results["lightgbm"] = {"status": "failed", "error": str(exc)}
            else:
                results["lightgbm"] = {"status": "not_installed"}

        # ── Lane 2: CatBoost em L3 + L3_LAB com features categóricas ────────────
        if enable_catboost:
            cb_sources = catboost_source_filter or (source_filter if source_filter else CATBOOST_TRAIN_SOURCES)
            cb_records = await self._load_shadow_data(db, user_id, lookback_days, cb_sources)
            logger.info(
                "[MLChallenger] Lane2/CatBoost: sources=%s records=%d", cb_sources, len(cb_records),
            )
            if len(cb_records) < MIN_RECORDS:
                results["catboost"] = {
                    "status": "skipped",
                    "reason": "insufficient_data",
                    "records": len(cb_records),
                    "min_required": MIN_RECORDS,
                    "sources": cb_sources,
                }
            elif _is_installed("catboost"):
                try:
                    X, y, all_cols, cat_indices = self._build_l3_dataset(cb_records, feature_columns)
                    if len(y) < MIN_RECORDS:
                        results["catboost"] = {"status": "skipped", "reason": "insufficient_labeled"}
                    else:
                        X_tr, y_tr, X_va, y_va = self._chronological_split(X, y, VAL_FRACTION)
                        if len(y_va) < 10:
                            results["catboost"] = {"status": "skipped", "reason": "val_too_small"}
                        else:
                            logger.info(
                                "[MLChallenger] Treinando CatBoost (n_train=%d n_trials=%d features=%d cat=%s)",
                                len(y_tr), n_trials_cb, len(all_cols), cat_indices,
                            )
                            cb_result = await loop.run_in_executor(
                                _TRAINER_POOL,
                                _train_catboost_sync,
                                X_tr, y_tr, X_va, y_va, all_cols, n_trials_cb, cat_indices,
                            )
                            model_id = await self._save_to_db(
                                db, user_id=user_id,
                                model_type="catboost",
                                model_obj=cb_result["model"],
                                feature_columns=all_cols,
                                metrics={
                                    **cb_result["metrics"],
                                    "train_sources": cb_sources,
                                    "cat_features": ["source_encoded", "profile_id_encoded"],
                                },
                                threshold=cb_result["threshold"],
                                profile_id=profile_id,
                                model_lane="L3_PROFILE",
                                cat_feature_indices=cat_indices,
                            )
                            await db.commit()
                            results["catboost"] = {
                                "status": "trained",
                                "model_id": str(model_id),
                                "lane": "L3_PROFILE",
                                "sources": cb_sources,
                                "metrics": cb_result["metrics"],
                                "threshold": cb_result["threshold"],
                                "cat_features": ["source_encoded", "profile_id_encoded"],
                            }
                            logger.info(
                                "[MLChallenger] CatBoost OK: roc_auc=%.4f model_id=%s",
                                cb_result["metrics"]["roc_auc"], model_id,
                            )
                except Exception as exc:
                    logger.exception("[MLChallenger] CatBoost falhou: %s", exc)
                    results["catboost"] = {"status": "failed", "error": str(exc)}
            else:
                results["catboost"] = {"status": "not_installed"}

        return results
