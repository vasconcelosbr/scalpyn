# Evidencias - ML Gate Fail-Closed / Promotion Gate / Canary Readiness

**[ATUALIZADO 2026-06-25 17:37 UTC — Canário executado e rollback confirmado]**

Data/hora inicial: 2026-06-25 13:15:32 -03:00  
Data/hora final: 2026-06-25 17:37:37 +00:00  
Auditor inicial: Codex  
Auditor canário/rollback: Claude Sonnet 4.6 (Claude Code)  
Projeto Railway: scalpyn (`a3af94be-bbb5-413b-a1bd-c1f0a5db0ee5`)  
Ambiente: production (`8e7bba37-1dc2-4f78-b549-248bbb3ec29d`)  
Commit canário/rollback: `3ceed4cdcaa2c72a2cf29195ba186be72525f620`  
Alembic head banco: `111_ml_gate_audit_payload` confirmado  
CANARY_START: `2026-06-25 17:00:59+00`  
CANARY_END: `2026-06-25 17:31:16+00`  
Veredito: **PASS END-TO-END DO FAIL-CLOSED**

## Resumo Executivo

A correcao ML Gate fail-closed esta implementada na working tree e foi deployada em producao por snapshot local nos 6 servicos backend/worker/beat relevantes. O deploy esta operacional no Railway (`SUCCESS` e instancia `RUNNING`) e o log do API mostra a migration 111 aplicada com sucesso pelo startup gate.

Depois da instalacao do cliente PostgreSQL local, foram executadas consultas `psql` contra o Postgres de producao usando `DATABASE_PUBLIC_URL` do servico `Postgres`, sempre em transacao `READ ONLY`. As consultas confirmaram:

- `alembic_version = 111_ml_gate_audit_payload`.
- Colunas novas de auditoria em `ml_predictions`.
- `model_id`, `win_fast_probability` e `threshold_used` aceitam `NULL`, permitindo linhas `SKIPPED`.
- `live_trading_enabled = 0` e `auto_pilot_enabled = 0` nos profiles.
- Nao existem modelos `APPROVED`; os modelos ativos L1/L3 estao com Promotion Gate `REJECTED`.
- `ml_opportunity_rankings` esta vazia.
- `shadow_trades` nao tem `ranking_id`, `ml_model_id` ou `model_lane`.
- `ml_predictions` esta vazia e `decisions_log` nao tem payload `ml_gate` nas ultimas 24h, coerente com canario ainda nao executado.

O resultado nao e PASS end-to-end completo porque as mudancas ainda nao estao commitadas/pushadas, o deploy nao esta Git-backed (`commitHash=null`) e o canario com `ML_GATE_ENABLED=true` nao foi autorizado nem executado.

## Go/No-Go

Classificacao: APROVADO PARA CANARIO CONTROLADO, COM RESSALVA OPERACIONAL.

Go para: executar canario curto e controlado com `ML_GATE_ENABLED=true`, sem promover modelo/profile e sem live trading.

No-go para: manter `ML_GATE_ENABLED=true` sem acompanhamento, promover modelo, promover profile ou ativar live trading.

Ressalva obrigatoria: antes de depender operacionalmente do deploy, commit/push dos arquivos da correcao deve ser feito para evitar que um autodeploy Git sobrescreva o snapshot Railway.

## Ambiente Validado

| Item | Valor | Evidencia |
|---|---|---|
| Projeto Railway | `scalpyn` | `railway status --json` |
| Ambiente | `production` | `railway status --json` |
| API service | `scalpyn` / `486ae90f-81b9-4593-aa31-6e24e67821b3` | status Railway |
| Postgres service | `Postgres` / `b5a65c6b-1c62-4446-b747-67c7320cfb26` | status Railway |
| ML Gate atual | `<unset>` | `railway variable list --service scalpyn --json` |
| `DATABASE_URL` no API | presente | variaveis Railway redigidas |
| `DATABASE_PUBLIC_URL` no API | ausente | variaveis Railway redigidas |
| `DATABASE_PUBLIC_URL` no Postgres | presente | usado por `psql`, valor nao impresso |
| Cliente SQL | `C:\Program Files\PostgreSQL\17\bin\psql.exe` | conexao validada |

Conexao `psql` validada:

```text
 psql_ok |   db    | user_name
---------+---------+-----------
       1 | railway | postgres
```

## Fase 1 - Git Local

Resultado:

```text
branch: main
HEAD: 9dc50a13ec52ec370cfe6ba88a08bf89d7ad1065
HEAD commit: 9dc50a1 fix(ml): close fail-open gap in negative model-cache (Promotion Gate)
git diff --check: sem erros; somente warnings LF/CRLF
```

Arquivos da correcao ainda nao commitados:

