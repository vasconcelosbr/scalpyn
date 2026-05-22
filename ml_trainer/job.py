import math
import os
import sys
import json
import logging
from datetime import datetime, timezone

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
DAYS_LOOKBACK     = int(os.getenv("DAYS_LOOKBACK", "90"))
N_TRIALS          = int(os.getenv("N_TRIALS", "50"))
MIN_RECORDS       = int(os.getenv("MIN_RECORDS", "200"))

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
    # 1. Extrai dados da decisions_log
    # ---------------------------------------------------------
    logger.info("Extraindo dados da decisions_log...")
    # Task #324 — DISTINCT ON (symbol, pnl_pct) dedupes the massive
    # NEW_SIGNAL/SIGNAL_EVOLVED/visibility-tick amplification (up to 88× per
    # unique trade in the 90d audit). Keep the earliest row per (symbol,
    # pnl_pct) so the outer ORDER BY preserves temporal split integrity.
    # pnl_pct IS NULL rows are dropped — we cannot label an unrealised trade.
    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT id, symbol, created_at, metrics, score,
                   pnl_pct, holding_seconds, outcome
            FROM (
                SELECT DISTINCT ON (symbol, pnl_pct)
                    id, symbol, created_at, metrics, score,
                    pnl_pct, holding_seconds, outcome
                FROM decisions_log
                WHERE l3_pass = true
                  AND decision = 'ALLOW'
                  AND outcome IN ('tp', 'sl')
                  AND pnl_pct IS NOT NULL
                  AND created_at >= NOW() - INTERVAL :days
                ORDER BY symbol, pnl_pct, created_at ASC
            ) AS deduped
            ORDER BY created_at ASC
        """), {"days": f"{DAYS_LOOKBACK} days"})
        records = [dict(row._mapping) for row in result.fetchall()]

    total = len(records)
    logger.info(f"Registros encontrados (pós-dedup): {total}")

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
        result = trainer.train(df, optuna_storage_url=None)
    except ValueError as exc:
        # Task #324 — degenerate dataset (single-class y_train or < min
        # samples per class). Exit 0: this is "still warming up", not a
        # failed run; we do not want Cloud Run Job failure alerts firing
        # while the post-wipe dataset accumulates.
        logger.info(f"[TRAINER] dataset degenerate — skipping: {exc}")
        sys.exit(0)

    logger.info(f"Treino concluído: {result['metrics']}")

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
                f"winrate_base={result.get('winrate_base', 0):.2f}% | "
                f"n_pos={result.get('n_pos', 0)} n_neg={result.get('n_neg', 0)} | "
                f"threshold={float(result.get('decision_threshold', 0.5)):.4f}"
            ),
        })

    logger.info(f"Modelo v{ver} registrado e ativado.")
    logger.info("=== Trainer Job concluído com sucesso ===")


if __name__ == "__main__":
    main()
