# Execucao - Scalpyn Project Plan PDF

Data: 2026-06-25  
Origem: `C:\Users\ricar\Downloads\Scalpyn Project Plan.pdf`  
Escopo executado: implementacao, testes, commit/push, autodeploy Railway, migration 112 e evidencia SQL read-only. Canario controlado ainda nao executado.

## Diagnostico Pre-Implementacao

| Arquivo | Funcao | Responsabilidade | Entrada | Saida | Tabela afetada | Problema encontrado |
|---|---|---|---|---|---|---|
| `backend/app/tasks/pipeline_scan.py` | `_record_ml_opportunity_ranking` | grava score ML do ciclo | decisao L3 + resultado ML | ranking_id | `ml_opportunity_rankings` | `decision_id` ficava NULL porque a decisao ainda nao existia |
| `backend/app/tasks/pipeline_scan.py` | `_persist_decision_logs` | grava auditoria de decisao | lista de decisions | payload com `id` | `decisions_log` | nao havia colunas proprias de ML Gate/ranking/model/payload |
| `backend/app/services/shadow_trade_service.py` | `_create_from_decision` | cria shadow trade a partir de decision | `DecisionLog` + lineage | shadow trade | `shadow_trades` | propagava `ranking_id/model_lane`, mas nao version/threshold/status/action |
| `backend/app/ml/prediction_service.py` | `WinFastPredictor.predict` | inferencia L1/L3 | features + lane | probability/gate result | `ml_predictions` | nao retornava `model_version` |
| `backend/app/services/decision_orchestrator.py` | `_get_active_l1_model_id` | identifica modelo L1 | DB | model_id | `ml_models` | buscava active sem exigir Promotion Gate `APPROVED` |
| `backend/alembic/versions/105_ml_opportunity_rankings.py` | migration | cria rankings | N/A | tabela | `ml_opportunity_rankings` | tinha `decision_id`, mas faltavam campos do contrato de canario |
| `backend/alembic/versions/106_shadow_ml_lineage.py` | migration | adiciona shadow lineage | N/A | colunas | `shadow_trades` | ORM nao declarava campos adicionados por migration |
| `backend/alembic/versions/111_ml_gate_audit_payload.py` | migration | permite SKIPPED em `ml_predictions` | N/A | colunas | `ml_predictions` | OK; base para fail-closed anterior |

Fluxo real mapeado:

```text
POOL -> L1/L2/L3 pipeline_scan -> ML Gate -> decisions_log -> shadow_trades -> outcome -> feedback
```

Tabela real de rankings: `ml_opportunity_rankings` (nao `ml_rankings`).

## Implementacao Realizada

1. Criada migration `112_ml_gate_lineage_contract.py`.
2. Adicionadas colunas auditaveis em `decisions_log`, `ml_opportunity_rankings` e `shadow_trades`.
3. `DecisionLog` e `ShadowTrade` alinhados ao schema novo.
4. `WatchlistLineageContext` passou a carregar version, threshold, score_status, gate_action, reason_codes, payload e `ml_gate_enabled`.
5. `prediction_service` passou a retornar `model_version`.
6. `pipeline_scan` passou a:
   - executar L1 ranker antes de L3 quando ML Gate esta ativo;
   - selecionar por `L1_RANKER_MODE=top_k|percentile`;
   - bloquear candidatos fora do top-k/percentil com reason code auditavel;
   - forcar linha em `decisions_log` para toda decisao avaliada pelo ML Gate;
   - gravar campos ML Gate first-class em `decisions_log`;
   - atualizar `ml_opportunity_rankings.decision_id` apos persistir a decision;
   - propagar payload L1/L3 para shadow lineage.
7. `decision_orchestrator` passou a exigir Promotion Gate `APPROVED` ao buscar L1 ativo.
8. Testes ampliados para lineage, L1 top-k e regressao CatBoost.

## Arquivos Alterados

- `backend/alembic/versions/112_ml_gate_lineage_contract.py`
- `backend/app/ml/prediction_service.py`
- `backend/app/models/backoffice.py`
- `backend/app/models/shadow_trade.py`
- `backend/app/schemas/watchlist_lineage_context.py`
- `backend/app/services/decision_orchestrator.py`
- `backend/app/services/shadow_trade_service.py`
- `backend/app/tasks/pipeline_scan.py`
- `backend/tests/test_ml_gate_fail_closed_audit.py`

## Migration

Head local:

```text
112_ml_gate_lineage (head)
```

Campos principais adicionados:

- `decisions_log.ranking_id`, `model_id`, `model_version`, `model_lane`, `probability`, `threshold_used`, `score_status`, `gate_action`, `reason_codes`, `orchestrator_payload`, `ml_gate_enabled`.
- `ml_opportunity_rankings.threshold_used`, `gate_action`, `used_by_gate`, `rank_percentile`, `l1_ranker_mode`, `selected_by_l1_ranker`, `reason_codes`, `orchestrator_payload`.
- `shadow_trades.model_version`, `threshold_used`, `score_status`, `gate_action`, `reason_codes`, `ml_gate_enabled`.

