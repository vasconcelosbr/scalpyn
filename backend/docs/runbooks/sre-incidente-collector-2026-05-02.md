# SRE — Fechamento de Incidente: Collector Stalled
**Data:** 2026-05-02  
**Serviço:** `scalpyn` (Cloud Run, us-central1, project `clickrate-477217`)  
**Sintoma:** Nenhum dado novo em `ohlcv` e `decisions_log` após redeploy. API responde normalmente. Banco acessível.

---

## FORMATO OBRIGATÓRIO — 7 itens + Decisão Final

### 1. API health (OK/FAIL)
**✅ OK — 129 ms**

```
GET https://scalpyn-330575088921.us-central1.run.app/api/health
→ HTTP 200  {"status":"ok","version":"0.2.0"}  time=0.129s
```

Schema confirmado OK em probe anterior:
```
GET /api/health/schema
→ HTTP 200  {"schema_ok":true,"checked_count":22,"missing":[]}
```

---

### 2. Pipeline status (operational / lag / decisões)
**❌ NÃO OPERACIONAL**

```
GET /api/system/pipeline-status
→ pipeline_operational : False
→ decisions_flowing    : False
→ last_decision        : 2026-04-29T13:48:16.875840+00:00
→ lag_minutes          : 4790  (~3 dias 7 horas)
→ decisions_last_hour  : 0
```

5138 decisões históricas existem — pipeline funcionou, parou em **2026-04-29 13:48 UTC**.

---

### 3. Logs de coleta (presentes ou não)
**⚠️ NÃO MEDIDO — exige gcloud**

Comando a executar:
```bash
gcloud logging read \
  "resource.type=cloud_run_revision
   AND resource.labels.service_name=scalpyn
   AND (textPayload:\"Starting market data collection\" OR
        textPayload:\"Collecting spot data\" OR
        textPayload:\"No spot pool symbols\" OR
        textPayload:\"Market data collection complete\" OR
        textPayload:\"Failed to collect\")" \
  --project clickrate-477217 --freshness=15m --limit=100
```

**Árvore de decisão:**
- Zero linhas → beat não enfileira `collect_all` → ir para item 4
- `"No spot pool symbols"` → `get_pool_symbols` retorna `[]` em prod
- `"Starting…"` presente mas sem `"…complete"` → fetch Gate.io travando

---

### 4. Worker ativo (SIM/NÃO)
**⚠️ NÃO MEDIDO — exige gcloud**

```bash
gcloud logging read \
  "resource.type=cloud_run_revision
   AND resource.labels.service_name=scalpyn
   AND (textPayload:\"celery\" OR textPayload:\"worker\" OR
        textPayload:\"beat\" OR textPayload:\"scheduler\")" \
  --project clickrate-477217 --freshness=30m --limit=100
```

**Evidência estrutural confirmada (código):**  
`backend/start.sh:122-136` inicia worker e beat em background com PIDs capturados.  
`backend/app/tasks/celery_app.py` configura `broker_connection_max_retries=10` — após 10 falhas, desiste.

---

### 5. Beat ativo (SIM/NÃO)
**⚠️ NÃO MEDIDO — exige gcloud** (mesmo comando do item 4)

Beat schedule confirmado em código: `collect_market_data_every_minute` a cada 60 s (`celery_app.py:66-69`).

---

### 6. Redis OK (SIM/NÃO)
**⚠️ NÃO MEDIDO — exige gcloud**

```bash
gcloud logging read \
  "resource.type=cloud_run_revision
   AND resource.labels.service_name=scalpyn
   AND textPayload:\"redis\"" \
  --project clickrate-477217 --freshness=30m --limit=50
```

E verificar REDIS_URL da revisão ativa:
```bash
gcloud run services describe scalpyn --region us-central1 \
  --project clickrate-477217 \
  --format='value(spec.template.spec.containers[0].env)' \
  | tr ',' '\n' | grep REDIS_URL
```

---

