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

# MLflow aponta para Cloud SQL + GCS
os.environ["MLFLOW_TRACKING_URI"] = DB_URL
mlflow.set_tracking_uri(DB_URL)


def main():
    logger.info("=== Scalpyn ML Trainer Job iniciado ===")
    logger.info(f"Config: days={DAYS_LOOKBACK} trials={N_TRIALS} min_records={MIN_RECORDS}")

    engine = create_engine(DB_URL, pool_pre_ping=True)

    # ---------------------------------------------------------
    # 1. Extrai dados da decisions_log
    # ---------------------------------------------------------
    logger.info("Extraindo dados da decisions_log...")
    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT
                id, symbol, created_at,
                metrics, score,
                pnl_pct, holding_seconds, outcome
            FROM decisions_log
            WHERE l3_pass = true
              AND decision = 'ALLOW'
              AND outcome IS NOT NULL
              AND created_at >= NOW() - INTERVAL :days
            ORDER BY created_at ASC
        """), {"days": f"{DAYS_LOOKBACK} days"})
        records = [dict(row._mapping) for row in result.fetchall()]

    total = len(records)
    logger.info(f"Registros encontrados: {total}")

    if total < MIN_RECORDS:
        logger.error(f"Dados insuficientes: {total} < {MIN_RECORDS}. Job abortado.")
        sys.exit(1)

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

    # Optuna storage no mesmo Cloud SQL
    optuna_storage = DB_URL.replace("postgresql+psycopg2", "postgresql")

    trainer = WinFastTrainer(n_trials=N_TRIALS)
    result = trainer.train(df, optuna_storage_url=optuna_storage)

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
                :model_path, 0.500,
                NOW(), :notes
            )
        """), {
            "version":      ver,
            "hyperparams":  json.dumps(result["best_params"]),
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
            "notes":        f"MLflow run_id: {result['run_id']} | GCS: {gcs_model_uri}",
        })

    logger.info(f"Modelo v{ver} registrado e ativado.")
    logger.info("=== Trainer Job concluído com sucesso ===")


if __name__ == "__main__":
    main()
