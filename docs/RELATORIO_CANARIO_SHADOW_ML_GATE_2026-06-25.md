# RELATORIO - CANARIO SHADOW ML GATE

## Status Final

FAIL

## Janela do Canario

- Deploys com `ML_GATE_ENABLED=true`: 2026-06-25 20:09:42Z a 20:09:50Z
- Inicio operacional registrado: 2026-06-25 17:13:53 -03:00
- Encerramento/rollback para `ML_GATE_ENABLED=false`: 2026-06-25 20:20:56Z a 20:21:05Z
- Confirmacao SQL final: 2026-06-25 20:23:21Z
- Duracao ativa aproximada: 11m15s
- Motivo do encerramento antecipado: erro critico no ciclo do pipeline e ausencia de persistencia auditavel pos-ativacao.

## Seguranca

| Item | Antes | Durante | Depois |
|---|---:|---:|---:|
| ML_GATE_ENABLED | false | true | false |
| live_trading_enabled | 0 | 0 | 0 |
| auto_pilot_enabled | 0 | 0 | 0 |
| possible_live_orders | 0 | 0 | 0 |

## Servicos alterados

Ativacao temporaria (`ML_GATE_ENABLED=true`) no commit `edce2f01c418a1350c83e28307470e5d22cd9b6b`:

| Servico | Deployment true | Status | Criado em UTC |
|---|---|---|---|
| scalpyn | 51b8204a-a21b-42f9-9454-5cc376320fcb | SUCCESS | 2026-06-25T20:09:42.510Z |
| scalpyn-beat | e41c2a04-7a79-450c-b055-62872af6eb27 | SUCCESS | 2026-06-25T20:09:43.743Z |
| scalpyn-worker-compute | 9ce496dc-53bd-4351-96c1-a776ba95537a | SUCCESS | 2026-06-25T20:09:45.217Z |
| scalpyn-worker-execution | 8a974511-fc30-401d-8313-9c7a02cdf900 | SUCCESS | 2026-06-25T20:09:46.839Z |
| scalpyn-worker-micro | 27010508-394c-4226-9f9d-83662ec35a14 | SUCCESS | 2026-06-25T20:09:48.326Z |
| scalpyn-worker-structural | 19a203a7-9a02-4a01-b6d4-599ae1de6c80 | SUCCESS | 2026-06-25T20:09:50.418Z |

Rollback obrigatorio (`ML_GATE_ENABLED=false`) no mesmo commit:

| Servico | Deployment false | Status | Criado em UTC |
|---|---|---|---|
| scalpyn | 8facb689-7b93-4e28-bd1f-aab69a6e0133 | SUCCESS | 2026-06-25T20:20:56.989Z |
| scalpyn-beat | 985d1dc0-1a26-488d-a50f-26e261f5345a | SUCCESS | 2026-06-25T20:20:58.558Z |
| scalpyn-worker-compute | f370edce-e92f-466e-9a0a-4e451bdaafda | SUCCESS | 2026-06-25T20:21:00.022Z |
| scalpyn-worker-execution | a84957d0-941e-472f-a14d-4b59052d1146 | SUCCESS | 2026-06-25T20:21:02.274Z |
| scalpyn-worker-micro | 90d28173-a521-4f8d-9c78-3dbaa2a88330 | SUCCESS | 2026-06-25T20:21:03.332Z |
| scalpyn-worker-structural | a07cea67-7d50-4a19-9815-2497d2b47305 | SUCCESS | 2026-06-25T20:21:05.064Z |

## Pre-check

```text
version_num = 112_ml_gate_lineage
live_enabled = 0
autopilot_enabled = 0
total_profiles = 109
active_approved_l1 = 1
active_approved_l3 = 1
```

Modelos elegiveis encontrados:

| id | version | model_lane | status | promotion_status | threshold | feature_count |
|---|---:|---|---|---|---:|---:|
| 83eafd35-a3eb-4c22-bb22-b0ab084a59b6 | 50 | L3_PROFILE | active | APPROVED | 0.3279983120858257 | 50 |
| 57ff8ea6-2884-4608-a6a9-e8c321141aeb | 48 | L1_SPECTRUM | active | APPROVED | 0.17276545237911042 | 48 |

As colunas de lineage esperadas existem em `decisions_log`, `ml_opportunity_rankings` e `shadow_trades`.

## Evidencias SQL

Janela SQL usada: `2026-06-25 20:09:50+00` ate `2026-06-25 20:21:05+00`.

### Rankings

```text
total_rankings | used_by_gate | selected_by_l1_ranker | with_decision_id | score_ok | with_gate_action
0              | 0            | 0                     | 0                | 0        | 0
```

### Decisions

