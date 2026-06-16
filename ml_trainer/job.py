import hashlib
import math
import os
import sys
import json
import logging
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version as package_version

# AUDIT_MODE — run deep audit instead of training when env var is set.
if os.getenv("AUDIT_MODE", "false").lower() == "true":
    from ml_trainer.audit import main as _audit_main
    _audit_main()
    sys.exit(0)

# PROBA_ANALYSIS_MODE — probability distribution analysis on active model.
if os.getenv("PROBA_ANALYSIS_MODE", "false").lower() == "true":
    from ml_trainer.proba_analysis import main as _proba_main
    _proba_main()
    sys.exit(0)

import mlflow
import mlflow.xgboost
import optuna
from sqlalchemy import create_engine, text

# Configuração de logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("scalpyn.trainer")

DIRECTIONAL_FEATURE_COLUMNS = {
    "rsi_slope_3",
    "rsi_slope_5",
    "macd_hist_slope_3",
    "macd_hist_slope_5",
    "ema21_ema50_distance_pct",
    "di_plus_minus_diff",
    "adx_slope_3",
    "vwap_reclaim_bool",
    "higher_highs_5",
    "higher_lows_5",
}


def _cfg_float(cfg: dict, key: str, default: float) -> float:
    value = cfg.get(key, default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _cfg_bool(cfg: dict, key: str, default: bool) -> bool:
    value = cfg.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _ratio(value: float | int | None) -> float:
    """Normalize percent-like values to a 0..1 ratio."""
    try:
        val = float(value)
    except (TypeError, ValueError):
        return 0.0
    return val / 100.0 if val > 1.0 else val


def _ml_dependency_versions() -> dict:
    deps = {
        "xgboost": "xgboost",
        "scikit_learn": "scikit-learn",
        "numpy": "numpy",
        "pandas": "pandas",
        "joblib": "joblib",
        "scipy": "scipy",
    }
    versions = {"python": sys.version.split()[0]}
    for key, package_name in deps.items():
        try:
            versions[key] = package_version(package_name)
        except PackageNotFoundError:
            versions[key] = None
    return versions

# -------------------------------------------------------------
# Config via env vars
# -------------------------------------------------------------
DB_URL    = os.environ["DB_URL"]                    # Railway Postgres URL
MODEL_DIR = os.getenv("MODEL_DIR", "/models")       # Railway Volume mount path
# BLOCO C — janela deslizante 30d (regime drift).
# Substituiu 90d fixos — ajustar via env se necessário.
DAYS_LOOKBACK            = int(os.getenv("DAYS_LOOKBACK", "30"))
N_TRIALS                 = int(os.getenv("N_TRIALS", "50"))
MIN_RECORDS              = int(os.getenv("MIN_RECORDS", "200"))
# Minimum AUC required to promote a trained model. Default 0.50 (better than random).
# Set MIN_AUC_TO_SAVE=0.0 to bypass for pipeline validation runs.
MIN_AUC_TO_SAVE          = float(os.getenv("MIN_AUC_TO_SAVE", "0.50"))
# Training mode: 'global' | 'profile' | 'all_profiles'
# 'global' = default — preserves exact existing behavior
# 'profile' = train on shadows from one specific profile (requires PROFILE_ID)
# 'all_profiles' = iterate all active profiles and train one model per profile
TRAINING_MODE            = os.getenv("TRAINING_MODE", "global")
# UUID of the profile to train for when TRAINING_MODE='profile'
PROFILE_ID               = os.getenv("PROFILE_ID", "")
# BLOCO C — source filter agnóstico.
# 'L3' = comportamento atual (fallback seguro).
# 'WATCHLIST_SPOT' = espectro completo (ativar apenas após dataset acumular).
# Controlado pelo operador via env var no Cloud Run Job — ZERO HARDCODE.
ML_SOURCE_FILTER         = os.getenv("ML_SOURCE_FILTER", "L1_SPECTRUM")
# BLOCO C — alvo agnóstico (binary | regression).
# Decisão adiada para após teste de separabilidade do espectro completo.
ML_TARGET_TYPE           = os.getenv("ML_TARGET_TYPE", "binary")
# Optional: exclude a date range with known bad indicators from training.
# Set TRAIN_EXCLUDE_FROM=YYYY-MM-DD and TRAIN_EXCLUDE_TO=YYYY-MM-DD to skip
# a period where features_snapshot contained absent/miscalculated indicators.
TRAIN_EXCLUDE_FROM       = os.getenv("TRAIN_EXCLUDE_FROM", "")   # e.g. "2026-05-01"
TRAIN_EXCLUDE_TO         = os.getenv("TRAIN_EXCLUDE_TO", "")     # e.g. "2026-05-20"

# MLflow — usa volume local por padrão (file://), sem dependência de servidor externo.
# Para usar um servidor MLflow remoto, setar MLFLOW_TRACKING_URI na env do Railway service.
_MLFLOW_URI = os.getenv(
    "MLFLOW_TRACKING_URI",
    f"file://{MODEL_DIR}/mlruns",
)
os.environ["MLFLOW_TRACKING_URI"] = _MLFLOW_URI
mlflow.set_tracking_uri(_MLFLOW_URI)


def _train_for_profile(engine, profile_id_str: str, _ml_cfg: dict, _fee_roundtrip_pct,
                        _label_net_of_fees, _win_fast_threshold_s, _promotion_min_auc,
                        _promotion_min_precision_lift, _promotion_max_fpr,
                        _promotion_max_fpr_relative_increase,
                        _promotion_min_precision_vs_champion, _promotion_min_f1_vs_champion,
                        _promotion_require_all_directional_features):
    """Train a profile-specific model on L3 shadows from one profile.

    TRAINING_MODE='profile' entry point. Uses source='L3' + profile_id filter.
    Aborts if dataset has mixed profile_ids or wrong source (anti-mixing guarantee).
    Saves model with model_scope='profile', profile_id=profile_id_str.
    """
    logger.info("[ProfileTrainer] Training for profile_id=%s", profile_id_str)
    assert profile_id_str, "PROFILE_ID env var required for TRAINING_MODE=profile"

    # Profile-specific MIN_RECORDS — allow smaller datasets for per-profile training
    min_records_profile = int(os.getenv("MIN_RECORDS", "100"))

    # Fetch profile metadata
    profile_updated_at = None
    profile_name = None
    try:
        with engine.connect() as conn:
            p_row = conn.execute(text(
                "SELECT updated_at, name FROM profiles WHERE id = :pid LIMIT 1"
            ), {"pid": profile_id_str}).fetchone()
            if p_row:
                profile_updated_at = p_row.updated_at
                profile_name = p_row.name
    except Exception as e:
        logger.warning("[ProfileTrainer] profile metadata fetch failed: %s", e)

    # Build dataset from shadow_trades WHERE source='L3' AND profile_id=profile_id
    exclude_clause = ""
    exclude_params: dict = {}
    if TRAIN_EXCLUDE_FROM and TRAIN_EXCLUDE_TO:
        exclude_clause = (
            "AND NOT (created_at >= :excl_from AND created_at <= :excl_to)"
        )
        exclude_params = {
            "excl_from": TRAIN_EXCLUDE_FROM,
            "excl_to": f"{TRAIN_EXCLUDE_TO} 23:59:59",
        }

    with engine.connect() as conn:
        dataset_query_cutoff = conn.execute(text("SELECT NOW()")).scalar()
        result = conn.execute(text(f"""
            SELECT
                id::text AS shadow_id,
                symbol, source, pnl_pct, net_return_pct, holding_seconds, outcome,
                features_snapshot, created_at,
                ttt_outcome, ttt_fast_win_bucket,
                time_to_tp_minutes, elapsed_minutes, profit_velocity,
                profile_id::text AS profile_id
            FROM shadow_trades
            WHERE source = 'L3'
              AND profile_id = :profile_id
              AND outcome IN ('TP_HIT', 'SL_HIT', 'TIMEOUT')
              AND pnl_pct IS NOT NULL
              AND features_snapshot IS NOT NULL
              AND features_snapshot::text <> '{{}}'
              AND created_at >= (:dataset_query_cutoff - CAST(:days AS interval))
              AND created_at <= :dataset_query_cutoff
              {exclude_clause}
            ORDER BY created_at ASC
        """), {
            "days": f"{DAYS_LOOKBACK} days",
            "profile_id": profile_id_str,
            "dataset_query_cutoff": dataset_query_cutoff,
            **exclude_params,
        })
        records = [dict(row._mapping) for row in result.fetchall()]

    total = len(records)
    logger.info("[ProfileTrainer] profile_id=%s records=%d", profile_id_str, total)

    if total < min_records_profile:
        logger.info(
            "[ProfileTrainer] insufficient data for profile %s (%d < %d) — skipping",
            profile_id_str, total, min_records_profile,
        )
        return

    import pandas as _pd
    df_check = _pd.DataFrame(records)

    # Anti-mixing assertions
    if "source" in df_check.columns:
        assert df_check["source"].eq("L3").all(), \
            "Profile dataset must only contain L3 source"
    if "profile_id" in df_check.columns:
        assert df_check["profile_id"].nunique() == 1, \
            "Dataset must contain exactly one profile_id"

    # Compute dataset_hash from shadow_trade IDs
    shadow_ids = [r.get("shadow_id", "") for r in records]
    dataset_hash = hashlib.sha256(
        "|".join(sorted(str(x) for x in shadow_ids)).encode()
    ).hexdigest()
    query_hash = hashlib.sha256(
        f"profile_id={profile_id_str}&source=L3&days={DAYS_LOOKBACK}".encode()
    ).hexdigest()

    # Build DataFrame — reuse global builder
    sys.path.insert(0, "/app")
    from app.ml.feature_extractor import (
        FEATURE_COLUMNS, FEATURE_SCHEMA_VERSION, build_training_dataframe, feature_columns_hash,
    )
    from app.ml.trainer import WinFastTrainer

    df = build_training_dataframe(
        records,
        fee_roundtrip_pct=_fee_roundtrip_pct,
        label_net_of_fees=_label_net_of_fees,
        win_fast_threshold_s=_win_fast_threshold_s,
    )
    logger.info("[ProfileTrainer] DataFrame: %d rows, %d cols", len(df), len(df.columns))

    n_trials_profile = int(os.getenv("N_TRIALS", str(N_TRIALS)))
    trainer = WinFastTrainer(n_trials=n_trials_profile)
    try:
        result = trainer.train(df, optuna_storage_url=None, ml_target=ML_TARGET_TYPE)
    except ValueError as exc:
        logger.info("[ProfileTrainer] dataset degenerate — skipping profile %s: %s",
                    profile_id_str, exc)
        return

    new_roc_auc = result["metrics"]["roc_auc"]
    promotion_status = "active" if new_roc_auc >= _promotion_min_auc else "rejected"
    rejection_reason = None if promotion_status == "active" else "roc_auc_below_min"

    _trained_feature_cols = list(result.get("feature_columns") or [
        c for c in FEATURE_COLUMNS
        if c not in set(result.get("features_excluded", []))
    ])
    _feature_columns_hash = result.get("feature_columns_hash") or feature_columns_hash(_trained_feature_cols)
    _feature_schema_version = result.get("feature_schema_version") or FEATURE_SCHEMA_VERSION

    import joblib
    import io
    buf = io.BytesIO()
    model_payload = {
        "model": trainer.model,
        "feature_columns": _trained_feature_cols,
        "metadata": {
            "trained_at": datetime.now(timezone.utc).isoformat(),
            "n_features": len(_trained_feature_cols),
            "target_type": ML_TARGET_TYPE,
            "dependency_versions": _ml_dependency_versions(),
            "feature_columns_hash": _feature_columns_hash,
            "feature_schema_version": _feature_schema_version,
            "dataset_query_cutoff": dataset_query_cutoff.isoformat()
            if hasattr(dataset_query_cutoff, "isoformat") else str(dataset_query_cutoff),
            "profile_id": profile_id_str,
            "profile_name": profile_name,
            "model_scope": "profile",
            "training_scope": "L3",
            "source_filter": "L3",
            "dataset_hash": dataset_hash,
            "query_hash": query_hash,
        },
    }
    joblib.dump(model_payload, buf)
    model_blob = buf.getvalue()

    # MLflow tags
    try:
        client = mlflow.tracking.MlflowClient()
        client.set_tag(result["run_id"], "model_scope", "profile")
        client.set_tag(result["run_id"], "profile_id", profile_id_str)
        client.set_tag(result["run_id"], "profile_name", profile_name or "")
        client.set_tag(result["run_id"], "training_scope", "L3")
        client.set_tag(result["run_id"], "dataset_hash", dataset_hash)
        client.set_tag(result["run_id"], "query_hash", query_hash)
        client.set_tag(result["run_id"], "feature_columns_hash", _feature_columns_hash)
        client.set_tag(result["run_id"], "feature_count", str(len(_trained_feature_cols)))
    except Exception as exc:
        logger.warning("[ProfileTrainer] MLflow annotation failed: %s", exc)

    import datetime as _dt

    def _to_date(v):
        if v is None:
            return None
        if hasattr(v, "date") and callable(v.date):
            return v.date()
        if isinstance(v, _dt.datetime):
            return v.date()
        return v

    try:
        with engine.begin() as conn:
            ver = conn.execute(
                text("SELECT COALESCE(MAX(version::integer), 0) + 1 FROM ml_models")
            ).scalar()
            # Profile models are never activated globally — status stays 'active' but
            # scoped to profile. We do NOT retire global models here.
            conn.execute(text("""
                INSERT INTO ml_models (
                    version, status, hyperparams,
                    train_samples, val_samples, test_samples,
                    precision_score, recall_score, f1_score, roc_auc,
                    win_fast_capture_rate, false_positive_rate,
                    train_from, train_to,
                    model_path, decision_threshold,
                    activated_at, notes,
                    feature_columns_json, feature_columns_hash,
                    feature_count, feature_schema_version,
                    dataset_query_cutoff,
                    model_blob,
                    profile_id, profile_version, model_scope,
                    training_scope, source_filter, dataset_hash, query_hash
                ) VALUES (
                    :version, :status, :hyperparams,
                    :n_train, :n_val, :n_test,
                    :precision, :recall, :f1, :roc_auc,
                    :capture_rate, :fpr,
                    :train_from, :train_to,
                    :model_path, :threshold,
                    CASE WHEN :status = 'active' THEN NOW() ELSE NULL END, :notes,
                    CAST(:feature_columns_json AS JSONB), :feature_columns_hash,
                    :feature_count, :feature_schema_version,
                    :dataset_query_cutoff,
                    :model_blob,
                    CAST(:profile_id AS UUID), :profile_version, :model_scope,
                    :training_scope, :source_filter, :dataset_hash, :query_hash
                )
            """), {
                "version": str(ver),
                "status": promotion_status,
                "hyperparams": json.dumps(
                    {k: (None if isinstance(v, float) and math.isnan(v) else v)
                     for k, v in result["best_params"].items()}
                ),
                "n_train": result["n_train"],
                "n_val": result["n_val"],
                "n_test": result["n_test"],
                "precision": result["metrics"]["precision"],
                "recall": result["metrics"]["recall"],
                "f1": result["metrics"]["f1"],
                "roc_auc": new_roc_auc,
                "capture_rate": result["metrics"]["win_fast_capture_rate"],
                "fpr": result["metrics"]["false_positive_rate"],
                "train_from": _to_date(result["train_from"]),
                "train_to": _to_date(result["train_to"]),
                "model_path": f"db://ml_models/profile_{profile_id_str}_v{ver}",
                "threshold": float(result.get("decision_threshold", 0.5)),
                "notes": (
                    f"Profile model | profile_id={profile_id_str} | profile_name={profile_name} | "
                    f"MLflow run_id: {result['run_id']} | source=L3 | "
                    f"lookback_days={DAYS_LOOKBACK} | records={total} | "
                    f"roc_auc={new_roc_auc:.4f} | promotion_status={promotion_status}"
                ),
                "feature_columns_json": json.dumps(_trained_feature_cols),
                "feature_columns_hash": _feature_columns_hash,
                "feature_count": len(_trained_feature_cols),
                "feature_schema_version": _feature_schema_version,
                "dataset_query_cutoff": dataset_query_cutoff,
                "model_blob": model_blob,
                "profile_id": profile_id_str,
                "profile_version": profile_updated_at,
                "model_scope": "profile",
                "training_scope": "L3",
                "source_filter": "L3",
                "dataset_hash": dataset_hash,
                "query_hash": query_hash,
            })
            logger.info(
                "[ProfileTrainer] INSERT OK — profile_id=%s version=%s status=%s",
                profile_id_str, ver, promotion_status,
            )
    except Exception as exc:
        logger.error("[ProfileTrainer] DB save failed for profile %s: %s",
                     profile_id_str, exc, exc_info=True)
        raise

    logger.info(
        "[ProfileTrainer] Done: profile_id=%s version=%s roc_auc=%.4f status=%s",
        profile_id_str, ver, new_roc_auc, promotion_status,
    )


def main():
    logger.info("=== Scalpyn ML Trainer Job iniciado ===")
    logger.info(
        f"Config: days={DAYS_LOOKBACK} trials={N_TRIALS} min_records={MIN_RECORDS} "
        f"training_mode={TRAINING_MODE}"
    )

    engine = create_engine(DB_URL, pool_pre_ping=True)

    # Load ML config_profile first — needed for dataset validity gate (B4) and fee labels.
    # Must run BEFORE the shadow_trades query so ml_dataset_valid_from is available.
    _ml_cfg: dict = {}
    try:
        with engine.connect() as conn:
            ml_cfg_row = conn.execute(text("""
                SELECT config_json FROM config_profiles
                WHERE config_type = 'ml' AND is_active = true
                LIMIT 1
            """)).fetchone()
            if ml_cfg_row and ml_cfg_row[0]:
                _ml_cfg = (
                    ml_cfg_row[0] if isinstance(ml_cfg_row[0], dict)
                    else json.loads(ml_cfg_row[0])
                )
    except Exception as e:
        logger.warning("Failed to load ML config_profile — using legacy label: %s", e)

    _fee_roundtrip_pct = _ml_cfg.get("ml_fee_roundtrip_pct")
    _label_net_of_fees = bool(_ml_cfg.get("ml_label_net_of_fees", False))
    _win_fast_threshold_s = int(_ml_cfg.get("ml_win_fast_threshold_seconds", 1800))
    _promotion_min_auc = _cfg_float(_ml_cfg, "ml_promotion_min_auc", MIN_AUC_TO_SAVE)
    _promotion_min_precision_lift = _cfg_float(
        _ml_cfg, "ml_promotion_min_precision_lift_relative", 0.10
    )
    _promotion_max_fpr = _cfg_float(_ml_cfg, "ml_promotion_max_fpr", 0.20)
    _promotion_max_fpr_relative_increase = _cfg_float(
        _ml_cfg, "ml_promotion_max_fpr_relative_increase", 0.25
    )
    _promotion_min_precision_vs_champion = _cfg_float(
        _ml_cfg, "ml_promotion_min_precision_vs_champion", 1.00
    )
    _promotion_min_f1_vs_champion = _cfg_float(
        _ml_cfg, "ml_promotion_min_f1_vs_champion", 1.00
    )
    _promotion_require_all_directional_features = _cfg_bool(
        _ml_cfg, "ml_promotion_require_all_directional_features", True
    )
    # B4: dataset validity gate — exclude shadows where features_snapshot was empty (pre-fix).
    # Set via backend/sql/set_ml_dataset_valid_from.sql after B1 deploy. Only moves forward.
    _dataset_valid_from = _ml_cfg.get("ml_dataset_valid_from")  # ISO string or None
    # ZERO HARDCODE: propagate DB coverage threshold to trainer's env-var check.
    # trainer.py reads ML_MIN_FEATURE_COVERAGE; setting it here means the DB value
    # takes precedence over the Railway service env var.
    _min_cov_cfg = _ml_cfg.get("ml_feature_min_coverage_pct")
    if _min_cov_cfg is not None:
        os.environ["ML_MIN_FEATURE_COVERAGE"] = str(_min_cov_cfg)

    # ── Training mode dispatch ────────────────────────────────────────────────
    if TRAINING_MODE == "profile":
        assert PROFILE_ID, "PROFILE_ID env var required for TRAINING_MODE=profile"
        _train_for_profile(
            engine=engine,
            profile_id_str=PROFILE_ID,
            _ml_cfg=_ml_cfg,
            _fee_roundtrip_pct=_fee_roundtrip_pct,
            _label_net_of_fees=_label_net_of_fees,
            _win_fast_threshold_s=_win_fast_threshold_s,
            _promotion_min_auc=_promotion_min_auc,
            _promotion_min_precision_lift=_promotion_min_precision_lift,
            _promotion_max_fpr=_promotion_max_fpr,
            _promotion_max_fpr_relative_increase=_promotion_max_fpr_relative_increase,
            _promotion_min_precision_vs_champion=_promotion_min_precision_vs_champion,
            _promotion_min_f1_vs_champion=_promotion_min_f1_vs_champion,
            _promotion_require_all_directional_features=_promotion_require_all_directional_features,
        )
        logger.info("=== Trainer Job (profile mode) concluído ===")
        return

    elif TRAINING_MODE == "all_profiles":
        # Iterate all active profiles and train one model per profile serially.
        try:
            with engine.connect() as conn:
                profile_rows = conn.execute(text("""
                    SELECT id::text AS pid FROM profiles WHERE is_active = true ORDER BY id
                """)).fetchall()
        except Exception as e:
            logger.error("[AllProfiles] Failed to fetch profiles: %s", e)
            raise
        logger.info("[AllProfiles] Training for %d active profiles", len(profile_rows))
        for p_row in profile_rows:
            pid = p_row.pid
            try:
                _train_for_profile(
                    engine=engine,
                    profile_id_str=pid,
                    _ml_cfg=_ml_cfg,
                    _fee_roundtrip_pct=_fee_roundtrip_pct,
                    _label_net_of_fees=_label_net_of_fees,
                    _win_fast_threshold_s=_win_fast_threshold_s,
                    _promotion_min_auc=_promotion_min_auc,
                    _promotion_min_precision_lift=_promotion_min_precision_lift,
                    _promotion_max_fpr=_promotion_max_fpr,
                    _promotion_max_fpr_relative_increase=_promotion_max_fpr_relative_increase,
                    _promotion_min_precision_vs_champion=_promotion_min_precision_vs_champion,
                    _promotion_min_f1_vs_champion=_promotion_min_f1_vs_champion,
                    _promotion_require_all_directional_features=_promotion_require_all_directional_features,
                )
            except Exception as e:
                logger.error("[AllProfiles] Training failed for profile %s: %s", pid, e)
                # Continue with remaining profiles
        logger.info("=== Trainer Job (all_profiles mode) concluído ===")
        return

    # TRAINING_MODE == 'global' (default) — falls through to existing behavior below.
    logger.info("[GlobalTrainer] TRAINING_MODE=global — running standard global training")

    # ---------------------------------------------------------
    # 1. Extrai dados de shadow_trades (fonte canônica — Bloco B)
    #
    # Migrado de decisions_log (DISTINCT ON) para shadow_trades:
    # cada row = 1 trade simulado real (sem deduplicação necessária).
    # features_snapshot = indicadores flat no momento da entrada,
    # copiado de decisions_log.metrics["indicators_snapshot"] pelo
    # shadow_trade_service — mesma fonte, sem DISTINCT ON bottleneck.
    # ---------------------------------------------------------
    logger.info(
        "Extraindo dados de shadow_trades... (source=%s, days=%d, target=%s, exclude=%s→%s)",
        ML_SOURCE_FILTER,
        DAYS_LOOKBACK,
        ML_TARGET_TYPE,
        TRAIN_EXCLUDE_FROM or "none",
        TRAIN_EXCLUDE_TO or "none",
    )
    # Optional exclusion clause for periods with known bad features_snapshot.
    exclude_clause = ""
    exclude_params: dict = {}
    if TRAIN_EXCLUDE_FROM and TRAIN_EXCLUDE_TO:
        exclude_clause = (
            "AND NOT (created_at >= :excl_from AND created_at <= :excl_to)"
        )
        exclude_params = {
            "excl_from": TRAIN_EXCLUDE_FROM,
            "excl_to": f"{TRAIN_EXCLUDE_TO} 23:59:59",
        }
    # B4: ml_dataset_valid_from — exclude pre-fix L3 shadows where features_snapshot was empty.
    # Only applied for ML_SOURCE_FILTER='L3' (the source that had the snapshot bug).
    # For other sources (L1_SPECTRUM etc.) the features_snapshot::text <> '{}' filter
    # already guarantees quality — applying valid_from would only waste valid records.
    valid_from_clause = ""
    valid_from_params: dict = {}
    if _dataset_valid_from and ML_SOURCE_FILTER == "L3":
        valid_from_clause = "AND created_at >= :valid_from"
        valid_from_params = {"valid_from": _dataset_valid_from}
        logger.info("Dataset valid_from filter active (L3): created_at >= %s", _dataset_valid_from)
    elif _dataset_valid_from:
        logger.info("Dataset valid_from skipped for source=%s (snapshot quality via <> '{}' filter)", ML_SOURCE_FILTER)
    with engine.connect() as conn:
        dataset_query_cutoff = conn.execute(text("SELECT NOW()")).scalar()
        result = conn.execute(text(f"""
            SELECT
                symbol, source, pnl_pct, net_return_pct, holding_seconds, outcome,
                features_snapshot, created_at,
                ttt_outcome, ttt_fast_win_bucket,
                time_to_tp_minutes, elapsed_minutes, profit_velocity
            FROM shadow_trades
            WHERE source = :source_filter
              AND outcome IN ('TP_HIT', 'SL_HIT', 'TIMEOUT')
              AND pnl_pct IS NOT NULL
              AND features_snapshot IS NOT NULL
              AND features_snapshot::text <> '{{}}'
              AND created_at >= (:dataset_query_cutoff - CAST(:days AS interval))
              AND created_at <= :dataset_query_cutoff
              {exclude_clause}
              {valid_from_clause}
            ORDER BY created_at ASC
        """), {"days": f"{DAYS_LOOKBACK} days", "source_filter": ML_SOURCE_FILTER,
               "dataset_query_cutoff": dataset_query_cutoff,
               **exclude_params, **valid_from_params})
        records = [dict(row._mapping) for row in result.fetchall()]

    total = len(records)
    n_ttt      = sum(1 for r in records if r.get("ttt_outcome") is not None)
    n_fast_win = sum(1 for r in records if r.get("ttt_outcome") == "FAST_WIN")
    logger.info(
        "shadow_trades source=%s finalizados: %d | "
        "ttt_outcome set: %d/%d (%.1f%%) FAST_WIN=%d | win_fast_threshold=%ds",
        ML_SOURCE_FILTER, total,
        n_ttt, total, 100 * n_ttt / max(total, 1), n_fast_win,
        _win_fast_threshold_s,
    )

    if total < MIN_RECORDS:
        # Task #324 — exit 0 (não 1) durante acumulação de dataset. Dataset
        # insuficiente NÃO é falha de job — não dispara alerta de Cloud Run
        # Job failure enquanto o DB ainda acumula amostras pós-wipe.
        logger.info(
            f"[TRAINER] insufficient data — skipping run "
            f"({total} < {MIN_RECORDS})"
        )
        sys.exit(0)

    # ---------------------------------------------------------
    # 2. Build DataFrame
    # ---------------------------------------------------------
    sys.path.insert(0, "/app")
    from app.ml.feature_extractor import (
        FEATURE_COLUMNS,
        FEATURE_SCHEMA_VERSION,
        build_training_dataframe,
        feature_columns_hash,
        train_val_test_split,
    )
    from app.ml.trainer import WinFastTrainer

    df = build_training_dataframe(
        records,
        fee_roundtrip_pct=_fee_roundtrip_pct,
        label_net_of_fees=_label_net_of_fees,
        win_fast_threshold_s=_win_fast_threshold_s,
    )
    logger.info(
        "DataFrame: %d rows, %d cols | label_net_of_fees=%s fee=%.2f%%",
        len(df), len(df.columns),
        _label_net_of_fees, _fee_roundtrip_pct or 0.0,
    )

    win_fast_rate = df["is_win_fast"].mean() * 100
    logger.info(f"Taxa base WIN_FAST: {win_fast_rate:.1f}%")

    # ---------------------------------------------------------
    # 3. Treino XGBoost + Optuna
    # ---------------------------------------------------------
    logger.info(f"Iniciando Optuna ({N_TRIALS} trials)...")

    # Optuna runs in-memory — avoids alembic_version table collision between
    # Optuna's internal RDB schema and the backend's Alembic migrations.
    # The study cannot be resumed across runs, but jobs run ~5 min so this is
    # not a concern. Best model + metrics are persisted to GCS + ml_models.
    trainer = WinFastTrainer(n_trials=N_TRIALS)
    try:
        result = trainer.train(df, optuna_storage_url=None, ml_target=ML_TARGET_TYPE)
    except ValueError as exc:
        # Task #324 — degenerate dataset (single-class y_train or < min
        # samples per class). Exit 0: this is "still warming up", not a
        # failed run; we do not want Cloud Run Job failure alerts firing
        # while the post-wipe dataset accumulates.
        logger.info(f"[TRAINER] dataset degenerate — skipping: {exc}")
        sys.exit(0)

    logger.info(f"Treino concluído: {result['metrics']}")

    # ---------------------------------------------------------
    # 3b. Quality guards + Champion/Challenger
    #     Modelo NÃO é promovido se:
    #       - roc_auc < 0.50  (pior que aleatório — sinal invertido)
    #       - fpr >= 0.90     (aprova quase tudo — threshold colapsado)
    #       - roc_auc < 95% do modelo atual (regressão significativa)
    # ---------------------------------------------------------
    new_roc_auc = result["metrics"]["roc_auc"]
    new_fpr     = result["metrics"]["false_positive_rate"]
    new_precision = result["metrics"]["precision"]
    new_f1 = result["metrics"]["f1"]
    new_capture = result["metrics"]["win_fast_capture_rate"]
    winrate_base_ratio = _ratio(result.get("winrate_base"))
    min_precision_vs_base = winrate_base_ratio * (1.0 + _promotion_min_precision_lift)
    excluded_features = set(result.get("features_excluded") or [])
    excluded_directional = sorted(DIRECTIONAL_FEATURE_COLUMNS & excluded_features)
    promotion_status = "active"
    rejection_reason = None
    comparison_vs_previous = {
        "promotion_status": promotion_status,
        "rejection_reason": rejection_reason,
        "new_precision": new_precision,
        "new_f1": new_f1,
        "new_roc_auc": new_roc_auc,
        "new_false_positive_rate": new_fpr,
        "new_capture_rate": new_capture,
        "winrate_base": winrate_base_ratio,
        "min_precision_vs_base": min_precision_vs_base,
        "min_auc_to_save": _promotion_min_auc,
        "max_fpr_to_promote": _promotion_max_fpr,
        "max_fpr_relative_increase": _promotion_max_fpr_relative_increase,
        "excluded_directional_features": excluded_directional,
    }

    if new_roc_auc < _promotion_min_auc:
        promotion_status = "rejected"
        rejection_reason = "roc_auc_below_min"
        logger.warning(
            "[PROMOTION] REJEITADO — roc_auc=%.4f < %.2f (MIN_AUC_TO_SAVE). "
            "Dataset provavelmente pequeno demais ou regime invertido no test set. "
            "Modelo NÃO promovido.",
            new_roc_auc, _promotion_min_auc,
        )
        logger.warning("[PROMOTION] Challenger will be stored as rejected; active model unchanged.")

    if promotion_status == "active" and new_fpr >= 0.90:
        promotion_status = "rejected"
        rejection_reason = "fpr_too_high"
        logger.warning(
            "[PROMOTION] REJEITADO — fpr=%.4f >= 0.90 (modelo aprova quase tudo). "
            "Threshold provavelmente colapsado. Modelo NÃO promovido.",
            new_fpr,
        )
        logger.warning("[PROMOTION] Challenger will be stored as rejected; active model unchanged.")

    # Champion/Challenger: só regride até 5% vs modelo ativo atual.
    # Se não há modelo ativo, ou o atual também é ruim (< 0.50),
    # ignora a comparação relativa e promove com base nos guards absolutos.
    if promotion_status == "active" and new_precision < min_precision_vs_base:
        promotion_status = "rejected"
        rejection_reason = "precision_below_baseline_lift"
        logger.warning(
            "[PROMOTION] REJEITADO - precision=%.4f < baseline_lift=%.4f "
            "(winrate_base=%.4f, lift=%.2f). Modelo NAO promovido.",
            new_precision, min_precision_vs_base, winrate_base_ratio,
            _promotion_min_precision_lift,
        )
        logger.warning("[PROMOTION] Challenger will be stored as rejected; active model unchanged.")

    if promotion_status == "active" and new_fpr > _promotion_max_fpr:
        promotion_status = "rejected"
        rejection_reason = "fpr_above_max"
        logger.warning(
            "[PROMOTION] REJEITADO - fpr=%.4f > max_fpr=%.4f. Modelo NAO promovido.",
            new_fpr, _promotion_max_fpr,
        )
        logger.warning("[PROMOTION] Challenger will be stored as rejected; active model unchanged.")

    if (
        promotion_status == "active"
        and _promotion_require_all_directional_features
        and excluded_directional
    ):
        promotion_status = "rejected"
        rejection_reason = "directional_features_excluded"
        logger.warning(
            "[PROMOTION] REJEITADO - features direcionais excluidas por baixa cobertura: %s. "
            "Modelo registrado, mas NAO promovido.",
            excluded_directional,
        )
        logger.warning("[PROMOTION] Challenger will be stored as rejected; active model unchanged.")

    with engine.connect() as conn:
        current_row = conn.execute(text(
            """
            SELECT version, roc_auc, precision_score, f1_score,
                   false_positive_rate, win_fast_capture_rate
            FROM ml_models
            WHERE status = 'active'
              AND (model_scope = 'global' OR model_scope IS NULL OR profile_id IS NULL)
            ORDER BY version DESC
            LIMIT 1
            """
        )).fetchone()

    if promotion_status == "active" and current_row and current_row.roc_auc is not None and float(current_row.roc_auc) >= 0.50:
        current_roc_auc = float(current_row.roc_auc)
        current_precision = float(current_row.precision_score or 0.0)
        current_f1 = float(current_row.f1_score or 0.0)
        current_fpr = float(current_row.false_positive_rate or 0.0)
        current_capture = float(current_row.win_fast_capture_rate or 0.0)
        min_required    = round(current_roc_auc * 0.95, 4)
        min_required_precision = current_precision * _promotion_min_precision_vs_champion
        min_required_f1 = current_f1 * _promotion_min_f1_vs_champion
        max_allowed_fpr = (
            current_fpr * (1.0 + _promotion_max_fpr_relative_increase)
            if current_fpr > 0
            else _promotion_max_fpr
        )
        comparison_vs_previous.update({
            "current_version": str(current_row.version),
            "current_roc_auc": current_roc_auc,
            "current_precision": current_precision,
            "current_f1": current_f1,
            "current_false_positive_rate": current_fpr,
            "current_capture_rate": current_capture,
            "min_required_roc_auc": min_required,
            "min_required_precision": min_required_precision,
            "min_required_f1": min_required_f1,
            "max_allowed_fpr": max_allowed_fpr,
        })
        if new_roc_auc < min_required:
            promotion_status = "rejected"
            rejection_reason = "champion_challenger_regression"
            logger.warning(
                "[PROMOTION] REJEITADO — champion/challenger: "
                "new roc_auc=%.4f < min=%.4f (95%% do atual v%s=%.4f). "
                "Modelo NÃO promovido — mantendo campeão atual.",
                new_roc_auc, min_required, current_row.version, current_roc_auc,
            )
            logger.warning("[PROMOTION] Challenger will be stored as rejected; active model unchanged.")
        elif new_precision < min_required_precision:
            promotion_status = "rejected"
            rejection_reason = "champion_precision_regression"
            logger.warning(
                "[PROMOTION] REJEITADO - new precision=%.4f < champion_min=%.4f "
                "(atual v%s=%.4f). Modelo NAO promovido.",
                new_precision, min_required_precision, current_row.version,
                current_precision,
            )
            logger.warning("[PROMOTION] Challenger will be stored as rejected; active model unchanged.")
        elif new_f1 < min_required_f1:
            promotion_status = "rejected"
            rejection_reason = "champion_f1_regression"
            logger.warning(
                "[PROMOTION] REJEITADO - new f1=%.4f < champion_min=%.4f "
                "(atual v%s=%.4f). Modelo NAO promovido.",
                new_f1, min_required_f1, current_row.version, current_f1,
            )
            logger.warning("[PROMOTION] Challenger will be stored as rejected; active model unchanged.")
        elif new_fpr > max_allowed_fpr:
            promotion_status = "rejected"
            rejection_reason = "champion_fpr_regression"
            logger.warning(
                "[PROMOTION] REJEITADO - new fpr=%.4f > max_allowed=%.4f "
                "(atual v%s=%.4f). Modelo NAO promovido.",
                new_fpr, max_allowed_fpr, current_row.version, current_fpr,
            )
            logger.warning("[PROMOTION] Challenger will be stored as rejected; active model unchanged.")
        else:
            logger.info(
                "[PROMOTION] Champion/Challenger OK: new roc_auc=%.4f >= min=%.4f "
            "(atual v%s=%.4f) — prosseguindo com promoção.",
                new_roc_auc, min_required, current_row.version, current_roc_auc,
            )
    elif promotion_status == "active":
        logger.info(
            "[PROMOTION] Sem campeão válido para comparar — promoção via guards absolutos."
        )

    # ---------------------------------------------------------
    # 4. Serializa modelo em memória (salvo no DB na seção 5)
    # ---------------------------------------------------------
    comparison_vs_previous.update({
        "promotion_status": promotion_status,
        "rejection_reason": rejection_reason,
    })

    import joblib
    import io

    logger.info("Serializando modelo (joblib → bytes)...")
    buf = io.BytesIO()
    # Audit P0-15: serialize model with feature_columns and metadata
    # so that model_loader.py (which expects model_data["model"] and
    # model_data["feature_columns"]) can also load models saved by job.py.
    # Derive actual feature columns used: FEATURE_COLUMNS minus any excluded
    _trained_feature_cols = list(result.get("feature_columns") or [
        c for c in FEATURE_COLUMNS
        if c not in set(result.get("features_excluded", []))
    ])
    _feature_columns_hash = result.get("feature_columns_hash") or feature_columns_hash(_trained_feature_cols)
    _feature_schema_version = result.get("feature_schema_version") or FEATURE_SCHEMA_VERSION
    model_payload = {
        "model": trainer.model,
        "feature_columns": _trained_feature_cols,
        "metadata": {
            "trained_at": datetime.now(timezone.utc).isoformat(),
            "n_features": len(_trained_feature_cols),
            "target_type": ML_TARGET_TYPE,
            "dependency_versions": _ml_dependency_versions(),
            "feature_columns_hash": _feature_columns_hash,
            "feature_schema_version": _feature_schema_version,
            "dataset_query_cutoff": dataset_query_cutoff.isoformat()
            if hasattr(dataset_query_cutoff, "isoformat")
            else str(dataset_query_cutoff),
        },
    }
    joblib.dump(model_payload, buf)
    model_blob = buf.getvalue()
    logger.info(f"Modelo serializado: {len(model_blob) / 1024:.1f} KB")

    model_filename = f"win_fast_v{result.get('version', 'latest')}"
    gcs_model_uri = f"db://ml_models/{model_filename}"  # referência simbólica para nota no DB

    # ---------------------------------------------------------
    # 5. Persiste ml_models no Cloud SQL
    # ---------------------------------------------------------
    import datetime as _dt
    logger.info("Registrando modelo em ml_models...")

    def _to_date(v):
        if v is None:
            return None
        if hasattr(v, "date") and callable(v.date):
            return v.date()
        if isinstance(v, _dt.datetime):
            return v.date()
        return v

    try:
        client = mlflow.tracking.MlflowClient()
        client.set_tag(result["run_id"], "feature_columns_hash", _feature_columns_hash)
        client.set_tag(result["run_id"], "feature_schema_version", _feature_schema_version)
        client.set_tag(result["run_id"], "feature_count", str(len(_trained_feature_cols)))
        client.set_tag(
            result["run_id"],
            "dataset_query_cutoff",
            dataset_query_cutoff.isoformat()
            if hasattr(dataset_query_cutoff, "isoformat")
            else str(dataset_query_cutoff),
        )
        client.set_tag(
            result["run_id"],
            "mlflow.note.content",
            (
                f"feature_columns_hash={_feature_columns_hash}; "
                f"feature_count={len(_trained_feature_cols)}; "
                f"feature_schema_version={_feature_schema_version}; "
                f"dataset_query_cutoff={dataset_query_cutoff}; "
                f"promotion_status={promotion_status}; "
                f"rejection_reason={rejection_reason}"
            ),
        )
    except Exception as exc:
        logger.warning("Failed to annotate MLflow run with feature schema metadata: %s", exc)

    try:
        with engine.begin() as conn:
            # Próxima versão
            ver = conn.execute(
                text("SELECT COALESCE(MAX(version::integer), 0) + 1 FROM ml_models")
            ).scalar()
            logger.info("[DB] next_version=%s", ver)

            # Desativa anterior somente se o challenger foi aprovado.
            # Only retires global models — profile models are scoped separately.
            if promotion_status == "active":
                conn.execute(
                    text(
                        "UPDATE ml_models SET status = 'retired', retired_at = NOW() "
                        "WHERE status = 'active' "
                        "  AND (model_scope = 'global' OR model_scope IS NULL OR profile_id IS NULL)"
                    )
                )
                logger.info("[DB] UPDATE retired OK (global models only)")
            else:
                logger.info("[DB] challenger rejected - keeping current active model unchanged")

            # Insere novo — model_scope='global' for standard training
            conn.execute(text("""
                INSERT INTO ml_models (
                    version, status, hyperparams,
                    train_samples, val_samples, test_samples,
                    precision_score, recall_score, f1_score, roc_auc,
                    win_fast_capture_rate, false_positive_rate,
                    train_from, train_to,
                    model_path, decision_threshold,
                    activated_at, notes,
                    feature_columns_json, feature_columns_hash,
                    feature_count, feature_schema_version,
                    dataset_query_cutoff,
                    comparison_vs_previous,
                    model_blob,
                    model_scope, source_filter
                ) VALUES (
                    :version, :status, :hyperparams,
                    :n_train, :n_val, :n_test,
                    :precision, :recall, :f1, :roc_auc,
                    :capture_rate, :fpr,
                    :train_from, :train_to,
                    :model_path, :threshold,
                    CASE WHEN :status = 'active' THEN NOW() ELSE NULL END, :notes,
                    CAST(:feature_columns_json AS JSONB), :feature_columns_hash,
                    :feature_count, :feature_schema_version,
                    :dataset_query_cutoff,
                    CAST(:comparison_vs_previous AS JSONB),
                    :model_blob,
                    'global', :source_filter
                )
            """), {
                "version":      str(ver),
                "status":       promotion_status,
                "hyperparams":  json.dumps(
                    {k: (None if isinstance(v, float) and math.isnan(v) else v)
                     for k, v in result["best_params"].items()}
                ),
                "n_train":      result["n_train"],
                "n_val":        result["n_val"],
                "n_test":       result["n_test"],
                "precision":    result["metrics"]["precision"],
                "recall":       result["metrics"]["recall"],
                "f1":           result["metrics"]["f1"],
                "roc_auc":      result["metrics"]["roc_auc"],
                "capture_rate": result["metrics"]["win_fast_capture_rate"],
                "fpr":          result["metrics"]["false_positive_rate"],
                "train_from":   _to_date(result["train_from"]),
                "train_to":     _to_date(result["train_to"]),
                "model_path":   gcs_model_uri,
                # Task #324 — calibrated via PR curve on the test set (no more
                # hardcoded 0.500). See trainer._calibrate_threshold.
                "threshold":    float(result.get("decision_threshold", 0.5)),
                "notes":        (
                    f"MLflow run_id: {result['run_id']} | storage: db://ml_models | "
                    f"source={ML_SOURCE_FILTER} | target={ML_TARGET_TYPE} | "
                    f"lookback_days={DAYS_LOOKBACK} | "
                    f"dataset_query_cutoff={dataset_query_cutoff} | "
                    f"feature_schema_version={_feature_schema_version} | "
                    f"feature_columns_hash={_feature_columns_hash} | "
                    f"feature_count={len(_trained_feature_cols)} | "
                    f"candidate_feature_count={len(FEATURE_COLUMNS)} | "
                    f"candidate_feature_columns_hash={feature_columns_hash(list(FEATURE_COLUMNS))} | "
                    f"winrate_base={result.get('winrate_base', 0):.2f}% | "
                    f"n_pos={result.get('n_pos', 0)} n_neg={result.get('n_neg', 0)} | "
                    f"threshold={float(result.get('decision_threshold', 0.5)):.4f} | "
                    f"regime_drift={result.get('regime_drift_warning', False)} | "
                    f"promotion_status={promotion_status} | "
                    f"rejection_reason={rejection_reason} | "
                    f"features_excluded={result.get('features_excluded', [])} | "
                    f"shap_top5={result.get('shap_bad_approval_drivers', [])[:5]}"
                ),
                "feature_columns_json": json.dumps(_trained_feature_cols),
                "feature_columns_hash": _feature_columns_hash,
                "feature_count": len(_trained_feature_cols),
                "feature_schema_version": _feature_schema_version,
                "dataset_query_cutoff": dataset_query_cutoff,
                "comparison_vs_previous": json.dumps(comparison_vs_previous),
                "model_blob":   model_blob,
                "source_filter": ML_SOURCE_FILTER,
            })
            logger.info("[DB] INSERT OK - modelo salvo status=%s", promotion_status)
    except Exception as exc:
        logger.error("[DB] FALHA AO SALVAR MODELO: %s", exc, exc_info=True)
        raise

    if promotion_status == "active":
        logger.info(f"Modelo v{ver} registrado e ativado.")
    else:
        logger.info(f"Modelo v{ver} registrado como {promotion_status}; campeao atual preservado.")
    logger.info("=== Trainer Job concluído com sucesso ===")


if __name__ == "__main__":
    main()
