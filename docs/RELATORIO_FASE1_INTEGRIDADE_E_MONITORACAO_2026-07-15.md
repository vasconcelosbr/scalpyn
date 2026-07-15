# RELATÓRIO — FASE 1 — INTEGRIDADE CERTIFICADA E MONITORAÇÃO CONTÍNUA

Data: 2026-07-15 | Branch: `feat/fase1-integridade-monitoracao` (base `610e04e`)
Contrato: `PROMPT_FASE1_INTEGRIDADE_E_MONITORACAO.md`
Estado: **implementação completa e testada; AGUARDANDO APROVAÇÃO DO OPERADOR para E.2 (deploy)**. E.3/E.4/E.5 são pós-deploy por definição do contrato.

## Decisões do operador (Seção 0) — como foram resolvidas

As decisões estavam preenchidas na execução anterior desta fase (registradas em código e migration, verificadas literalmente):

- **D1 = A** — TP ATR-dinâmico simétrico ao SL; contrato `shadow_atr_dynamic_v2` (`dataset_config.py`, migration 134) [código]
- **D2 = LOG_ONLY** — `ml_data_certification_service.py:6` [código]
- **D3 = 80** — default; lido fail-closed de `ml_certification_generation_floor` (**chave ainda NÃO existe na config de produção** — ver pendências) [query]
- **D4 = 1.5 / clamp [0.5, 3.0]** — espelhando o SL (`_resolve_atr_barriers`, migration 134) [código]

## Status por bloco

| Bloco | Status | Onde |
|---|---|---|
| A — sub-gate estrutural | ✅ (execução anterior) | A-1: `COL_PNL=pnl_pct`; A-2: tabela nova `ml_data_certification_runs` (migration 134) |
| B.1 — config_snapshot completo | ✅ | `shadow_trade_service.py` — `_build_economic_config_snapshot` (contrato econômico + 4 versões de contrato) |
| B.2 — valid_from como contrato | ✅ | `_load_shadow_data` fail-closed sem `dataset_valid_from`; guard duro `dataset_row_before_valid_from` pós-query |
| B.3 — fonte única win_threshold | ✅ | config é única fonte; caller divergente → `win_fast_threshold_divergent`; valor gravado em notes + `ml_training_dataset.win_threshold_s` |
| B.4 — governança transacional | ✅ | `_save_to_db`: contract_ids obrigatórios ANTES de qualquer INSERT; lane/source restritos a `ml_dataset_contracts`; `ml_training_dataset` + `ml_promotion_gate_results` na mesma transação |
| B.5 — D1=A aplicado | ✅ | `_resolve_atr_barriers` + carimbo `_resolve_barrier_contract_version`; contratos semeados na migration 134 |
| C — query de certificação | ✅ | `backend/sql/fase1_certification_integrity.sql` + `ml_data_certification_service.py` (I01–I11, cumulativa, WARNs) |
| D — job 2h + endpoint | ✅ **(nesta sessão)** | `app/tasks/ml_data_certification.py`; beat `crontab(minute=0, hour="*/2")` na fila `structural_compute` (isolada da captura); `GET /api/ml/readiness/latest` em `app/api/ml.py` |
| E.1 — baseline pré-deploy | ✅ **(nesta sessão)** | saída literal abaixo |
| E.2 — deploy | ⏸️ **aguarda aprovação do operador sobre o diff** | regra 1 do contrato |
| E.3/E.4/E.5 | ⏸️ pós-deploy | — |

## Trabalho desta sessão (delta sobre a execução anterior)

