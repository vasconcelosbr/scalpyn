# RELATORIO - CORRECAO P0 CANARIO ML GATE

## Status Final

PARTIAL

## Problemas corrigidos

- P0-1: criado adaptador central `predict_positive_probability` para extrair probabilidade positiva de modelos com `predict_proba` ou apenas `predict`, cobrindo o caso LightGBM `Booster` sem `predict_proba`.
- P0-2: preservado o contrato de persistencia de `ML_GATE_BLOCKED` em `decisions_log`; testes novos travam que bloqueios do ML Gate forcarao `decisions_to_log`, campos first-class e update de `ml_opportunity_rankings.decision_id`.
- P0-3: `ml_opportunity_rankings` agora roda dentro de `db.begin_nested()`, evitando que falha de INSERT capturada deixe a transacao pai em estado abortado e provoque cascata de `InFailedSQLTransactionError`.

## Arquivos alterados

| Arquivo | Alteracao | Motivo |
|---|---|---|
| `backend/app/ml/prediction_probability.py` | Novo adaptador multi-modelo e validacao de probabilidade | Suportar sklearn/CatBoost `predict_proba`, LightGBM-like `predict`, XGBoost Booster e outputs escalares/1D/2D |
| `backend/app/ml/prediction_service.py` | Usa `predict_positive_probability`; excecao ML retorna `score_status=ML_EXCEPTION_FAIL_CLOSED` | Eliminar chamada direta obrigatoria a `predict_proba` no runtime principal |
| `backend/app/services/decision_orchestrator.py` | Usa `predict_positive_probability` no caminho CatBoost/profile | Evitar novo ponto runtime dependente de `predict_proba` |
| `backend/app/tasks/pipeline_scan.py` | Ranking insert isolado em SAVEPOINT; log inclui `transaction_rolled_back=true`; fallback interno usa `ML_EXCEPTION_FAIL_CLOSED` | Evitar transacao abortada e manter fail-closed auditavel |
| `backend/tests/test_ml_prediction_probability_adapter.py` | Novo teste unitario do adaptador | Cobrir outputs 1D/2D, invalidos e modelo sem metodo |
| `backend/tests/test_ml_gate_blocked_decision_persistence.py` | Novo teste estrutural de persistencia de bloqueio | Travar que `ML_GATE_BLOCKED` nao desaparece por visibilidade |
| `backend/tests/test_pipeline_scan_transaction_recovery.py` | Novo teste estrutural de recuperacao transacional | Travar SAVEPOINT e rollback por watchlist |
| `backend/tests/test_ml_gate_fail_closed_audit.py` | Atualiza contrato para adaptador e `ML_EXCEPTION_FAIL_CLOSED` | Refletir comportamento P0 esperado |
| `backend/tests/test_ml_opportunity_ranking_producer.py` | Verifica SAVEPOINT/log de rollback no ranking producer | Cobrir P0-3 |

## Diagnostico em codigo

| Arquivo | Funcao | Problema | Evidencia | Correcao |
|---|---|---|---|---|
| `backend/app/ml/prediction_service.py` | `WinFastPredictor.predict` | Chamava `model.predict_proba(X_infer)` diretamente | Log do canario: `'Booster' object has no attribute 'predict_proba'` | Substituido por `predict_positive_probability(...)` |
| `backend/app/services/decision_orchestrator.py` | `_predict_profile_model` | Tambem chamava `predict_proba` diretamente | Busca local encontrou chamadas runtime em `decision_orchestrator.py` | Substituido por `predict_positive_probability(...)` |
| `backend/app/tasks/pipeline_scan.py` | `_record_ml_opportunity_ranking` | INSERT de ranking capturava excecao sem SAVEPOINT | Canario exibiu `InFailedSQLTransactionError` apos falha no ciclo | INSERT agora roda em `async with db.begin_nested()` |
| `backend/app/tasks/pipeline_scan.py` | `_ml_predict_one` | Fallback interno retornava `score_status=SKIPPED` em excecao | Prompt exige fail-closed auditavel | Retorna `score_status=ML_EXCEPTION_FAIL_CLOSED` |
| `backend/app/tasks/pipeline_scan.py` | bloco `decisions_to_log` | Bloqueio ML precisava forcar persistencia mesmo com `current_visibility=0` | Codigo ja continha `if _ml_gate_enabled and sym in _ml_gate_scores: should_log = True` | Teste novo trava esse contrato |
| `backend/app/tasks/pipeline_scan.py` | pos-`_persist_decision_logs` | Ranking precisava receber `decision_id` apos flush | Codigo atualiza `ml_opportunity_rankings` por `ranking_id` | Teste novo trava `UPDATE ... SET decision_id = :decision_id` |

