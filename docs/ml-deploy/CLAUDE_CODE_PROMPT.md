# CLAUDE CODE — Scalpyn ML Deploy Prompt
# Cole este conteúdo como primeira mensagem ao abrir o Claude Code

---

## PROMPT INICIAL

```
Você é o engenheiro responsável pelo deploy da camada ML do Scalpyn.

## Contexto do projeto

Scalpyn é uma plataforma SaaS de trading quantitativo de crypto rodando em:
- Backend: FastAPI no Cloud Run (deploy automático via gcloud run deploy ao alterar main.py)
- Banco: Cloud SQL (PostgreSQL)
- Cache: Redis (redis.io gerenciado)
- Frontend: Next.js no Vercel
- Repositório: https://github.com/vasconcelosbr/scalpyn.git

## Regras obrigatórias

1. ZERO HARDCODE — nenhum threshold, parâmetro ou regra de negócio no código.
   Tudo em PostgreSQL (tabela config_profiles JSONB) ou ml_models.
2. ADDITIVE ONLY — nunca remover ou alterar código, rotas, tabelas ou dados existentes.
   Apenas adicionar.
3. Sempre fazer git pull --rebase antes de qualquer alteração.
4. Validar o que existe no codebase antes de criar qualquer arquivo.
5. celery_app.py está em backend/app/tasks/celery_app.py
6. Tabelas corretas: pool_coins (não pool_assets), pipeline_watchlists (não watchlists)

## Variáveis do ambiente GCP

PROJECT_ID: clickrate-477217
REGION: us-central1
CLOUD_SQL_INSTANCE: clickrate-477217:us-central1:scalpyndata
BACKEND_SERVICE_NAME: scalpyn
BUCKET_NAME: scalpyn-mlflow

## Arquivos de referência

Todos os scripts estão em docs/ml-deploy/:
- README.md         → arquitetura completa + sequência
- STEP1_gcp_infra.sh
- STEP2_trainer_job.md
- STEP3_mlflow_ui.md
- STEP4_cloud_run_job.sh
- STEP5_prediction_service_gcp.md
- STEP6_backend_deploy.sh

## Tarefa

1. Leia docs/ml-deploy/README.md para entender a arquitetura completa
2. Leia cada STEP antes de executá-lo
3. Valide o que já existe no repositório antes de criar arquivos
4. Execute os STEPs em sequência: 1 → 2 → 3 → 4 → 5 → 6
5. A cada STEP concluído, confirme o que foi feito antes de avançar
6. Se encontrar erro, diagnostique e corrija antes de continuar
7. Ao final, rode a verificação do STEP 6 e reporte o status de cada componente

Comece lendo docs/ml-deploy/README.md e depois execute o STEP 1.
```

---

## Como usar

```bash
# 1. Copiar os arquivos para o repositório
cd /caminho/para/scalpyn
mkdir -p docs/ml-deploy

# Copiar todos os arquivos gerados para esta pasta
# (STEP1 a STEP6 + README)

# 2. Commitar
git add docs/ml-deploy/
git commit -m "feat: add ML deploy docs and scripts"
git push

# 3. Abrir Claude Code no diretório do projeto
claude

# 4. Colar o prompt acima como primeira mensagem
# Substituindo os valores entre colchetes:
#   [SEU_PROJECT_ID]     → ex: scalpyn-prod-123456
#   [NOME_DA_INSTANCIA]  → ex: scalpyn-db
#   [NOME_DO_CLOUD_RUN_SERVICE] → ex: scalpyn-backend
```

---

## Valores a substituir antes de usar

| Placeholder | Onde encontrar | Exemplo |
|-------------|---------------|---------|
| `[SEU_PROJECT_ID]` | `gcloud config get-value project` | `clickrate-477217` |
| `[NOME_DA_INSTANCIA]` | Console GCP → Cloud SQL | `scalpyndata` |
| `[NOME_DO_CLOUD_RUN_SERVICE]` | Console GCP → Cloud Run | `scalpyn` |

---

## Prompts de continuação (se sessão interromper)

### Retomar do STEP 2
```
Retomando o deploy ML do Scalpyn.
STEP 1 (infra GCP) já foi executado com sucesso.
Leia docs/ml-deploy/STEP2_trainer_job.md e continue a partir daí.
Regras: ZERO HARDCODE, ADDITIVE ONLY, git pull --rebase primeiro.
```

### Retomar do STEP 4
```
Retomando o deploy ML do Scalpyn.
STEPs 1, 2, 3 concluídos.
Leia docs/ml-deploy/STEP4_cloud_run_job.sh e execute o Cloud Run Job + Scheduler.
PROJECT_ID: clickrate-477217
CLOUD_SQL_INSTANCE: clickrate-477217:us-central1:scalpyndata
```

### Executar treino manual após deploy
```
O deploy ML do Scalpyn está completo.
Execute o primeiro treino manual:
  gcloud run jobs execute scalpyn-ml-trainer --region us-central1 --wait
Monitore os logs e reporte métricas finais (precision, f1, roc_auc).
```

### Se algo der errado
```
Erro no deploy ML do Scalpyn no STEP [N].
Erro: [COLAR MENSAGEM DE ERRO]
Leia docs/ml-deploy/README.md para contexto.
Diagnostique, corrija e continue a partir do STEP [N].
Regras: ADDITIVE ONLY — não remover nada existente.
```