## Testes

Focados:

```text
77 passed in 1.48s
```

Comando:

```powershell
$env:PYTHONPATH='backend'
python -m pytest backend\tests\test_ml_gate_fail_closed_audit.py backend\tests\test_ml_lane_eligibility.py backend\tests\test_promotion_gate.py backend\tests\test_shadow_ml_lineage.py backend\tests\test_ml_opportunity_ranking_producer.py backend\tests\test_gcs_model_loader_cache.py -q
```

Sintaxe:

```text
py_compile OK
```

Regressao completa:

```text
64 failed, 935 passed, 4 skipped, 2 warnings, 12 errors in 83.11s
```

Comparacao com baseline anterior conhecido:

```text
Antes: 64 failed, 929 passed, 4 skipped, 2 warnings, 12 errors
Agora: 64 failed, 935 passed, 4 skipped, 2 warnings, 12 errors
Delta: +6 passed, 0 novas falhas, 0 novos erros
```

As falhas/erros continuam concentradas em suites ja conhecidas, incluindo dependencias de `localhost:8001`.

## Deploy

Commit:

```text
fe073a287961f080cf399b4472d23dc91b6f13b8 fix(ml): link gate rankings to decisions
```

Autodeploy Railway em production:

| Servico | Status | Instancia | Deployment |
|---|---|---|---|
| `scalpyn` | SUCCESS | RUNNING | `9d211800-3c21-4407-9b71-98caa1fba327` |
| `scalpyn-beat` | SUCCESS | RUNNING | `8a1ac3bb-47ef-46fe-a9f4-1c27e8fb2bb8` |
| `scalpyn-worker-compute` | SUCCESS | RUNNING | `5b71cf94-6e31-483e-8730-59597b12115a` |
| `scalpyn-worker-execution` | SUCCESS | RUNNING | `d4b131d0-7faf-4763-9e1e-11f7cf3e087a` |
| `scalpyn-worker-micro` | SUCCESS | RUNNING | `d62eae1a-8bfe-4a89-86f3-0c8a41597fe0` |
| `scalpyn-worker-structural` | SUCCESS | RUNNING | `4a2805f6-2e21-4c66-8079-80e3b7c4cf69` |

Log de migration no API:

```text
Running upgrade 111_ml_gate_audit_payload -> 112_ml_gate_lineage, Add ML Gate lineage contract fields.
==> [migrations] alembic upgrade head OK
```

## Evidencias SQL

Executadas via `psql` contra o Postgres de producao em transacao `READ ONLY`.

Head aplicado:

```text
version_num
---------------------
112_ml_gate_lineage
```

Colunas confirmadas:

```text
decisions_log:
gate_action, ml_gate_enabled, model_id, model_lane, model_version,
orchestrator_payload, probability, ranking_id, reason_codes,
score_status, threshold_used

ml_opportunity_rankings:
decision_id, gate_action, l1_ranker_mode, orchestrator_payload,
rank_percentile, reason_codes, selected_by_l1_ranker,
threshold_used, used_by_gate

shadow_trades:
decision_id, gate_action, ml_gate_enabled, ml_model_id, model_lane,
model_version, orchestrator_payload, ranking_id, reason_codes,
score_status, threshold_used
```

Estado seguro:

```text
live_enabled=0
autopilot_enabled=0
total_profiles=109
```

Flags Railway:

```text
scalpyn                   ML_GATE_ENABLED=false
scalpyn-worker-compute    ML_GATE_ENABLED=false
scalpyn-worker-execution  ML_GATE_ENABLED=false
scalpyn-worker-micro      ML_GATE_ENABLED=false
scalpyn-worker-structural ML_GATE_ENABLED=false
scalpyn-beat              ML_GATE_ENABLED=false
```

Queries de canario com dados novos ainda nao foram executadas porque `ML_GATE_ENABLED` permaneceu `false`.

## Resultado do Canario

Nao executado. `ML_GATE_ENABLED` nao foi religado por esta execucao.

## Status Final

- `ML_GATE_ENABLED`: confirmado `false` nos 6 servicos backend/worker/beat.
- Live trading: nao alterado.
- v48: implementado como ranker top-k/percentil no ML Gate; nao aprova trade sozinho.
- v50: mantido como L3_PROFILE; regressao CatBoost protegida por testes.
- v51: nao criada nesta execucao; permanece pendente como challenger/candidate em etapa separada.

## Pendencias

1. Autorizar explicitamente canario shadow de 30-60 minutos com `ML_GATE_ENABLED=true`.
2. Coletar evidencias SQL pos-canario.
3. Manter `ML_GATE_ENABLED=false` para live trading ate lineage 100% comprovado.

## Veredito

Implementacao, commit/push, deploy Git-backed e migration 112: PASS.

Canario/end-to-end com dados novos: PENDENTE. Nao houve reativacao do ML Gate nesta execucao.
