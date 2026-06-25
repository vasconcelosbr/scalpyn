# RELATORIO - VALIDACAO SQL CONTROLADA P0 ML GATE

## Status Final

PARTIAL

## Ambiente

- Ambiente: local controlado, cluster PostgreSQL temporario iniciado em `C:\tmp\scalpyn_ml_gate_validation_pg_20260625`
- Banco: `scalpyn_validation` em `localhost:55432`, usuario `scalpyn`, autenticacao `trust` restrita ao cluster temporario
- Commit: `ef477e8c097846df25a87f6bff4ad025006188d7`
- Branch: `main`
- Data/hora inicio: `2026-06-25 18:08:35-03`
- Data/hora fim: `2026-06-25 18:14-03`

## Seguranca

| Item | Valor |
|---|---:|
| ML_GATE_ENABLED local controlado | true |
| ML_GATE_ENABLED producao Railway | false em todos os 6 servicos consultados |
| live_trading_enabled local | 0 |
| auto_pilot_enabled local | 0 |
| possible_live_orders local | 0 |
| live_trading_enabled producao read-only | 0 |
| auto_pilot_enabled producao read-only | 0 |
| possible_live_orders producao read-only 24h | 0 |

Nenhuma escrita foi executada em producao. Producao foi consultada apenas com `BEGIN; SET TRANSACTION READ ONLY; ... ROLLBACK;`.

## Modelos elegiveis

| id | version | lane | status | promotion_status | threshold | feature_count |
|---|---|---|---|---|---:|---:|
| `11111111-1111-1111-1111-111111111111` | `validation-l1-booster-no-predict-proba` | `L1_SPECTRUM` | active | APPROVED | 0.70 | 12 |
| `22222222-2222-2222-2222-222222222222` | `validation-l3-fail-closed` | `L3_PROFILE` | active | APPROVED | 0.65 | 18 |

Os modelos sao fixtures locais controladas, sem promocao real e sem alteracao de thresholds operacionais.

## Execucao

1. `initdb` criou um cluster PostgreSQL temporario em `C:\tmp`.
2. `pg_ctl` iniciou o cluster na porta `55432`.
3. `psql` criou o banco `scalpyn_validation`.
4. A tentativa de aplicar Alembic real em banco vazio falhou em `001_add_overrides_column.py`, pois a migration altera `pools` antes de a tabela existir nesse historico.
5. Foi criado schema temporario compativel com as tabelas e colunas reais do contrato P0: `profiles`, `ml_models`, `ml_opportunity_rankings`, `decisions_log`, `shadow_trades`, `orders`.
6. O ciclo controlado materializou: ranking -> decision -> update `ranking.decision_id` -> shadow trade com lineage.
7. Uma falha de insert duplicado em `ml_opportunity_rankings` foi induzida dentro de `SAVEPOINT`; `ROLLBACK TO SAVEPOINT` recuperou a transacao pai, que continuou utilizavel.

## Evidencias SQL

### Seguranca local

```sql
SELECT COUNT(*) FILTER (WHERE live_trading_enabled = true) AS live_enabled,
       COUNT(*) FILTER (WHERE auto_pilot_enabled = true) AS autopilot_enabled,
       COUNT(*) AS total_profiles
FROM profiles;
```

Resultado: `live_enabled=0`, `autopilot_enabled=0`, `total_profiles=1`.

```sql
SELECT COUNT(*) AS possible_live_orders
FROM orders
WHERE status NOT IN ('cancelled', 'rejected', 'simulation', 'shadow');
```

Resultado: `possible_live_orders=0`.

### Rankings criados

Resultado: `total_rankings=1`, `used_by_gate=1`, `selected_by_l1_ranker=1`, `with_decision_id=1`, `with_score_status=1`, `with_gate_action=1`.

### Decisions com ML Gate

Resultado: `total_decisions=1`, `ml_gate_enabled_true=1`, `with_ranking_id=1`, `with_model_id=1`, `with_model_version=1`, `with_probability=1`, `with_threshold=1`, `with_score_status=1`, `with_gate_action=1`, `with_reason_codes=1`, `with_payload=1`.

