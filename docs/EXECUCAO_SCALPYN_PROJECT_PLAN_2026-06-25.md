# Execucao - Scalpyn Project Plan PDF

Data: 2026-06-25  
Origem: `C:\Users\ricar\Downloads\Scalpyn Project Plan.pdf`  
Escopo executado: implementacao local versionavel, testes e preparacao para novo deploy/canario controlado.

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

## Evidencias SQL

Nao executadas para a migration 112 porque ela ainda nao foi aplicada em producao neste passo. As evidencias SQL obrigatorias devem ser coletadas depois do deploy/migration e antes/durante o canario.

Queries ajustadas ao schema real devem usar `ml_opportunity_rankings`, nao `ml_rankings`.

## Resultado do Canario

Nao executado. `ML_GATE_ENABLED` nao foi religado por esta execucao.

## Status Final

- `ML_GATE_ENABLED`: nao alterado.
- Live trading: nao alterado.
- v48: implementado como ranker top-k/percentil no ML Gate; nao aprova trade sozinho.
- v50: mantido como L3_PROFILE; regressao CatBoost protegida por testes.
- v51: nao criada nesta execucao; permanece pendente como challenger/candidate em etapa separada.

## Pendencias

1. Commit/push dos arquivos desta execucao.
2. Deploy controlado para aplicar migration 112.
3. Coletar SQL pos-migration.
4. Executar canario shadow de 30-60 minutos com `ML_GATE_ENABLED=true`.
5. Coletar evidencias SQL pos-canario.
6. Manter `ML_GATE_ENABLED=false` para live trading ate lineage 100% comprovado.

## Veredito

Implementacao local: PASS.

Deploy/canario/end-to-end: PENDENTE. Nao houve alteracao de producao nem reativacao do ML Gate nesta execucao.
