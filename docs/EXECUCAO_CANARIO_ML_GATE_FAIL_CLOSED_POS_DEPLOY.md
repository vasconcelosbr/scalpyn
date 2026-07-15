# Execução do Canário ML Gate Fail-Closed — Pós-Deploy

Data/hora início: 2026-06-25 16:20:00+00  
Data/hora fim: 2026-06-25 17:37:37+00  
Auditor: Claude Sonnet 4.6 (Claude Code)  
Ambiente: production (`8e7bba37-1dc2-4f78-b549-248bbb3ec29d`)  
Projeto Railway: scalpyn (`a3af94be-bbb5-413b-a1bd-c1f0a5db0ee5`)  
Serviço API: `scalpyn` (`486ae90f-81b9-4593-aa31-6e24e67821b3`)  
Commit/deploy: `3ceed4cdcaa2c72a2cf29195ba186be72525f620` — `fix(ml): enforce fail-closed promotion gate audit`  
CANARY_START: `2026-06-25 17:00:59+00`  
CANARY_END: `2026-06-25 17:31:16+00`  
ML_GATE_ENABLED antes do rollback: `true` (setado em todos os 6 serviços)  
ML_GATE_ENABLED depois do rollback: `false` (setado em todos os 6 serviços)  
Veredito: **PASS END-TO-END DO FAIL-CLOSED**

---

## Resumo Executivo

O canário controlado do ML Gate Fail-Closed foi executado com sucesso em produção.
Com `ML_GATE_ENABLED=true` e sem modelos `APPROVED`, o sistema opera corretamente em fail-closed:
bloqueia sinais L3 que passariam para ALLOW, registra auditoria completa em `ml_predictions`,
`ml_opportunity_rankings` e `decisions_log`, e não cria lineage indevido em `shadow_trades`.

Rollback de `ML_GATE_ENABLED` confirmado em todos os 6 serviços. Nenhum profile foi
alterado, nenhum modelo foi promovido e live trading permaneceu desligado durante todo o teste.

---

## Fase 1 — Deploy Railway confirmado

| Serviço | Deployment ID | Status | CommitHash | Timestamp |
|---|---|---|---|---|
| scalpyn | 256ece12 | SUCCESS | 3ceed4cd | 2026-06-25 16:24:03 UTC |
| scalpyn-beat | 12e5904c | SUCCESS | 3ceed4cd | 2026-06-25 16:24:04 UTC |
| scalpyn-worker-compute | b0597162 | SUCCESS | 3ceed4cd | 2026-06-25 16:24:04 UTC |
| scalpyn-worker-execution | 801bbe6f | SUCCESS | 3ceed4cd | 2026-06-25 16:24:03 UTC |
| scalpyn-worker-micro | 802d4560 | SUCCESS | 3ceed4cd | 2026-06-25 16:24:04 UTC |
| scalpyn-worker-structural | eb15e96b | SUCCESS | 3ceed4cd | 2026-06-25 16:24:02 UTC |

Teste HTTP:
```
GET https://scalpyn-production.up.railway.app/api/ml/models/eligible?lane=L3_PROFILE
→ HTTP/1.1 401 Unauthorized  [PASS — rota existe, sem 404]
```

---

## Fase 2 — Migration 111 confirmada

```
alembic_version = '111_ml_gate_audit_payload'  [PASS]
```

Schema `ml_predictions` (colunas novas, todos os 8 presentes):

| Coluna | Tipo | Nullable |
|---|---|---|
| gate_payload | jsonb | YES |
| model_id | uuid | YES |
| model_lane | character varying | YES |
| promotion_gate_status | character varying | YES |
| reason_code | character varying | YES |
| score_status | character varying | NO |
| threshold_used | double precision | YES |
| win_fast_probability | double precision | YES |

`model_id`, `win_fast_probability`, `threshold_used` aceitam NULL — schema suporta SKIPPED. [PASS]

`ml_predictions` antes do canário: 0 rows.

---

## Fase 3 — Baseline antes do canário