```text
total_decisions | ml_gate_enabled_true | with_ranking_id | with_model_id | with_model_version | with_probability | with_threshold | with_score_status | with_gate_action | with_reason_codes | with_payload
0               | 0                    | 0               | 0             | 0                  | 0                | 0              | 0                 | 0                | 0                 | 0
```

### Ranking -> Decision

```text
0 rows
```

### Shadow Trades

```text
total_shadow | with_decision_id | with_ranking_id | with_ml_model_id | with_model_version | with_threshold | with_score_status | with_gate_action | with_reason_codes | with_payload | ml_gate_enabled_true
0            | 0                | 0               | 0                | 0                  | 0              | 0                 | 0                | 0                 | 0            | 0
```

### SKIPPED / Fail-Closed

```text
score_status | gate_action | reason_codes | total
0 rows
```

### Modelos usados

```text
0 rows with decisions_log.ml_gate_enabled = true
```

### Live Trading

```text
possible_live_orders = 0
live_enabled = 0
autopilot_enabled = 0
```

## Logs relevantes

Evidencia de gate ligado e bloqueio fail-closed:

```text
[2026-06-25 20:16:04,433] [MLGate] wl=L3_ANTI_EXAUSTAO_V3: 6/6 ALLOW blocked by ML gate
[2026-06-25 20:16:04,433] [L3_DIAG] wl=L3_ANTI_EXAUSTAO_V3 decisions=6 ALLOW=0 BLOCK=6 profile_passed=13 [ML_GATE_ON]
[2026-06-25 20:16:04,466] [L3_DIAG] wl=L3_ANTI_EXAUSTAO_V3 decisions_to_log=6 prior_visibility=4 current_visibility=0 events={'ML_GATE_BLOCKED': 6}
```

Erro de inferencia observado:

```text
[2026-06-25 20:16:04] [ML] prediction exception lane=L1_SPECTRUM: 'Booster' object has no attribute 'predict_proba'
```

Erro critico que motivou encerramento antecipado:

```text
[2026-06-25 20:20:23] Pipeline scan: market data fetch failed:
asyncpg.exceptions.InFailedSQLTransactionError: current transaction is aborted, commands ignored until end of transaction block

[2026-06-25 20:20:23] [PipelineScan] Error processing watchlist AP - rsi_gte_72_AND_adx_gte_35_AND_ema50_gt_ema200_false:
sqlalchemy.exc.DBAPIError: current transaction is aborted, commands ignored until end of transaction block
```

Evidencia de ausencia de execucao real:

```text
[2026-06-25 20:16:01] Buy cycle result: {'users_processed': 0, 'trades_placed': 0, 'skipped': 1, 'errors': 0}
[TradeMonitor] task complete: {'open_trades': 0, 'no_price': 0, 'closed_tp': 0, 'closed_sl': 0, 'closed_timeout': 0, 'errors': 0}
```

Warnings operacionais adicionais, sem ordem real:

```text
OrderFlow EMPTY BUFFER / REST fallback em multiplos simbolos.
MICRO-SCHED CRITICAL taker_ratio=None em alguns simbolos.
Gate.io INVALID_CURRENCY_PAIR para TON_USDT/TR e Binance 451 em fallback.
```

## Problemas encontrados

- O ML Gate entrou em execucao (`ML_GATE_ON`) e bloqueou 6 decisions, mas nenhuma linha foi persistida em `decisions_log` com `ml_gate_enabled=true`.
- Nenhum `ml_opportunity_rankings` foi criado ou marcado `used_by_gate=true` na janela do canario.
- Nenhum `shadow_trades` herdou lineage do ML Gate porque nao houve decision/ranking persistido na janela.
- O modelo/lane `L1_SPECTRUM` emitiu excecao de inferencia: `'Booster' object has no attribute 'predict_proba'`.
- O pipeline entrou em `InFailedSQLTransactionError`, encerrando a confiabilidade do ciclo e impedindo classificacao como PASS/PARTIAL.

## Veredito tecnico

O canario nao comprovou o fluxo auditavel end-to-end exigido:

```text
ML Gate ON -> ranking -> decision -> ranking.decision_id -> shadow_trade com lineage
```

Apesar de a seguranca operacional ter permanecido intacta (`live_trading_enabled=0`, `auto_pilot_enabled=0`, `possible_live_orders=0`, rollback para `ML_GATE_ENABLED=false` confirmado), o resultado deve permanecer bloqueado para avanco. Antes de qualquer novo canario, e necessario corrigir/investigar:

- compatibilidade do loader/inferencia para modelos LightGBM `Booster` sem `predict_proba`;
- persistencia de decisions/rankings quando o ML Gate bloqueia decisions;
- causa raiz do `InFailedSQLTransactionError` no ciclo do pipeline.

Nao avancar para live trading nesta execucao.
