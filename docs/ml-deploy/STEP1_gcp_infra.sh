#!/bin/bash
# =============================================================
# STEP 1 — GCP Infrastructure Setup
# Scalpyn ML Layer — rodar UMA VEZ no terminal local
# Pré-requisito: gcloud auth login
# =============================================================

set -e

PROJECT_ID="clickrate-477217"
REGION="us-central1"
BUCKET_NAME="scalpyn-mlflow"

echo "=== Projeto: $PROJECT_ID ==="
echo "=== Região: $REGION ==="

# -------------------------------------------------------------
# 1. APIs necessárias
# -------------------------------------------------------------
echo ">>> Habilitando APIs..."
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  cloudscheduler.googleapis.com \
  storage.googleapis.com \
  secretmanager.googleapis.com \
  --quiet

# -------------------------------------------------------------
# 2. GCS Bucket para MLflow artifacts
# -------------------------------------------------------------
echo ">>> Criando bucket GCS: gs://$BUCKET_NAME..."
gsutil mb -l $REGION gs://$BUCKET_NAME 2>/dev/null || echo "Bucket já existe, continuando..."

gsutil -q cp /dev/null gs://$BUCKET_NAME/artifacts/.keep
gsutil -q cp /dev/null gs://$BUCKET_NAME/models/.keep

echo ">>> Bucket criado: gs://$BUCKET_NAME"

# -------------------------------------------------------------
# 3. Service Account para os serviços ML
# -------------------------------------------------------------
echo ">>> Criando Service Account..."
SA_NAME="scalpyn-ml"
SA_EMAIL="$SA_NAME@$PROJECT_ID.iam.gserviceaccount.com"

gcloud iam service-accounts create $SA_NAME \
  --display-name="Scalpyn ML Service Account" \
  --quiet 2>/dev/null || echo "SA já existe, continuando..."

# Permissões no projeto
for ROLE in \
  "roles/storage.objectAdmin" \
  "roles/cloudsql.client" \
  "roles/run.invoker" \
  "roles/secretmanager.secretAccessor"; do
  gcloud projects add-iam-policy-binding $PROJECT_ID \
    --member="serviceAccount:$SA_EMAIL" \
    --role="$ROLE" \
    --quiet
done

echo ">>> Service Account configurada: $SA_EMAIL"

# -------------------------------------------------------------
# 4. Secrets — usando os existentes (database-url, redis-url)
# Nenhum secret novo criado — apenas validação e permissões
# -------------------------------------------------------------
echo ">>> Validando secrets existentes..."

for SECRET in "database-url" "redis-url"; do
  gcloud secrets versions access latest --secret=$SECRET &>/dev/null && \
    echo "✅ $SECRET — OK" || \
    echo "❌ $SECRET — NÃO ENCONTRADO — verifique o Secret Manager"
done

echo ">>> Concedendo acesso da Service Account aos secrets existentes..."
for SECRET in "database-url" "redis-url" "encryption-key" "jwt-secret" "ai-keys-encryption-key"; do
  gcloud secrets add-iam-policy-binding $SECRET \
    --member="serviceAccount:$SA_EMAIL" \
    --role="roles/secretmanager.secretAccessor" \
    --quiet 2>/dev/null || echo "Permissão em $SECRET já configurada"
done

# -------------------------------------------------------------
# 5. Cloud Scheduler configurado no STEP 4
# -------------------------------------------------------------
echo ""
echo "=== STEP 1 CONCLUÍDO ==="
echo "Bucket: gs://$BUCKET_NAME"
echo "SA: $SA_EMAIL"
echo "Secrets: usando database-url e redis-url existentes"