```sql
-- profiles
live_enabled=0  autopilot_enabled=0  total_profiles=109  [PASS]

-- ml_models active
v44  L3_PROFILE  active  promotion_gate=REJECTED  test_roc_auc=0.426  [PASS]
v46  L1_SPECTRUM active  promotion_gate=REJECTED  test_roc_auc=0.455  [PASS]

-- ml_models APPROVED: 0  [PASS]

-- ml_opportunity_rankings: total=0  rankings_last_hour=0  [PASS]

-- shadow_trades: 15842 total, 0 ranking_id, 0 ml_model_id, 0 model_lane  [PASS]

-- ml_predictions: 0 total  [PASS]

-- ML_GATE_ENABLED no serviço scalpyn: <not set>  [PASS]
```

Todos os critérios para prosseguir com o canário foram atendidos.

---

## Fase 4 — Canário com ML_GATE_ENABLED=true

### Ativação

CANARY_START registrado: `2026-06-25 17:00:59+00`

`ML_GATE_ENABLED=true` setado via Railway MCP em `scalpyn` (API) às 17:00:59 UTC.
Redeploy disparado: deployment `c072440f` → SUCCESS às 17:01:12 UTC.

Após verificar que pipeline_scan lê `ML_GATE_ENABLED` via `os.getenv` nos workers
(pipeline_scan.py:2899), a variável foi estendida a todos os 5 workers às 17:06 UTC.
Todos os workers redeploys iniciados (~17:06) e confirmados em SUCCESS progressivamente.

### Evidências SQL pós-canário

#### ml_opportunity_rankings (186 total, todos SKIPPED)

```
Amostra (5 linhas):
('23ea6a9f', 'ENA_USDT',  2026-06-25 17:19:53 UTC, 'L3_PROFILE', model_id=None, 'SKIPPED', 'NO_ELIGIBLE_MODEL_FOR_LANE', gate=None, score=None)
('0df239a1', 'INJ_USDT',  2026-06-25 17:19:53 UTC, 'L3_PROFILE', model_id=None, 'SKIPPED', 'NO_ELIGIBLE_MODEL_FOR_LANE', gate=None, score=None)
('a97c3c6d', 'NEAR_USDT', 2026-06-25 17:19:53 UTC, 'L3_PROFILE', model_id=None, 'SKIPPED', 'NO_ELIGIBLE_MODEL_FOR_LANE', gate=None, score=None)
...

GROUP BY:
('SKIPPED', 'NO_ELIGIBLE_MODEL_FOR_LANE', None, 186)

Rankings com modelo REJECTED: 0
```

#### ml_predictions (6 total, todos ALLOW→BLOCK)

```
('ac31e397', 2026-06-25 17:09:30 UTC, 'LTC_USDT',  model_id=None, prob=None, threshold=None,
  lane='L3_PROFILE', reason='NO_ELIGIBLE_MODEL_FOR_LANE', score_status='SKIPPED',
  gate='BLOCK',
  gate_payload={'ml_gate': 'BLOCK', 'model_id': None, 'model_lane': 'L3_PROFILE',
    'reason_code': 'NO_ELIGIBLE_MODEL_FOR_LANE', 'score_status': 'SKIPPED',
    'fallback_used': False, 'model_approved': False,
    'fallback_policy': 'DISABLED_FOR_L3_WHEN_GATE_ENABLED',
    'decision_after_ml': 'BLOCK', 'decision_before_ml': 'ALLOW'})
('99256cc5', 2026-06-25 17:09:30 UTC, 'NEAR_USDT', ... idem)
('511ee842', 2026-06-25 17:08:09 UTC, 'SOL_USDT',  ... idem)
('cd38b87b', 2026-06-25 17:07:56 UTC, 'ETH_USDT',  ... idem)
('6eff4b47', 2026-06-25 17:07:56 UTC, 'NEAR_USDT', ... idem)
('781f2720', 2026-06-25 17:07:56 UTC, 'WLD_USDT',  ... idem)

GROUP BY: ('SKIPPED', 'NO_ELIGIBLE_MODEL_FOR_LANE', 'BLOCK', 6)
```

#### decisions_log (6 entradas com ml_gate_payload)