## Testes

```text
$env:PYTHONPATH='backend'; python -m pytest backend\tests\test_ml_gate_fail_closed_audit.py backend\tests\test_ml_opportunity_ranking_producer.py backend\tests\test_shadow_ml_lineage.py backend\tests\test_ml_lane_eligibility.py backend\tests\test_promotion_gate.py backend\tests\test_gcs_model_loader_cache.py backend\tests\test_ml_prediction_probability_adapter.py backend\tests\test_ml_gate_blocked_decision_persistence.py backend\tests\test_pipeline_scan_transaction_recovery.py -q
92 passed in 1.57s
```

```text
$env:PYTHONPATH='backend'; python -m py_compile backend\app\ml\prediction_service.py backend\app\tasks\pipeline_scan.py backend\app\services\decision_orchestrator.py backend\app\services\shadow_trade_service.py backend\app\ml\prediction_probability.py
OK
```

## Regressao

Baseline conhecido:

```text
64 failed, 935 passed, 4 skipped, 12 errors
```

Resultado atual:

```text
64 failed, 954 passed, 1 warning, 12 errors in 85.74s
```

Delta:

```text
falhas: 64 -> 64 (0 novas)
erros: 12 -> 12 (0 novos)
passes: 935 -> 954 (+19)
```

Falhas/erros remanescentes seguem o perfil conhecido do baseline, incluindo testes que dependem de servidor local `localhost:8001` nao iniciado e falhas historicas fora do escopo P0.

## Evidencias SQL local/staging

Nao executado com escrita porque nao havia banco local/staging controlado disponivel nesta execucao. Nenhum SQL mutavel foi executado em producao.

Evidencia read-only de seguranca em producao:

```text
BEGIN;
SET TRANSACTION READ ONLY;
SELECT COUNT(*) FILTER (WHERE live_trading_enabled = true) AS live_enabled,
       COUNT(*) FILTER (WHERE auto_pilot_enabled = true) AS autopilot_enabled,
       COUNT(*) AS total_profiles
FROM profiles;

 live_enabled | autopilot_enabled | total_profiles
--------------+-------------------+----------------
            0 |                 0 |            109
ROLLBACK;
```

## Evidencia de seguranca

```text
ML_GATE_ENABLED=false nos 6 servicos:
scalpyn                   false
scalpyn-beat              false
scalpyn-worker-compute    false
scalpyn-worker-execution  false
scalpyn-worker-micro      false
scalpyn-worker-structural false

live_trading_enabled = 0
auto_pilot_enabled = 0
```

## Riscos remanescentes

- Falta evidencia SQL de escrita em banco local/staging controlado para confirmar materializacao real de `ml_opportunity_rankings`, `decisions_log` e update `ranking.decision_id`.
- O novo canario em producao continua exigindo autorizacao explicita e deve ser encerrado imediatamente se qualquer lineage essencial voltar a ficar ausente.
- O adaptador suporta XGBoost Booster por import opcional de `xgboost`; esta rota foi coberta estruturalmente, mas nao por dependencia real nesta suite local.

## Veredito

Correcao P0 de codigo e testes focados concluida, com regressao sem novas falhas ou novos erros. Ainda nao declarar apto para novo canario shadow ate haver autorizacao explicita e, idealmente, evidencia SQL de escrita em ambiente controlado.

Nao executar live trading. Nao executar Auto-Pilot. Nao ligar `ML_GATE_ENABLED=true` sem novo prompt explicito.