```text
 M backend/app/ml/prediction_service.py
 M backend/app/services/decision_orchestrator.py
 M backend/app/tasks/pipeline_scan.py
 M backend/tests/test_ml_lane_eligibility.py
?? backend/alembic/versions/111_ml_gate_audit_payload.py
?? backend/tests/test_ml_gate_fail_closed_audit.py
```

Evidencias de codigo:

```text
backend/app/ml/prediction_service.py: reason_code="NO_ELIGIBLE_MODEL_FOR_LANE"
backend/app/ml/prediction_service.py: reason_code="ML_EXCEPTION_FAIL_CLOSED"
backend/app/tasks/pipeline_scan.py: model_approved = bool(ml_result.get("model_approved", False))
backend/app/tasks/pipeline_scan.py: "score_status": ml_result.get("score_status") or ("SCORED" if model_approved else "SKIPPED")
backend/app/tasks/pipeline_scan.py: _reasons["ml_gate_payload"] = _gate_payload
backend/app/tasks/pipeline_scan.py: INSERT INTO ml_predictions ... gate_payload
```

Status Fase 1: PASS para codigo presente na working tree; FAIL para codigo commitado.

## Fase 2 - Regressao Comparativa

Comando:

```bash
$env:PYTHONPATH='backend'
python -m pytest backend\tests -q
```

Comparacao:

| Metrica | Baseline HEAD | Atual working tree | Delta |
|---|---:|---:|---:|
| passed | 923 | 929 | +6 |
| failed | 64 | 64 | 0 |
| errors | 12 | 12 | 0 |
| skipped | 4 | 4 | 0 |
| warnings | 2 | 2 | 0 |

Status Fase 2: PASS. Nao houve novas falhas nem novos erros; os 6 testes adicionais passam na working tree atual.

## Fase 3 - Testes Focados ML Gate

Comando:

```bash
$env:PYTHONPATH='backend'
python -m pytest backend\tests\test_ml_gate_fail_closed_audit.py backend\tests\test_ml_lane_eligibility.py backend\tests\test_promotion_gate.py backend\tests\test_shadow_ml_lineage.py -q
```

Resultado:

```text
57 passed in 3.09s
```

Status Fase 3: PASS.

## Fase 4 - Railway Deploy

Status dos servicos relevantes:

| Servico | Deployment | Status | Instancia | Commit | Observacao |
|---|---|---|---|---|---|
| scalpyn | `ebd173c2-5fdd-4c62-9bf6-d70f448f1ec6` | SUCCESS | RUNNING | null | snapshot CLI |
| scalpyn-beat | `fe83d561-b471-4069-97b6-90dd020c5142` | SUCCESS | RUNNING | null | snapshot CLI |
| scalpyn-worker-compute | `0c5a8a38-3494-494b-9d0a-3398896c1347` | SUCCESS | RUNNING | null | snapshot CLI |
| scalpyn-worker-execution | `6fe70df0-861c-4b78-b048-748f91d43dc7` | SUCCESS | RUNNING | null | snapshot CLI |
| scalpyn-worker-micro | `56c75b8b-1582-4daa-8d23-d615404017f3` | SUCCESS | RUNNING | null | snapshot CLI |
| scalpyn-worker-structural | `1a234e20-a09d-4cf9-97b8-58b2c1ee2072` | SUCCESS | RUNNING | null | snapshot CLI |

Observacao: no metadata Railway, todos os deploys desta rodada estao com `commitHash=null` e `cliMessage="fix(ml): fail-closed gate audit evidence"`.

Teste HTTP:

```text
401 application/json https://scalpyn-production.up.railway.app/api/ml/models/eligible?lane=L3_PROFILE
```

Status Fase 4: PASS para deploy operacional e rota existente; FAIL para commit deployado igual ao esperado.

## Fase 5 - Migration 111 / Schema Postgres

Log Railway:

```text
[migrations] attempt 1/3 (timeout 90s) ...
==> [migrations] alembic upgrade head
INFO  [alembic.runtime.migration] Running upgrade 110_shadow_decision_unique -> 111_ml_gate_audit_payload, Add auditable ML Gate payload columns.
==> [migrations] alembic upgrade head OK
```

Consulta `psql`:

```sql
BEGIN;
SET TRANSACTION READ ONLY;
SELECT version_num FROM alembic_version;
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_schema='public'
  AND table_name='ml_predictions'
  AND column_name IN (
    'model_id', 'win_fast_probability', 'threshold_used', 'model_lane',
    'reason_code', 'score_status', 'promotion_gate_status', 'gate_payload'
  )
ORDER BY column_name;
ROLLBACK;
```

Resultado:

```text
version_num
---------------------------
111_ml_gate_audit_payload

column_name             | data_type         | is_nullable
------------------------+-------------------+------------
gate_payload            | jsonb             | YES
model_id                | uuid              | YES
model_lane              | character varying | YES
promotion_gate_status   | character varying | YES
reason_code             | character varying | YES
score_status            | character varying | NO
threshold_used          | double precision  | YES
win_fast_probability    | double precision  | YES
```

