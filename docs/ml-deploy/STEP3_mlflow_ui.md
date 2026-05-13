# =============================================================
# STEP 3 — MLflow UI no Cloud Run
# Serviço separado — interface web para experimentos
# =============================================================

# -------------------------------------------------------------
# ARQUIVO: mlflow_server/Dockerfile
# -------------------------------------------------------------

FROM python:3.11-slim

RUN pip install --no-cache-dir \
    mlflow==2.13.0 \
    psycopg2-binary==2.9.9 \
    google-cloud-storage==2.17.0 \
    gunicorn==22.0.0

# MLflow server expõe na porta 8080 (padrão Cloud Run)
ENV PORT=8080

CMD mlflow server \
    --backend-store-uri $MLFLOW_TRACKING_URI \
    --default-artifact-root $MLFLOW_ARTIFACT_ROOT \
    --host 0.0.0.0 \
    --port $PORT \
    --workers 2

# -------------------------------------------------------------
# Deploy do MLflow UI no Cloud Run
# -------------------------------------------------------------

#!/bin/bash
# mlflow_server/deploy_mlflow_ui.sh

set -e

PROJECT_ID=$(gcloud config get-value project)
REGION="us-central1"
SERVICE_NAME="scalpyn-mlflow-ui"
IMAGE="gcr.io/$PROJECT_ID/$SERVICE_NAME"
SA_EMAIL="scalpyn-ml@$PROJECT_ID.iam.gserviceaccount.com"

echo ">>> Build da imagem MLflow UI..."
gcloud builds submit ./mlflow_server \
  --tag $IMAGE \
  --quiet

echo ">>> Deploy MLflow UI no Cloud Run..."
gcloud run deploy $SERVICE_NAME \
  --image $IMAGE \
  --region $REGION \
  --platform managed \
  --service-account $SA_EMAIL \
  --memory 1Gi \
  --cpu 1 \
  --min-instances 0 \
  --max-instances 1 \
  --timeout 300 \
  --set-secrets="MLFLOW_TRACKING_URI=SCALPYN_DB_URL:latest" \
  --set-env-vars="MLFLOW_ARTIFACT_ROOT=gs://scalpyn-mlflow/artifacts" \
  --no-allow-unauthenticated \
  --quiet

# URL do serviço
MLFLOW_URL=$(gcloud run services describe $SERVICE_NAME \
  --region $REGION \
  --format "value(status.url)")

echo ""
echo "=== MLflow UI deployado ==="
echo "URL: $MLFLOW_URL"
echo ""
echo "Acesso restrito (--no-allow-unauthenticated)"
echo "Para acessar localmente via proxy:"
echo ""
echo "  gcloud run services proxy $SERVICE_NAME --region $REGION --port 8888"
echo "  Acesse: http://localhost:8888"
echo ""
echo "Ou adicione seu email como IAM invoker:"
echo "  gcloud run services add-iam-policy-binding $SERVICE_NAME \\"
echo "    --region $REGION \\"
echo "    --member='user:SEU_EMAIL@gmail.com' \\"
echo "    --role='roles/run.invoker'"
