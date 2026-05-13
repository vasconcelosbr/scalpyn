# Scalpyn ML — Deploy GCP Completo
## Arquitetura Final + Sequência de Execução

---

## Arquitetura no GCP

```
┌─────────────────────────────────────────────────────────────┐
│  CLOUD RUN — scalpyn-backend (existente + ML layer)         │
│                                                             │
│  FastAPI                                                    │
│  ├── /api/ml/status          → status do modelo ativo       │
│  ├── /api/ml/predictions     → histórico de predições       │
│  ├── /api/ml/threshold       → ajuste de threshold          │
│  └── /api/ml/reset           → zera inteligência            │
│                                                             │
│  WinFastPredictor                                           │
│  └── carrega win_fast_latest.pkl do GCS no cold start       │
│      cache em memória (TTL 5min)                            │
│      threshold lido do Cloud SQL (Zero Hardcode)            │
└──────────────────┬──────────────────────────────────────────┘
                   │ lê/escreve
                   ▼
┌─────────────────────────────────────────────────────────────┐
│  CLOUD SQL (PostgreSQL) — existente                         │
│  ├── decisions_log          fonte de dados (inalterada)     │
│  ├── ml_models              modelo ativo + threshold        │
│  ├── ml_predictions         log de predições                │
│  ├── ml_performance_log     monitoramento                   │
│  └── optuna_studies         trials de hyperparameter        │
└──────────────────┬──────────────────────────────────────────┘
                   │
        ┌──────────┴──────────┐
        │                     │
        ▼                     ▼
┌───────────────┐   ┌─────────────────────────────────────────┐
│  GCS BUCKET   │   │  CLOUD RUN JOB — scalpyn-ml-trainer      │
│  scalpyn-     │   │                                          │
│  mlflow/      │   │  Executa: domingo 02:00 UTC              │
│  ├── models/  │◄──│  1. Lê decisions_log (Cloud SQL)         │
│  │   ├── v1   │   │  2. Extrai features do metrics JSONB     │
│  │   ├── v2   │   │  3. Optuna (50 trials) → best params     │
│  │   └── latest   │  4. Treina XGBoost                       │
│  └── artifacts│──►│  5. Salva modelo no GCS                  │
└───────────────┘   │  6. Registra em ml_models (Cloud SQL)    │
        │           │  7. Desativa modelo anterior             │
        ▼           └─────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────┐
│  CLOUD RUN — scalpyn-mlflow-ui                              │
│  MLflow Server                                              │
│  ├── backend: Cloud SQL (experimentos, métricas)            │
│  └── artifacts: GCS (modelos, feature importance)           │
│  Acesso restrito — proxy local ou IAM                       │
└─────────────────────────────────────────────────────────────┘
```

---

## Sequência de Execução

### PRÉ-REQUISITO
```bash
gcloud auth login
gcloud config set project SEU_PROJECT_ID
```

### STEP 1 — Infraestrutura GCP (rodar uma vez)
```bash
chmod +x STEP1_gcp_infra.sh
./STEP1_gcp_infra.sh

# Após rodar, preencher os secrets manualmente:
echo -n 'postgresql+psycopg2://user:pass@/dbname?host=/cloudsql/PROJECT:REGION:INSTANCE' | \
  gcloud secrets versions add database-url --data-file=-

echo -n 'redis://user:pass@host:6379' | \
  gcloud secrets versions add redis-url --data-file=-
```

### STEP 2 — Criar arquivos ML no repositório
```
Criar os arquivos conforme STEP2_trainer_job.md:
  ml_trainer/Dockerfile
  ml_trainer/requirements_trainer.txt
  ml_trainer/job.py

Criar conforme STEP5_prediction_service_gcp.md:
  backend/app/ml/__init__.py  (vazio)
  backend/app/ml/gcs_model_loader.py
  backend/app/ml/feature_extractor.py
  backend/app/ml/prediction_service.py

Criar conforme deploy anterior (STEP 6 do scalpyn_ml_deploy.md):
  backend/app/api/ml.py

Atualizar backend/app/main.py (1 linha):
  from app.api.ml import router as ml_router
  app.include_router(ml_router)

Atualizar backend/requirements.txt (adicionar):
  xgboost==2.1.1
  scikit-learn==1.5.0
  joblib==1.4.2
  google-cloud-storage==2.17.0
  pandas==2.2.2
  numpy==1.26.4
  mlflow-skinny==2.13.0

Criar mlflow_server/Dockerfile (conforme STEP3_mlflow_ui.md)
```

