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
from google.cloud import storage

# Configuração de logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("scalpyn.trainer")

# -------------------------------------------------------------
# Config via env vars (injetadas pelo Cloud Run Job)
# -------------------------------------------------------------
DB_URL            = os.environ["DB_URL"]          # Cloud SQL via unix socket
BUCKET_NAME       = os.environ["BUCKET_NAME"]     # scalpyn-mlflow
GCS_ARTIFACT_ROOT = f"gs://{BUCKET_NAME}/artifacts"
# BLOCO C — janela deslizante 30d (regime drift).
# Substituiu 90d fixos — ajustar via env se necessário.
DAYS_LOOKBACK            = int(os.getenv("DAYS_LOOKBACK", "30"))
N_TRIALS                 = int(os.getenv("N_TRIALS", "50"))
MIN_RECORDS              = int(os.getenv("MIN_RECORDS", "200"))
# BLOCO C — source filter agnóstico.
# 'L3' = comportamento atual (fallback seguro).
# 'WATCHLIST_SPOT' = espectro completo (ativar apenas após dataset acumular).
# Controlado pelo operador via env var no Cloud Run Job — ZERO HARDCODE.
ML_SOURCE_FILTER         = os.getenv("ML_SOURCE_FILTER", "L3")
# BLOCO C — alvo agnóstico (binary | regression).
# Decisão adiada para após teste de separabilidade do espectro completo.
ML_TARGET_TYPE           = os.getenv("ML_TARGET_TYPE", "binary")
# Optional: exclude a date range with known bad indicators from training.
# Set TRAIN_EXCLUDE_FROM=YYYY-MM-DD and TRAIN_EXCLUDE_TO=YYYY-MM-DD to skip
# a period where features_snapshot contained absent/miscalculated indicators.
TRAIN_EXCLUDE_FROM       = os.getenv("TRAIN_EXCLUDE_FROM", "")   # e.g. "2026-05-01"
TRAIN_EXCLUDE_TO         = os.getenv("TRAIN_EXCLUDE_TO", "")     # e.g. "2026-05-20"

# MLflow aponta para o serviço scalpyn-mlflow-ui (Cloud Run) que mantém
# seu próprio banco — sem colisão com o alembic_version do backend.
_MLFLOW_URI = os.getenv(
    "MLFLOW_TRACKING_URI",
    "https://scalpyn-mlflow-ui-wm56dfqgta-uc.a.run.app",
)
os.environ["MLFLOW_TRACKING_URI"] = _MLFLOW_URI
mlflow.set_tracking_uri(_MLFLOW_URI)