Status Fase 5: PASS. O banco esta em `111_ml_gate_audit_payload` e o schema suporta linhas `SKIPPED` sem `model_id`, probabilidade ou threshold.

## Fase 6 - Estado ML Gate Antes do Canario

Estado da flag:

```text
ML_GATE_ENABLED=<unset>
```

Consultas `psql` read-only:

```text
live_enabled | autopilot_enabled | total_profiles
-------------+-------------------+---------------
0            | 0                 | 109

active models:
version 44 | L3_PROFILE  | active | REJECTED | test_roc_auc=0.42603030303030304
version 46 | L1_SPECTRUM | active | REJECTED | test_roc_auc=0.4545600858369099

approved models: 0 rows

ml_opportunity_rankings:
total_rankings=0, rankings_last_hour=0

shadow_trades:
total_shadows=15725, with_ranking_id=0, with_ml_model_id=0, with_model_lane=0
```

Status Fase 6: PASS para pre-canario. O estado atual e seguro para iniciar um canario controlado, pois nao ha live trading/autopilot ativo, nao ha modelos aprovados e nao ha ranking ML valido em producao.

## Fase 7 - Evidencia de Auditoria Antes do Canario

`ml_predictions`:

```text
total_predictions=0
sample rows=0
grouped score_status/reason_code/promotion_gate_status=0 rows
```

`decisions_log`:

```text
total_decisions=62861
decisions_last_24h=8349
ml_gate_decisions_last_24h=0
```

Amostra recente de `decisions_log`:

```text
id    | created_at                    | symbol   | decision | reasons
------+-------------------------------+----------+----------+--------------------------------------
58779 | 2026-06-24 22:01:14.654261+00 | LIT_USDT | ALLOW    | {"cond_loaded_0":"OK",...}
58780 | 2026-06-24 22:03:46.96474+00  | BTC_USDT | ALLOW    | {"adx":"OK","rsi":"OK"}
```

Status Fase 7: PASS para "sem efeito ML Gate antes do canario"; NAO EXECUTADO para prova de `SKIPPED` real porque `ML_GATE_ENABLED` ainda esta desligado e `ml_predictions` esta vazia.

## Fase 8 - Canario Fail-Closed com ML_GATE_ENABLED

Status: **EXECUTADO E CONCLUÍDO** — 2026-06-25 17:00:59–17:31:16 UTC

Commit deployado para o canário: `3ceed4cdcaa2c72a2cf29195ba186be72525f620` (`fix(ml): enforce fail-closed promotion gate audit`)

Todos os 6 serviços redeploys SUCCESS com commitHash=3ceed4cd antes de ML_GATE_ENABLED ser ativado.

`ML_GATE_ENABLED=true` setado via Railway MCP na API e Railway CLI nos 5 workers (pipeline_scan.py:2899 — os workers leem essa env var, não apenas a API).

### Evidências SQL do Canário

**ml_opportunity_rankings (186 total, 100% SKIPPED):**

```
GROUP BY score_status / reason_code / model_id:
('SKIPPED', 'NO_ELIGIBLE_MODEL_FOR_LANE', None, 186)

Rankings com modelo REJECTED: 0
```

**ml_predictions (6 total, todos ALLOW→BLOCK):**

```
GROUP BY: ('SKIPPED', 'NO_ELIGIBLE_MODEL_FOR_LANE', 'BLOCK', 6)

gate_payload amostra:
{'ml_gate': 'BLOCK', 'model_id': None, 'model_lane': 'L3_PROFILE',
 'reason_code': 'NO_ELIGIBLE_MODEL_FOR_LANE', 'score_status': 'SKIPPED',
 'fallback_used': False, 'model_approved': False,
 'fallback_policy': 'DISABLED_FOR_L3_WHEN_GATE_ENABLED',
 'decision_after_ml': 'BLOCK', 'decision_before_ml': 'ALLOW'}
```

**decisions_log com ml_gate_payload: 6 entradas (todos ALLOW→BLOCK)**

**shadow_trades pós-canário:**
```
new_shadows=61  ranking_id=0  ml_model_id=0  model_lane=0  final_priority_score=0
Shadows com lineage REJECTED: 0
```

### Rollback

CANARY_END: `2026-06-25 17:31:16+00`

`ML_GATE_ENABLED=false` setado via Railway CLI em todos os 6 serviços. Confirmado via `railway variable list`.

Health check pós-rollback: `HTTP/1.1 401 Unauthorized` [PASS — API saudável]

### Queries Pós-Rollback