### STEP 3 — Deploy MLflow UI
```bash
chmod +x mlflow_server/deploy_mlflow_ui.sh
./mlflow_server/deploy_mlflow_ui.sh

# Acessar localmente:
gcloud run services proxy scalpyn-mlflow-ui --region us-central1 --port 8888
# http://localhost:8888
```

### STEP 4 — Deploy Cloud Run Job + Scheduler
```bash
# Ajustar CLOUD_SQL_INSTANCE no arquivo antes de rodar
chmod +x STEP4_cloud_run_job.sh
./STEP4_cloud_run_job.sh
```

### STEP 5 — Deploy Backend (env vars ML)
```bash
chmod +x STEP6_backend_deploy.sh
./STEP6_backend_deploy.sh
# O seu gcloud run deploy existente já faz o resto
```

### STEP 6 — Verificação Final
```bash
# Incluído no STEP6_backend_deploy.sh
# Verifica: GCS, Cloud Run services, Cloud Run jobs, Scheduler, Secrets, ML tables
```

---

## Alembic Migration Necessária

Adicionar coluna `mlflow_run_id` na tabela `ml_models`:

```bash
cd backend
git pull --rebase
alembic revision --autogenerate -m "add_mlflow_run_id_to_ml_models"
alembic upgrade head
```

Se autogenerate não pegar, editar a migration manualmente:
```python
def upgrade():
    op.add_column('ml_models',
        sa.Column('mlflow_run_id', sa.String(100), nullable=True)
    )

def downgrade():
    op.drop_column('ml_models', 'mlflow_run_id')
```

---

## Custos GCP Estimados

| Serviço | Uso | Custo/mês estimado |
|---------|-----|-------------------|
| GCS bucket | <1GB artifacts | ~$0.02 |
| Cloud Run Job (trainer) | 1x/semana, ~30min, 2CPU/4GB | ~$2-5 |
| Cloud Run (MLflow UI) | min-instances=0, uso esporádico | ~$0-2 |
| Cloud Scheduler | 1 job | $0.10 |
| **Total adicional** | | **~$3-8/mês** |

O backend principal e Cloud SQL já existem — sem custo adicional.

---

## Quando Iniciar o Primeiro Treino

```bash
# Execução manual imediata (não espera domingo)
gcloud run jobs execute scalpyn-ml-trainer \
  --region us-central1 \
  --wait

# Ver logs em tempo real
gcloud run jobs executions list \
  --job scalpyn-ml-trainer \
  --region us-central1
```

---

## Reset Completo da Inteligência ML

```bash
# Via API (quando sistema estiver rodando)
curl -X POST https://SEU_BACKEND_URL/api/ml/reset \
  -H "Authorization: Bearer TOKEN"

# Via SQL direto (Cloud SQL Studio)
TRUNCATE ml_performance_log CASCADE;
TRUNCATE ml_predictions CASCADE;
TRUNCATE ml_feature_importance CASCADE;
TRUNCATE ml_training_dataset CASCADE;
TRUNCATE ml_models CASCADE;
```

---

## Fluxo de Dados — do Sinal ao Modelo

```
1. Sinal L3 gerado → decisions_log (já acontece hoje)
       ↓
2. WinFastPredictor.predict(metrics) é chamado
   no pipeline de execução (integração futura)
       ↓
3. Predição logada em ml_predictions
       ↓
4. Outcome real gravado quando trade fecha
       ↓
5. Todo domingo 02:00 UTC:
   Cloud Run Job lê decisions_log → treina novo modelo
   → salva no GCS → ativa em ml_models
       ↓
6. Próximo cold start do backend:
   GCSModelLoader baixa win_fast_latest.pkl
   Threshold lido de ml_models.decision_threshold
```