### 7. Container inicia Celery? (SIM/NÃO)
**✅ SIM — confirmado em código**

`backend/start.sh:122-136`:
```bash
celery -A app.tasks.celery_app worker --loglevel=info --concurrency=1 &
CELERY_WORKER_PID=$!
celery -A app.tasks.celery_app beat   --loglevel=info &
CELERY_BEAT_PID=$!
```

Worker e beat são iniciados. Watchdog (`start.sh:153-169`) monitora PIDs e mata container se algum morrer — **mas NÃO checa conectividade Redis**, apenas liveness do processo.

---

## 🎯 DECISÃO FINAL

### C) ⚠️ INSUFICIENTE

**O que foi comprovado:**
- API viva, schema 22/22 OK, container respondendo 129 ms.
- Pipeline parado há **4790 min** desde `2026-04-29T13:48Z`.
- `decisions_last_hour = 0` — zero atividade.
- `start.sh` inicia worker e beat corretamente (análise estática).
- Watchdog monitora apenas liveness de processo, **não conectividade Redis**.
- Nenhum endpoint de trigger manual existe (todos retornam 404).
- `FORCE_RESTART=2026-05-02T21:15:00Z` commitado em `cloudbuild.yaml` para forçar nova revisão no próximo push.

**O que ainda falta (executar com gcloud autenticado):**

```bash
# 1. Identificar revisão ativa e quando foi deployada
gcloud run revisions list --service scalpyn --region us-central1 \
  --project clickrate-477217 --limit 5 \
  --format='table(name,active,deployedAt)'

# 2. Logs de Celery na revisão ativa (últimos 30 min)
REV=$(gcloud run revisions list --service scalpyn --region us-central1 \
  --project clickrate-477217 --filter='status.conditions.status=True' \
  --format='value(name)' --limit 1)
gcloud logging read \
  "resource.type=cloud_run_revision
   AND resource.labels.revision_name=$REV
   AND (textPayload:\"celery\" OR textPayload:\"worker\" OR
        textPayload:\"beat\" OR textPayload:\"Starting market data\"
        OR textPayload:\"No spot pool symbols\"
        OR textPayload:\"Celery process down\")" \
  --project clickrate-477217 --freshness=30m --limit=100

# 3. Verificar REDIS_URL
gcloud run services describe scalpyn --region us-central1 \
  --project clickrate-477217 \
  --format='value(spec.template.spec.containers[0].env)' \
  | tr ',' '\n' | grep -E 'REDIS_URL|FORCE_RESTART'
```

**Após rodar os 3 comandos, aplicar a árvore de decisão:**

| Resultado dos logs | Causa raiz | Ação |
|---|---|---|
| Zero linhas de `celery`/`worker`/`beat` | `start.sh` não chamado — image usa CMD diferente | Verificar Dockerfile CMD |
| `"celery"` presente mas `"Celery process down"` ausente, e worker não responde | Worker morreu antes do watchdog, watchdog morreu junto | `git push` (FORCE_RESTART commitado) |
| `"redis"` com `ConnectionError` | Redis (Memorystore) IP mudou ou serviço down | Atualizar REDIS_URL em Cloud Run secrets |
| `"No spot pool symbols"` | `pool_coins` vazio em prod | Inserir ativos via UI ou `pool_coins` direct insert |
| Tudo presente, sem erro | Fetch Gate.io falhando em massa | Checar rate-limit Gate.io |

---

## Validação pós-fix

```bash
# Aguardar nova revisão Ready (~3-5 min após push/deploy)
# Então:
curl -s https://scalpyn-330575088921.us-central1.run.app/api/system/pipeline-status \
  | python3 -c "import sys,json; d=json.load(sys.stdin); p=d['pipeline']['decisions']; print('last_hour:', p['last_hour'], '| lag_min:', p['lag_minutes'], '| flowing:', d['health_summary']['decisions_flowing'])"

# Critério de sucesso: last_hour > 0 e lag_minutes < 5 dentro de 5 min após revisão Ready
```