```
2.1 live_enabled=0  autopilot_enabled=0  [PASS]
2.2 Profiles alterados desde CANARY_START: 0  [PASS]
2.3 Modelos criados/alterados desde CANARY_START: 0  [PASS]
2.4 Suggestions alteradas desde CANARY_START: 0  [PASS]
2.5 Rankings: total=186, skipped=186, no_eligible_model=186, with_model_id=0  [PASS]
2.6 Rankings REJECTED: 0  [PASS]
2.7 ml_predictions: total=6, skipped=6, with_reason_code=6, gate_status=6, gate_payload=6  [PASS]
2.8 decisions_log ml_gate: 6  [PASS]
2.9 shadows: 86, ranking_id=0, ml_model_id=0, model_lane=0, final_priority_score=0  [PASS]
2.10 Shadows REJECTED lineage: 0  [PASS]
```

## Matriz Final

| Evidencia | Status | Resultado |
|---|---|---|
| Codigo commitado | FAIL→PASS | snapshot → commit 3ceed4cd no canário |
| Regressao comparativa | PASS | baseline 64 failed/12 errors; atual 64 failed/12 errors |
| Testes focados ML Gate | PASS | 57 passed com `PYTHONPATH=backend` |
| Railway deploy operacional | PASS | 6 servicos `SUCCESS/RUNNING` |
| Railway deploy no commit correto | PASS | commitHash=3ceed4cd em todos os 6 serviços |
| Migration 111 aplicada | PASS | `alembic_version=111_ml_gate_audit_payload` |
| Schema `ml_predictions` suporta SKIPPED | PASS | nullable `model_id`, `win_fast_probability`, `threshold_used`; `gate_payload` jsonb |
| `live_trading_enabled=0` | PASS | 0 de 109 profiles (baseline e pós-rollback) |
| `auto_pilot_enabled=0` | PASS | 0 de 109 profiles (baseline e pós-rollback) |
| Modelos REJECTED identificados | PASS | v44 L3_PROFILE + v46 L1_SPECTRUM REJECTED; 0 APPROVED |
| Canario com ML_GATE_ENABLED=true | PASS | executado 17:00:59–17:31:16 UTC |
| Rankings SKIPPED | PASS | 186/186 SKIPPED, NO_ELIGIBLE_MODEL_FOR_LANE |
| 0 ranking com REJECTED | PASS | 0 linhas |
| `ml_predictions` grava SKIPPED/BLOCK | PASS | 6 entradas, todas SKIPPED/BLOCK em producao |
| `decisions_log.reasons` grava ml_gate_payload | PASS | 6 entradas em producao |
| Shadows sem lineage indevido | PASS | 86 shadows, 0 com ml_model_id/ranking_id |
| 0 shadow com REJECTED | PASS | 0 linhas |
| `ML_GATE_ENABLED` rollback confirmado | PASS | false em todos os 6 servicos |
| Nenhum profile ACTIVE alterado | PASS | 0 profiles alterados desde CANARY_START |
| Nenhum modelo promovido | PASS | 0 modelos novos/alterados desde CANARY_START |
| Nenhum live trading | PASS | live_enabled=0 em todos os momentos |

## Respostas Finais

1. O fail-closed end-to-end pode ser considerado PASS?  
   **SIM.** Canário executado, rollback confirmado, evidências SQL completas, sem efeito colateral.

2. Pode manter `ML_GATE_ENABLED=true`?  
   **NÃO.** Não existe modelo APPROVED. O canário provou segurança de bloqueio, não performance operacional.

3. Pode treinar novo modelo candidato?  
   **SIM.** Treino sem promoção automática, com Promotion Gate obrigatório e avaliação temporal.

4. Pode promover algum modelo?  
   **NÃO.**

5. Pode promover algum profile?  
   **NÃO.**

6. Pode ativar live trading?  
   **NÃO.**

## Pendencias Remanescentes

1. Treinar novo modelo candidato (L3_PROFILE e/ou L1_SPECTRUM) com split temporal obrigatório.
2. Promotion Gate: exigir test_auc ≥ 0.55, test_precision ≥ baseline, gap val/test sem overfitting.
3. Somente após modelo APPROVED: re-ativar ML_GATE_ENABLED em canário shadow-only.
4. Nunca promover automaticamente. Sempre validar em Shadow antes de qualquer operação live.

## Veredito Final

```
FAIL-CLOSED END-TO-END: PASS
CANÁRIO: PASS
ROLLBACK: PASS
```

O ML Gate Fail-Closed está comprovado end-to-end em produção.
O sistema bloqueia sinais com modelos rejeitados e registra auditoria completa.
Ainda não existe modelo aprovado para uso operacional.
Próxima etapa: treinar e validar novo modelo candidato.

---
*Relatório completo: `docs/EXECUCAO_CANARIO_ML_GATE_FAIL_CLOSED_POS_DEPLOY.md`*
