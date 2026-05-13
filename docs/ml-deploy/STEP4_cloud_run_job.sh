#!/bin/bash
# =============================================================
# STEP 4 — Cloud Run Job + Cloud Scheduler
# Treino automático semanal
# =============================================================

set -e

PROJECT_ID="clickrate-477217"
REGION="us-central1"
JOB_NAME="scalpyn-ml-trainer"
IMAGE="gcr.io/$PROJECT_ID/$JOB_NAME"
SA_EMAIL="scalpyn-ml@$PROJECT_ID.iam.gserviceaccount.com"
CLOUD_SQL_INSTANCE="clickrate-477217:us-central1:scalpyndata"

# -------------------------------------------------------------
# 1. Build da imagem do trainer
# -------------------------------------------------------------
echo ">>> Build da imagem Trainer Job..."
gcloud builds submit . \
  --tag $IMAGE \
  --file ml_trainer/Dockerfile \
  --quiet

# -------------------------------------------------------------
# 2. Cria Cloud Run Job
# -------------------------------------------------------------
echo ">>> Criando Cloud Run Job..."
gcloud run jobs create $JOB_NAME \
  --image $IMAGE \
  --region $REGION \
  --service-account $SA_EMAIL \
  --memory 4Gi \
  --cpu 2 \
  --task-timeout 3600 \
  --max-retries 1 \
  --set-cloudsql-instances $CLOUD_SQL_INSTANCE \
  --set-secrets="DB_URL=database-url:latest" \
  --set-env-vars="\
BUCKET_NAME=scalpyn-mlflow,\
DAYS_LOOKBACK=90,\
N_TRIALS=50,\
MIN_RECORDS=200,\
MLFLOW_ARTIFACT_ROOT=gs://scalpyn-mlflow/artifacts" \
  --quiet 2>/dev/null || \
gcloud run jobs update $JOB_NAME \
  --image $IMAGE \
  --region $REGION \
  --service-account $SA_EMAIL \
  --memory 4Gi \
  --cpu 2 \
  --task-timeout 3600 \
  --max-retries 1 \
  --set-cloudsql-instances $CLOUD_SQL_INSTANCE \
  --set-secrets="DB_URL=database-url:latest" \
  --set-env-vars="\
BUCKET_NAME=scalpyn-mlflow,\
DAYS_LOOKBACK=90,\
N_TRIALS=50,\
MIN_RECORDS=200,\
MLFLOW_ARTIFACT_ROOT=gs://scalpyn-mlflow/artifacts" \
  --quiet

echo ">>> Cloud Run Job criado: $JOB_NAME"

# -------------------------------------------------------------
# 3. Cloud Scheduler — domingo 02:00 UTC
# -------------------------------------------------------------
echo ">>> Configurando Cloud Scheduler..."

# Service Account do Scheduler para invocar o Job
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:$SA_EMAIL" \
  --role="roles/run.jobsExecutor" \
  --quiet

gcloud scheduler jobs create http "scalpyn-ml-weekly-train" \
  --location $REGION \
  --schedule "0 2 * * 0" \
  --uri "https://$REGION-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/$PROJECT_ID/jobs/$JOB_NAME:run" \
  --http-method POST \
  --oauth-service-account-email $SA_EMAIL \
  --time-zone "UTC" \
  --attempt-deadline "3600s" \
  --description "Scalpyn ML — treino semanal XGBoost (domingo 02:00 UTC)" \
  --quiet 2>/dev/null || \
gcloud scheduler jobs update http "scalpyn-ml-weekly-train" \
  --location $REGION \
  --schedule "0 2 * * 0" \
  --quiet

echo ""
echo "=== Cloud Scheduler configurado ==="
echo "Job: $JOB_NAME"
echo "Schedule: domingo 02:00 UTC (toda semana)"
echo ""
echo "Para executar AGORA (teste manual):"
echo "  gcloud run jobs execute $JOB_NAME --region $REGION --wait"
echo ""
echo "Para ver logs do último job:"
echo "  gcloud run jobs executions list --job $JOB_NAME --region $REGION"
echo "  gcloud beta run jobs executions logs --job $JOB_NAME --region $REGION"