1. **Bloco D completo** (não existia): task Celery `app.tasks.ml_data_certification.run` (teardown canônico Task #274; falha do job não re-raise — nunca afeta captura), rota de fila + beat em `celery_app.py`, endpoint `GET /api/ml/readiness/latest`.
2. **Testes (regra 4 do contrato)** — `tests/test_fase1_integrity_certification.py`, 19 testes cobrindo B.1 (snapshot completo + cópia point-in-time), B.2 (fail-closed + guard pré-fronteira), B.3 (divergente e ausente), B.4 (abort sem contract_ids com ZERO INSERTs; lane não registrada), B.5/D1=A (clamps, carimbo v1/v2), C/D (GREEN/RED/YELLOW, I09 informativo vs job, idempotência de alerta, D3 fail-closed).
3. **Refatoração cirúrgica para testabilidade** em `shadow_trade_service.py`: extração dos helpers puros `_resolve_atr_barriers`, `_resolve_barrier_contract_version`, `_build_economic_config_snapshot` (mesma lógica, zero mudança de comportamento).
4. **Testes legados atualizados ao contrato novo**: `test_ml_maturity_embargo.py` (passa `dataset_valid_from` e asserta a cláusula B.2), `test_shadow_profile_attribution.py` (fakes atualizados para o guard de shadow ativo, resolver de lineage V2 e savepoint `begin_nested`).

## Saídas literais de teste

```
tests/test_fase1_integrity_certification.py — 19 passed in 4.69s
tests/test_shadow_profile_attribution.py    — 5 passed
tests/test_ml_maturity_embargo.py           — 4 passed (incluído na suíte abaixo)
tests/test_celery_routing_invariants.py::test_every_registered_task_is_routed — PASSED
tests/test_celery_routing_invariants.py::test_no_routes_for_unknown_tasks     — PASSED

Suíte relacionada (-k "ml or shadow or lineage or governance or certification or celery",
excluindo 2 arquivos com erro de coleção pré-existente):
  14 failed, 409 passed
```

### As 14 falhas restantes são TODAS pré-existentes (nenhuma da Fase 1)

Verificação: baseline `610e04e` nem coleta (importa `app.models.crypto_ev`, arquivo que ficou fora do commit) — o A/B foi feito por inspeção de causa:

| Teste | Causa (pré-existente) |
|---|---|
| `test_algorithm_governance` (2) | referencia `097_ml_champion_challenger_registry.py` movido no rebaseline de migrations |
| `test_celery_routing_invariants::test_no_silent_default_queue_fallback` | fila-sentinela `__no_default__` declarada de propósito (workaround Celery ≥ 5.6 comentado no próprio arquivo) — conflita com o lint antigo |
| `test_direction_vocabulary_invariants[shadow_trade_service.py]` | falso positivo do regex: casa `level_direction = 'up'` (linhas 1328/3023, código commitado no baseline; 0 ocorrências no diff da Fase 1) |
| `test_l1_features` (3) | mock de `CeleryAsyncSessionLocal` que não existe mais no namespace do módulo |
| `test_ml_lane_eligibility` (1) | skip-reason mudou para `MODEL_ARTIFACT_UNAVAILABLE` em trabalho anterior |
| `test_shadow_intrabar_convention` (3) | importa `_MIN_WIN_PNL_PCT` removido pelo label v2 |
| `test_shadow_watchlist_lineage` (3) | lê `backend/app/tasks/pipeline_scan.py` com path relativo que só funciona com cwd na raiz do repo |
| Erros de coleção (2 arquivos) | `test_migration_023_taker_ratio_scale.py` e `test_catboost_retrain_gate_contract.py` referenciam migrations movidas para `legacy/` |

## E.1 — Certificação baseline pré-deploy (literal, produção, READ-ONLY)

Config ativa em produção [query, 2026-07-15 09:57 UTC]:

```
ml_dataset_valid_from              = 2026-07-01T00:00:00+00:00
ml_certification_generation_floor  = None   ← AUSENTE (D3)
ml_maturity_embargo_margin_minutes = 60
ml_win_fast_threshold_seconds      = 14400  ← ver pendência P2
```

Invariantes (janela `entry_timestamp >= 2026-07-01` até now(), população L1_SPECTRUM + ATR_DYNAMIC):

```
I01_outcome_casing                               violacoes=      0 PASS
I02_contratos_nulos_em_elegiveis                 violacoes=    103 FAIL
I03_elegivel_pre_valid_from                      violacoes=      0 PASS
I04_snapshot_incompleto                          violacoes=   2997 FAIL
I05_flag_x_lineage_divergente                    violacoes=      0 PASS
I06_coverage_baixa_em_elegiveis                  violacoes=      4 FAIL
I07_tp_hit_pnl_negativo                          violacoes=      0 PASS
I08_atr_nulo_em_completed_acima_de_meio_pct      violacoes=      0 PASS
I09_geracao_abaixo_do_piso (INFORMATIVO)         violacoes=   2997 PASS (piso 80)
I10_duplicidade_elegivel                         violacoes=      4 FAIL
I11_holding_negativo                             violacoes=      0 PASS
```

Cumulativa: `elegiveis_maturados_pos_boundary=1107`, mediana diária 7d = 229,0 → **dias_para_1500=7 | dias_para_3000=14 | dias_para_5000=22** [query].

**Leitura do baseline (critério E.1 ATENDIDO):** a certificação detecta os problemas conhecidos — I04 FAIL (2.997 linhas sem `barrier_mode`/multiplicadores/win_threshold no snapshot — exatamente o que B.1 corrige) e I06 FAIL com os mesmos 4 elegíveis de coverage < 0,8 observados na Fase 0. I02 (103) são elegíveis criados antes dos carimbos de contrato das migrations 131/133 chegarem ao write path completo; I10 (4) é duplicidade real `(symbol, entry_timestamp)` a investigar pós-deploy. `ml_data_certification_runs` ainda não existe (migration 134 não aplicada) — esperado pré-deploy.

## Ledger consolidado

| Número | Valor | Tag |
|---|---|---|
| Testes novos Fase 1 | 19 passed | [teste] |
| Suíte relacionada | 409 passed / 14 failed pré-existentes | [teste] |
| I04 baseline | 2.997 FAIL | [query] |
| I06 baseline | 4 FAIL | [query] |
| I02 baseline | 103 FAIL | [query] |
| I10 baseline | 4 FAIL | [query] |
| Elegíveis maturados pós-boundary | 1.107 | [query] |
| Mediana diária 7d | 229,0 | [query] |
| Dias para 1500/3000/5000 | 7 / 14 / 22 | [calc de queries] |
| Piso D3 | 80 (default; chave ausente em prod) | [operador]/[query] |

## Pendências que exigem decisão/ação do operador (antes ou durante E.2)

- **P1 — Aprovar o diff do branch** (regra 1). Deploy = `alembic upgrade head` (131→134) + redeploy dos services.
- **P2 — `ml_win_fast_threshold_seconds` = 14400 em produção.** O achado da Fase 0 diz que o contrato canônico é 1800 e que v80 treinou "errado" com 14400 — mas a config viva hoje é 14400. Com B.3 (config = única fonte), o próximo treino usará o que estiver na config. Confirmar 1800 ou 14400 e gravar. Nenhuma alteração foi feita.
- **P3 — Semear `ml_certification_generation_floor=80`** (D3) e `ml_certification_alert_channel='LOG_ONLY'` (D2) na config `ml`. Sem P3 o job falha fail-closed (por design).
- **P4 — Bump de `ml_dataset_valid_from` para o timestamp do deploy** (exigência D1=A / B.2). Hoje: 2026-07-01.
- **P5 — I10 (4 duplicidades elegíveis)** — investigar pós-deploy; se persistir, é candidata a constraint.

## Declaração final (obrigatória, Seção SAÍDA 5)

- **Nenhum backfill** de elegibilidade/lineage executado.
- **`shadow_trades` intocada** — zero UPDATE/DELETE; E.1 rodou com `transaction_read_only=on` [literal na saída].
- **Gate de produção ML não alterado.**
- **Nenhum modelo promovido, demovido ou apagado.**
- Única escrita prevista pelo job (INSERT em `ml_data_certification_runs`) só passa a existir após E.2.

## Definição de pronto

> "Aguardando dados" é o estado oficial do sistema quando `GET /api/ml/readiness/latest` retorna GREEN e o único item pendente é o contador cumulativo.

Pós-E.2/E.3, com o baseline atual (229 elegíveis/dia), a projeção é GREEN com 1.500 elegíveis em ~7 dias a partir do novo `valid_from`.