def main():
    logger.info("=== Scalpyn ML Trainer Job iniciado ===")
    logger.info(f"Config: days={DAYS_LOOKBACK} trials={N_TRIALS} min_records={MIN_RECORDS}")

    engine = create_engine(DB_URL, pool_pre_ping=True)

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
    with engine.connect() as conn:
        result = conn.execute(text(f"""
            SELECT
                symbol, source, pnl_pct, holding_seconds, outcome,
                features_snapshot, created_at,
                ttt_outcome, ttt_fast_win_bucket,
                time_to_tp_minutes, elapsed_minutes, profit_velocity
            FROM shadow_trades
            WHERE source = :source_filter
              AND outcome IN ('TP_HIT', 'SL_HIT', 'TIMEOUT')
              AND pnl_pct IS NOT NULL
              AND features_snapshot IS NOT NULL
              AND created_at >= NOW() - INTERVAL :days
              {exclude_clause}
            ORDER BY created_at ASC
        """), {"days": f"{DAYS_LOOKBACK} days", "source_filter": ML_SOURCE_FILTER, **exclude_params})
        records = [dict(row._mapping) for row in result.fetchall()]

    total = len(records)
    n_ttt      = sum(1 for r in records if r.get("ttt_outcome") is not None)
    n_fast_win = sum(1 for r in records if r.get("ttt_outcome") == "FAST_WIN")
    logger.info(
        "shadow_trades L3 finalizados: %d | "
        "TTT labels: %d/%d (%.1f%%) FAST_WIN=%d",
        total,
        n_ttt, total, 100 * n_ttt / max(total, 1), n_fast_win,
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
    from app.ml.feature_extractor import build_training_dataframe, train_val_test_split
    from app.ml.trainer import WinFastTrainer

    df = build_training_dataframe(records)
    logger.info(f"DataFrame: {len(df)} rows, {len(df.columns)} cols")

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

    if new_roc_auc < 0.50:
        logger.warning(
            "[PROMOTION] REJEITADO — roc_auc=%.4f < 0.50 (pior que aleatório). "
            "Dataset provavelmente pequeno demais ou regime invertido no test set. "
            "Modelo NÃO promovido.",
            new_roc_auc,
        )
        sys.exit(0)

    if new_fpr >= 0.90:
        logger.warning(
            "[PROMOTION] REJEITADO — fpr=%.4f >= 0.90 (modelo aprova quase tudo). "
            "Threshold provavelmente colapsado. Modelo NÃO promovido.",
            new_fpr,
        )
        sys.exit(0)

    # Champion/Challenger: só regride até 5% vs modelo ativo atual.
    # Se não há modelo ativo, ou o atual também é ruim (< 0.50),
    # ignora a comparação relativa e promove com base nos guards absolutos.
    with engine.connect() as conn:
        current_row = conn.execute(text(
            "SELECT version, roc_auc FROM ml_models WHERE status = 'active' "
            "ORDER BY version DESC LIMIT 1"
        )).fetchone()

    if current_row and current_row.roc_auc is not None and float(current_row.roc_auc) >= 0.50:
        current_roc_auc = float(current_row.roc_auc)
        min_required    = round(current_roc_auc * 0.95, 4)
        if new_roc_auc < min_required:
            logger.warning(
                "[PROMOTION] REJEITADO — champion/challenger: "
                "new roc_auc=%.4f < min=%.4f (95%% do atual v%s=%.4f). "
                "Modelo NÃO promovido — mantendo campeão atual.",
                new_roc_auc, min_required, current_row.version, current_roc_auc,
            )
            sys.exit(0)
        logger.info(
            "[PROMOTION] Champion/Challenger OK: new roc_auc=%.4f >= min=%.4f "
            "(atual v%s=%.4f) — prosseguindo com promoção.",
            new_roc_auc, min_required, current_row.version, current_roc_auc,
        )
    else:
        logger.info(
            "[PROMOTION] Sem campeão válido para comparar — promoção via guards absolutos."
        )

    # ---------------------------------------------------------
    # 4. Salva modelo serializado no GCS
    # ---------------------------------------------------------
    logger.info("Salvando modelo no GCS...")
    import joblib
    import tempfile

    model_filename = f"win_fast_v{result.get('version', 'latest')}.pkl"
    gcs_path = f"models/{model_filename}"

    with tempfile.NamedTemporaryFile(suffix=".pkl", delete=False) as tmp:
        joblib.dump(trainer.model, tmp.name)
        tmp_path = tmp.name

    gcs_client = storage.Client()
    bucket = gcs_client.bucket(BUCKET_NAME)
    blob = bucket.blob(gcs_path)
    blob.upload_from_filename(tmp_path)
    os.unlink(tmp_path)

    # Também salva como "latest" para o predictor carregar sempre o mais novo
    latest_blob = bucket.blob("models/win_fast_latest.pkl")
    bucket.copy_blob(blob, bucket, "models/win_fast_latest.pkl")

    gcs_model_uri = f"gs://{BUCKET_NAME}/{gcs_path}"
    logger.info(f"Modelo salvo: {gcs_model_uri}")
    logger.info(f"Latest atualizado: gs://{BUCKET_NAME}/models/win_fast_latest.pkl")

    # ---------------------------------------------------------
    # 5. Persiste ml_models no Cloud SQL
    # ---------------------------------------------------------
    logger.info("Registrando modelo em ml_models...")
    with engine.begin() as conn:
        # Próxima versão
        ver = conn.execute(
            text("SELECT COALESCE(MAX(version), 0) + 1 FROM ml_models")
        ).scalar()

        # Desativa anterior
        conn.execute(
            text("UPDATE ml_models SET status = 'retired', retired_at = NOW() WHERE status = 'active'")
        )

        # Insere novo
        conn.execute(text("""
            INSERT INTO ml_models (
                version, status, hyperparams,
                train_samples, val_samples, test_samples,
                precision_score, recall_score, f1_score, roc_auc,
                win_fast_capture_rate, false_positive_rate,
                train_from, train_to,
                model_path, decision_threshold,
                activated_at, notes
            ) VALUES (
                :version, 'active', :hyperparams,
                :n_train, :n_val, :n_test,
                :precision, :recall, :f1, :roc_auc,
                :capture_rate, :fpr,
                :train_from, :train_to,
                :model_path, :threshold,
                NOW(), :notes
            )
        """), {
            "version":      ver,
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
            "train_from":   result["train_from"],
            "train_to":     result["train_to"],
            "model_path":   gcs_model_uri,
            # Task #324 — calibrated via PR curve on the test set (no more
            # hardcoded 0.500). See trainer._calibrate_threshold.
            "threshold":    float(result.get("decision_threshold", 0.5)),
            "notes":        (
                f"MLflow run_id: {result['run_id']} | GCS: {gcs_model_uri} | "
                f"source={ML_SOURCE_FILTER} | target={ML_TARGET_TYPE} | "
                f"lookback_days={DAYS_LOOKBACK} | "
                f"winrate_base={result.get('winrate_base', 0):.2f}% | "
                f"n_pos={result.get('n_pos', 0)} n_neg={result.get('n_neg', 0)} | "
                f"threshold={float(result.get('decision_threshold', 0.5)):.4f} | "
                f"regime_drift={result.get('regime_drift_warning', False)} | "
                f"features_excluded={result.get('features_excluded', [])} | "
                f"shap_top5={result.get('shap_bad_approval_drivers', [])[:5]}"
            ),
        })

    logger.info(f"Modelo v{ver} registrado e ativado.")
    logger.info("=== Trainer Job concluído com sucesso ===")


if __name__ == "__main__":
    main()