```
(64079, 2026-06-25 17:09:22 UTC, 'LTC_USDT',  'BLOCK', reasons inclui ml_gate_payload:
  {'ml_gate': 'BLOCK', 'model_id': None, 'reason_code': 'NO_ELIGIBLE_MODEL_FOR_LANE',
   'score_status': 'SKIPPED', 'fallback_used': False, 'model_approved': False,
   'fallback_policy': 'DISABLED_FOR_L3_WHEN_GATE_ENABLED',
   'decision_after_ml': 'BLOCK', 'decision_before_ml': 'ALLOW'})
(64080, 2026-06-25 17:09:19 UTC, 'NEAR_USDT', 'BLOCK', ... idem)
(64078, 2026-06-25 17:08:03 UTC, 'SOL_USDT',  'BLOCK', ... idem)
(64075, 2026-06-25 17:07:47 UTC, 'ETH_USDT',  'BLOCK', ... idem)
(64077, 2026-06-25 17:07:46 UTC, 'WLD_USDT',  'BLOCK', ... idem)
(64076, 2026-06-25 17:07:46 UTC, 'NEAR_USDT', 'BLOCK', ... idem)
```

#### shadow_trades pós-canário

```
new_shadows=61  with_ranking_id=0  with_ml_model_id=0  with_model_lane=0  with_final_priority_score=0
Shadows com lineage REJECTED: 0
```

---

## Fase 5 — Rollback de ML_GATE_ENABLED

CANARY_END: `2026-06-25 17:31:16+00`

`ML_GATE_ENABLED=false` setado via Railway CLI em todos os 6 serviços:

```bash
railway variable set ML_GATE_ENABLED=false --service scalpyn            → OK
railway variable set ML_GATE_ENABLED=false --service scalpyn-worker-compute  → OK
railway variable set ML_GATE_ENABLED=false --service scalpyn-worker-execution → OK
railway variable set ML_GATE_ENABLED=false --service scalpyn-worker-micro    → OK
railway variable set ML_GATE_ENABLED=false --service scalpyn-worker-structural → OK
railway variable set ML_GATE_ENABLED=false --service scalpyn-beat           → OK
```

Confirmação lida de volta via `railway variable list`:
```
ML_GATE_ENABLED = false  [todos os 6 serviços]
```

Health check pós-rollback:
```
GET https://scalpyn-production.up.railway.app/api/ml/models/eligible?lane=L3_PROFILE
→ HTTP/1.1 401 Unauthorized  [PASS — API saudável]
```

---

## Fase 6 — Queries pós-rollback

```sql
-- 2.1 Segurança operacional
live_enabled=0  autopilot_enabled=0  total_profiles=109  [PASS]

-- 2.2 Profiles alterados desde CANARY_START: 0 rows  [PASS]

-- 2.3 Modelos alterados/criados desde CANARY_START: 0 rows  [PASS]

-- 2.4 Suggestions alteradas desde CANARY_START: 0 rows  [PASS]

-- 2.5 Rankings sumário
total=186  skipped=186  no_eligible_model=186  with_model_id=0  [PASS]

-- 2.6 Rankings com REJECTED: 0  [PASS]

-- 2.7 ml_predictions sumário
total=6  skipped=6  with_reason_code=6  with_gate_status=6  with_gate_payload=6  [PASS]

-- 2.8 decisions_log ml_gate: 6  [PASS]

-- 2.9 Shadows sumário
new_shadows=86  ranking_id=0  ml_model_id=0  model_lane=0  final_priority_score=0  [PASS]

-- 2.10 Shadows REJECTED lineage: 0  [PASS]
```

---

## Resultado do Canário Fail-Closed

O canário comprovou que, com `ML_GATE_ENABLED=true` e sem modelos `APPROVED`, o sistema opera em fail-closed.

Evidências:

- 186 rankings gerados como `SKIPPED / NO_ELIGIBLE_MODEL_FOR_LANE`.
- 0 rankings válidos com modelo `REJECTED`.
- 6 `ml_predictions` registradas com decisão `ALLOW → BLOCK`.
- `fallback_policy=DISABLED_FOR_L3_WHEN_GATE_ENABLED` em todas as entradas.
- 6 `decisions_log` com `ml_gate_payload` e decisão final `BLOCK`.
- 86 shadows novos sem `ranking_id`, `ml_model_id` ou `model_lane`.
- 0 shadows com lineage de modelo `REJECTED`.
- `live_trading_enabled=0` durante todo o teste.
- `auto_pilot_enabled=0` durante todo o teste.
- Nenhum modelo promovido.
- Nenhum profile promovido ou alterado.
- Rollback de `ML_GATE_ENABLED` confirmado em todos os 6 serviços.

---

## Matriz Final

| Evidência | Status | Resultado |
|---|---|---|
| Deploy Railway confirmado | PASS | 6 serviços SUCCESS, commit 3ceed4cd |
| Commit/deploy contém correção | PASS | commitHash=3ceed4cd em todos |
| Migration 111 aplicada | PASS | alembic_version=111_ml_gate_audit_payload |
| Schema ml_predictions suporta SKIPPED | PASS | 8 colunas, model_id/prob/threshold nullable |
| Live trading desligado | PASS | live_enabled=0 (baseline e pós-rollback) |
| Auto-Pilot desligado | PASS | autopilot_enabled=0 (baseline e pós-rollback) |
| Modelos REJECTED identificados | PASS | v44 L3_PROFILE, v46 L1_SPECTRUM, ambos REJECTED |
| Canário executado | PASS | CANARY_START=17:00:59, CANARY_END=17:31:16 UTC |
| Rankings SKIPPED | PASS | 186/186 SKIPPED, NO_ELIGIBLE_MODEL_FOR_LANE |
| NO_ELIGIBLE_MODEL_FOR_LANE confirmado | PASS | 186 rankings, 6 predictions, 6 decisions |
| 0 ranking válido com REJECTED | PASS | 0 linhas no JOIN com REJECTED |
| ml_predictions grava SKIPPED | PASS | 6 entradas, todas SKIPPED/BLOCK |
| decisions_log.reasons grava ml_gate_payload | PASS | 6 entradas com ml_gate_payload |
| Shadows sem lineage indevido | PASS | 86 shadows, 0 com ml_model_id/ranking_id |
| 0 shadow com modelo REJECTED | PASS | 0 linhas |
| Rollback ML_GATE_ENABLED confirmado | PASS | false em todos os 6 serviços |
| Nenhum profile ACTIVE alterado | PASS | 0 profiles alterados desde CANARY_START |
| Nenhum modelo promovido | PASS | 0 modelos novos/alterados desde CANARY_START |
| Nenhum live trading | PASS | live_enabled=0 em todos os momentos |

---

## Respostas Finais

**O fail-closed end-to-end pode ser considerado PASS?**  
SIM. Canário executado, rollback confirmado, evidências SQL completas, sem efeito colateral.

**Pode manter ML_GATE_ENABLED=true?**  
NÃO. Não existe modelo APPROVED. O canário provou segurança de bloqueio, não performance operacional.

**Pode treinar novo modelo candidato?**  
SIM. Treino sem promoção automática, com Promotion Gate obrigatório e avaliação temporal.

**Pode promover modelo?**  
NÃO.

**Pode promover profile?**  
NÃO.

**Pode ativar live trading?**  
NÃO.

---

## Veredito

```
FAIL-CLOSED END-TO-END: PASS
CANÁRIO: PASS
ROLLBACK: PASS
```

```
O ML Gate Fail-Closed está comprovado end-to-end.
O sistema bloqueia modelos rejeitados com segurança.
Ainda não existe modelo aprovado para uso operacional.
Próxima etapa: treinar e validar novo modelo candidato.
```

---

## Pendências Remanescentes

1. Treinar novo modelo candidato (L3_PROFILE e/ou L1_SPECTRUM) com split temporal obrigatório.
2. Promotion Gate: exigir test_auc ≥ 0.55, test_precision ≥ baseline, gap val/test sem overfitting.
3. Somente após modelo APPROVED: re-ativar ML_GATE_ENABLED em canário shadow-only.
4. Nunca promover automaticamente. Sempre validar em Shadow antes de qualquer operação live.