### Ranking para Decision

Resultado: `ranking_id=aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa`, `ranking_decision_id=1`, `decision_id=1`, `ml_gate_enabled=true`, `model_id=11111111-1111-1111-1111-111111111111`, `model_version=validation-l1-booster-no-predict-proba`, `probability=0.41`, `threshold_used=0.70`, `score_status=ML_EXCEPTION_FAIL_CLOSED`, `gate_action=BLOCK`.

### Shadow Trades com lineage

Resultado: `total_shadow=1`, `with_decision_id=1`, `with_ranking_id=1`, `with_ml_model_id=1`, `with_model_version=1`, `with_threshold=1`, `with_score_status=1`, `with_gate_action=1`, `with_reason_codes=1`, `with_payload=1`, `ml_gate_enabled_true=1`.

### Fail-closed / BLOCK

Resultado: `score_status=ML_EXCEPTION_FAIL_CLOSED`, `gate_action=BLOCK`, `reason_codes=["ML_EXCEPTION_FAIL_CLOSED","ML_GATE_BLOCKED","VISIBILITY_ZERO_PERSISTED"]`, `total=1`.

### Modelos usados

Resultado: `model_id=11111111-1111-1111-1111-111111111111`, `model_version=validation-l1-booster-no-predict-proba`, `total_decisions=1`.

### Recuperacao transacional

```sql
BEGIN;
SAVEPOINT ranking_insert_guard;
INSERT INTO ml_opportunity_rankings (...) VALUES (... id duplicado ...);
ROLLBACK TO SAVEPOINT ranking_insert_guard;
INSERT INTO orders (status) VALUES ('simulation');
COMMIT;
```

Resultado: erro controlado `duplicate key value violates unique constraint "ml_opportunity_rankings_pkey"`, seguido de `transaction_rolled_back=true`, `simulation_orders_inside_recovered_tx=1` e `possible_live_orders=0`.

## Logs relevantes

```text
ML_GATE_ON=true adapter=predict_positive_probability model_api=predict probability=0.41
ML_EXCEPTION_FAIL_CLOSED score_status=ML_EXCEPTION_FAIL_CLOSED gate_action=BLOCK error=ProbabilityPredictionError
transaction_rolled_back=true | SAVEPOINT ranking_insert_guard recovered duplicate ranking insert
```

Testes focados executados:

```text
backend/tests/test_ml_prediction_probability_adapter.py
backend/tests/test_pipeline_scan_transaction_recovery.py
11 passed in 0.17s
```

Nao houve ocorrencia de `InFailedSQLTransactionError` em cascata.

## Veredito tecnico

Classificacao: PARTIAL.

O contrato SQL e de lineage P0 foi comprovado em escrita controlada local: ranking criado, decision persistida com `ml_gate_enabled=true`, `ranking.decision_id` atualizado, shadow trade criado com lineage completo, fail-closed auditavel e recuperacao por `SAVEPOINT` sem transacao pai abortada.

Nao classifiquei como PASS porque o pipeline real nao foi executado de ponta a ponta contra um banco migrado por Alembic. A tentativa de migrar banco vazio falhou no historico legado de migrations (`pools` inexistente em `001_add_overrides_column.py`), entao a validacao usou schema temporario compativel com o contrato P0 em vez do schema completo produzido pelo app.

## Riscos remanescentes

- Validar o mesmo fluxo em staging/clone com schema completo real.
- Corrigir ou documentar o bootstrap Alembic para banco vazio, se esse caminho precisar suportar validacoes futuras.
- Executar um novo canario shadow curto em producao somente apos autorizacao explicita, mantendo live trading e Auto-Pilot desligados.

## Proximo passo recomendado

Preparar um staging/clone seguro com schema completo real e repetir a validacao via task integrada do `pipeline_scan`. Enquanto isso, nao executar canario produtivo como PASS; o estado atual e suficiente para evidenciar o contrato SQL controlado, mas nao substitui uma rodada integrada em ambiente migrado completo.
