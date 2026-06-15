import math
import os
import sys
import json
import logging
from datetime import datetime, timezone

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


def main():
    logger.info("=== Scalpyn ML Trainer Job iniciado ===")
    logger.info(f"Config: days={DAYS_LOOKBACK} trials={N_TRIALS} min_records={MIN_RECORDS}")

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
    # B4: dataset validity gate — exclude shadows where features_snapshot was empty (pre-fix).
    # Set via backend/sql/set_ml_dataset_valid_from.sql after B1 deploy. Only moves forward.
    _dataset_valid_from = _ml_cfg.get("ml_dataset_valid_from")  # ISO string or None
    # ZERO HARDCODE: propagate DB coverage threshold to trainer's env-var check.
    # trainer.py reads ML_MIN_FEATURE_COVERAGE; setting it here means the DB value
    # takes precedence over the Railway service env var.
    _min_cov_cfg = _ml_cfg.get("ml_feature_min_coverage_pct")
    if _min_cov_cfg is not None:
        os.environ["ML_MIN_FEATURE_COVERAGE"] = str(_min_cov_cfg)

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
              AND created_at >= NOW() - INTERVAL :days
              {exclude_clause}
              {valid_from_clause}
            ORDER BY created_at ASC
        """), {"days": f"{DAYS_LOOKBACK} days", "source_filter": ML_SOURCE_FILTER,
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
    from app.ml.feature_extractor import build_training_dataframe, train_val_test_split, FEATURE_COLUMNS
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
    # 3b. Fetch previous active model metrics for comparison.
    #     No promotion guards — new version is always activated.
    # ---------------------------------------------------------
    with engine.connect() as conn:
        prev_row = conn.execute(text("""
            SELECT version, f1_score, roc_auc, precision_score, recall_score,
                   win_fast_capture_rate, false_positive_rate, ev_score
            FROM ml_models WHERE status = 'active'
            ORDER BY version DESC LIMIT 1
        """)).fetchone()

    if prev_row:
        logger.info(
            "[COMPARISON] Versão anterior v%s: f1=%.4f auc=%.4f precision=%.4f "
            "recall=%.4f ev=%.4f",
            prev_row.version, prev_row.f1_score or 0, prev_row.roc_auc or 0,
            prev_row.precision_score or 0, prev_row.recall_score or 0,
            prev_row.ev_score or 0,
        )

    # ---------------------------------------------------------
    # 4. Serializa modelo em memória (salvo no DB na seção 5)
    # ---------------------------------------------------------
    import datetime as _dt
    import joblib
    import io

    logger.info("Serializando modelo (joblib → bytes)...")
    buf = io.BytesIO()
    # Audit P0-15: serialize model with feature_columns and metadata
    # so that model_loader.py (which expects model_data["model"] and
    # model_data["feature_columns"]) can also load models saved by job.py.
    # Derive actual feature columns used: FEATURE_COLUMNS minus any excluded
    _trained_feature_cols = [
        c for c in FEATURE_COLUMNS
        if c not in set(result.get("features_excluded", []))
    ]
    model_payload = {
        "model": trainer.model,
        "feature_columns": _trained_feature_cols,
        "metadata": {
            "trained_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "n_features": len(_trained_feature_cols),
            "target_type": ML_TARGET_TYPE,
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
    logger.info("Registrando modelo em ml_models...")

    def _to_date(v):
        if v is None:
            return None
        if hasattr(v, "date") and callable(v.date):
            return v.date()
        if isinstance(v, _dt.datetime):
            return v.date()
        return v

    # Compute comparison_vs_previous block
    _comparison: dict | None = None
    if prev_row:
        _new_m = result["metrics"]
        _prev = {
            "f1":           float(prev_row.f1_score or 0),
            "roc_auc":      float(prev_row.roc_auc or 0),
            "precision":    float(prev_row.precision_score or 0),
            "recall":       float(prev_row.recall_score or 0),
            "ev":           float(prev_row.ev_score or 0),
            "capture_rate": float(prev_row.win_fast_capture_rate or 0),
            "fpr":          float(prev_row.false_positive_rate or 0),
        }
        _new = {
            "f1":           _new_m["f1"],
            "roc_auc":      _new_m["roc_auc"],
            "precision":    _new_m["precision"],
            "recall":       _new_m["recall"],
            "ev":           _new_m.get("ev", 0.0),
            "capture_rate": _new_m["win_fast_capture_rate"],
            "fpr":          _new_m["false_positive_rate"],
        }
        # fpr: lower is better
        _lower_is_better = {"fpr"}
        _deltas, _improved = {}, {}
        for key in _new:
            delta = round(_new[key] - _prev[key], 6)
            _deltas[key] = delta
            _improved[key] = (delta < 0) if key in _lower_is_better else (delta > 0)
        _core = ["f1", "roc_auc", "precision", "recall", "ev"]
        _comparison = {
            "previous_version":    str(prev_row.version),
            "deltas":              _deltas,
            "improved":            _improved,
            "all_metrics_improved": all(_improved[k] for k in _core),
        }
        logger.info(
            "[COMPARISON] all_improved=%s deltas=%s",
            _comparison["all_metrics_improved"], _deltas,
        )

    try:
        with engine.begin() as conn:
            # Próxima versão
            ver = conn.execute(
                text("SELECT COALESCE(MAX(version::integer), 0) + 1 FROM ml_models")
            ).scalar()
            logger.info("[DB] next_version=%s", ver)

            # Desativa anterior
            conn.execute(
                text("UPDATE ml_models SET status = 'retired', retired_at = NOW() WHERE status = 'active'")
            )
            logger.info("[DB] UPDATE retired OK")

            # Insere novo
            conn.execute(text("""
                INSERT INTO ml_models (
                    version, status, hyperparams,
                    train_samples, val_samples, test_samples,
                    precision_score, recall_score, f1_score, roc_auc,
                    win_fast_capture_rate, false_positive_rate,
                    train_from, train_to,
                    model_path, decision_threshold,
                    activated_at, notes,
                    model_blob, ev_score,
                    comparison_vs_previous
                ) VALUES (
                    :version, 'active', :hyperparams,
                    :n_train, :n_val, :n_test,
                    :precision, :recall, :f1, :roc_auc,
                    :capture_rate, :fpr,
                    :train_from, :train_to,
                    :model_path, :threshold,
                    NOW(), :notes,
                    :model_blob, :ev_score,
                    :comparison
                )
            """), {
                "version":      str(ver),
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
                    f"winrate_base={result.get('winrate_base', 0):.2f}% | "
                    f"n_pos={result.get('n_pos', 0)} n_neg={result.get('n_neg', 0)} | "
                    f"threshold={float(result.get('decision_threshold', 0.5)):.4f} | "
                    f"regime_drift={result.get('regime_drift_warning', False)} | "
                    f"features_excluded={result.get('features_excluded', [])} | "
                    f"shap_top5={result.get('shap_bad_approval_drivers', [])[:5]}"
                ),
                "model_blob":   model_blob,
                "ev_score":     result["metrics"].get("ev", 0.0),
                "comparison":   json.dumps(_comparison) if _comparison else None,
            })
            logger.info("[DB] INSERT OK — modelo salvo")
    except Exception as exc:
        logger.error("[DB] FALHA AO SALVAR MODELO: %s", exc, exc_info=True)
        raise

    logger.info(f"Modelo v{ver} registrado e ativado.")
    logger.info("=== Trainer Job concluído com sucesso ===")


if __name__ == "__main__":
    main()
