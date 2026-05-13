#!/bin/bash
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
  --set-secrets="MLFLOW_TRACKING_URI=database-url:latest" \
  --set-env-vars="MLFLOW_ARTIFACT_ROOT=gs://scalpyn-mlflow/artifacts" \
  --no-allow-unauthenticated \
  --quiet

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
