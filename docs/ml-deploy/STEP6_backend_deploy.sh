#!/bin/bash
# =============================================================
# STEP 6 — Deploy do Backend Principal (Cloud Run)
# Adiciona variáveis ML ao deploy existente
# NÃO altera o processo de deploy atual — apenas env vars
# =============================================================

set -e

PROJECT_ID="clickrate-477217"
REGION="us-central1"
BACKEND_SERVICE="scalpyn"
SA_EMAIL="scalpyn-ml@$PROJECT_ID.iam.gserviceaccount.com"
CLOUD_SQL_INSTANCE="clickrate-477217:us-central1:scalpyndata"

# O seu processo atual de deploy via main.py continua igual.
# Apenas adicionamos as variáveis de ambiente ML:

echo ">>> Atualizando env vars ML no backend..."
gcloud run services update $BACKEND_SERVICE \
  --region $REGION \
  --set-env-vars="\
BUCKET_NAME=scalpyn-mlflow,\
MLFLOW_ARTIFACT_ROOT=gs://scalpyn-mlflow/artifacts" \
  --quiet

echo ">>> Backend atualizado com vars ML"
echo ""

# =============================================================
# STEP 7 — requirements.txt do backend (adições ML)
# =============================================================
# Adicionar ao backend/requirements.txt existente:
#
# # ML Prediction Layer (adicionado)
# xgboost==2.1.1
# scikit-learn==1.5.0
# joblib==1.4.2
# google-cloud-storage==2.17.0
# pandas==2.2.2
# numpy==1.26.4
#
# NÃO adicionar ao backend principal:
# - optuna (só no trainer job)
# - mlflow completo (só no trainer job e mlflow-ui)
# O backend principal usa apenas mlflow-skinny para logging mínimo:
# mlflow-skinny==2.13.0

echo ">>> Adicionar ao backend/requirements.txt:"
echo ""
cat << 'EOF'
# ML Prediction Layer
xgboost==2.1.1
scikit-learn==1.5.0
joblib==1.4.2
google-cloud-storage==2.17.0
pandas==2.2.2
numpy==1.26.4
mlflow-skinny==2.13.0
EOF

echo ""

# =============================================================
# STEP 8 — Estrutura final de diretórios no repositório
# =============================================================

cat << 'EOF'
Estrutura de arquivos a criar no repositório Scalpyn:

scalpyn/
├── backend/                          (existente)
│   ├── app/
│   │   ├── ml/                       (NOVO — prediction layer)
│   │   │   ├── __init__.py
│   │   │   ├── gcs_model_loader.py   (STEP 5)
│   │   │   ├── feature_extractor.py  (STEP 2 do deploy anterior)
│   │   │   ├── prediction_service.py (STEP 5)
│   │   │   └── trainer.py            (não deployado aqui — só no job)
│   │   ├── api/
│   │   │   └── ml.py                 (NOVO — endpoints)
│   │   └── main.py                   (1 linha adicionada: include_router)
│   ├── requirements.txt              (adições ML acima)
│   └── Dockerfile                    (sem alteração)
│
├── ml_trainer/                       (NOVO — Cloud Run Job)
│   ├── Dockerfile
│   ├── requirements_trainer.txt
│   └── job.py
│
└── mlflow_server/                    (NOVO — MLflow UI)
    └── Dockerfile
EOF

echo ""

# =============================================================
# STEP 9 — Verificação completa do ambiente GCP
# =============================================================

echo ">>> Verificação do ambiente..."
echo ""

PROJECT_ID=$(gcloud config get-value project)
REGION="us-central1"

echo "=== GCS Bucket ==="
gsutil ls gs://scalpyn-mlflow/ 2>/dev/null && echo "✅ OK" || echo "❌ Não encontrado"

echo ""
echo "=== Cloud Run Services ==="
gcloud run services list --region $REGION --format="table(metadata.name,status.url)" 2>/dev/null

echo ""
echo "=== Cloud Run Jobs ==="
gcloud run jobs list --region $REGION --format="table(metadata.name,status.conditions.type)" 2>/dev/null

echo ""
echo "=== Cloud Scheduler Jobs ==="
gcloud scheduler jobs list --location $REGION --format="table(name,schedule,state)" 2>/dev/null

echo ""
echo "=== Secrets ==="
for SECRET in database-url redis-url; do
  gcloud secrets versions access latest --secret=$SECRET &>/dev/null && \
    echo "✅ $SECRET" || echo "❌ $SECRET — não configurado"
done

echo ""
echo "=== ML Tables no Cloud SQL ==="
echo "Verificar manualmente no Cloud SQL Studio:"
echo "SELECT tablename FROM pg_tables WHERE tablename LIKE 'ml_%';"
