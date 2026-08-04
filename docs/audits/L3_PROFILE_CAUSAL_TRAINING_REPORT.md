# Relatório consolidado — correção causal e treinamento L3_PROFILE

## Resultado executivo

A ausência física de `feature_source_at` nos shadows históricos não significa ausência total de evidência temporal. O snapshot pai em `decisions_log.metrics.indicators_snapshot`, ligado por `shadow_trades.decision_id`, preserva envelopes com `ts` ou `timestamp` para parte das features `[query]`.

A correção monta uma projeção causal somente em memória: lê os dois aliases, calcula `feature_source_at = max(feature_source_times)` e nunca atualiza `shadow_trades`. Features sem origem exata são convertidas para `NaN` no dataset, sem fabricar timestamps ou valores.

## Contrato implementado

- Join explícito: `JOIN decisions_log dl ON dl.id = st.decision_id` `[código/teste]`.
- Aliases aceitos: `ts` e `timestamp` `[config: ml]`.
- Fonte temporal agregada: máximo dos timestamps válidos por feature `[código/teste]`.
- Imutabilidade: `0` escritas em `shadow_trades` no carregador e `shadow_mutations=0` no diagnóstico `[teste/query]`.
- Grupos sem proveniência confiável: `live_injection` `[config: ml]`.
- Features sempre neutralizadas: `taker_ratio`, `volume_delta`, `flow_strength` e `delta_normalized` `[config: ml]`.
- Features adicionais presentes no modelo, mas sem envelope temporal conciliável, também são neutralizadas por linha `[código]`.
- Âncora histórica do label: instante real da decisão, `decisions_log.created_at` `[config: ml]`.
- Evento de resolução: `barrier_touched_at` para `TP_HIT`/`SL_HIT` e `exit_timestamp` para `TIMEOUT` `[código]`.
- Qualquer fonte posterior à decisão, valor divergente ou label não posterior à decisão falha de forma fechada `[teste]`.

## Evidência produtiva congelada antes do deploy

Cutoff: `2026-08-04T13:31:07.131406+00:00` `[query]`.

| Verificação | Resultado |
|---|---:|
| Shadows históricos consultados | `1.226` `[query]` |
| Labels posteriores à decisão | `1.226` `[query]` |
| Labels não posteriores à decisão | `0` `[query]` |
| Registros resolvidos pelo novo contrato | `1.225` `[query]` |
| Registros excluídos | `1` `[query]` |
| Motivo da exclusão | `no_resolved_model_feature_sources` `[query]` |
| Linhas construídas no dataset | `1.225` `[query]` |
| Labels positivos | `483` `[query]` |
| Linhas com neutralização | `1.225` `[query]` |
| Células neutralizadas | `9.800` `[query]` |
| Fonte de feature posterior à decisão | `0` `[query]` |
| Label não posterior à decisão | `0` `[query]` |
| Mutações em shadows | `0` `[query]` |

As `9.800` células neutralizadas resultam de `1.225 × 8` `[calc: linhas × features neutralizadas]`. Além das quatro features configuradas de injeção ao vivo, quatro scores de contexto sem envelope conciliável nesta população foram neutralizados: `liquidity_score`, `market_structure_score`, `momentum_score` e `signal_score` `[query]`.

## Reavaliação temporal dos labels

A auditoria comparou o instante da decisão com os timestamps históricos:

| Relação temporal | Resultado |
|---|---:|
| `entry_timestamp < decisão` | `1.218` `[query]` |
| `entry_timestamp > decisão` | `8` `[query]` |
| `entry_timestamp = decisão` | `0` `[query]` |
| TP/SL com `barrier_touched_at` | `1.223` de `1.223` `[query]` |
| `exit_timestamp > decisão` | `1.226` `[query]` |

Consequentemente, `entry_timestamp` não é uma âncora histórica consistente. Na projeção do dataset, `entry_timestamp` e `created_at` são reancorados para `decisions_log.created_at`; `holding_seconds` é recalculado entre a decisão e o evento real do label. Os valores originais ficam preservados somente na projeção como `*_original`; nenhuma linha histórica é alterada.

## Gates preservados

O reparo aumenta a população causal recuperável, mas não reduz o gate mínimo. O contrato continua exigindo `2.000` registros elegíveis `[config: ml]`; a população pré-deploy resolvida é `1.225` `[query]`. Portanto, um treino candidato deve ser interrompido por insuficiência de dados até que a população alcance o mínimo. Não há autorização para promoção, ativação ou execução real.

## Validação local

- Suíte focal da correção e contratos relacionados: `38 passed` `[pytest]`.
- Suíte compatível ampliada: `83 passed; 2 failed` `[pytest]`. As duas falhas são de testes legados desatualizados: mock sem `ml_win_fast_threshold_seconds` e expectativa de caminho antigo de migração; não indicam falha do resolver novo.
- Contratos de retrain isolados: `12 passed; 4 failed` `[pytest]`. As quatro falhas também são mocks legados incompletos para chaves de governança já exigidas.
- Compilação Python: concluída sem erro `[compileall]`.
- Auditoria de schema produtivo antes da publicação: `32/32` colunas críticas presentes `[query]`.

## Publicação e treino

Esta seção será preenchida com a revisão Alembic ativa, deploys, saúde HTTP, resultado literal do treino candidato e reconciliação de escritas após a implantação.

## Ledger de Evidências

| NÚMERO REPORTADO | ORIGEM | VALOR LITERAL DA FONTE |
|---|---|---|
| cutoff pré-deploy | `[query]` | `2026-08-04T13:31:07.131406+00:00` |
| shadows consultados | `[query]` | `queried_rows=1226` |
| população resolvida | `[query]` | `included_rows=1225; excluded_rows=1` |
| causa da exclusão | `[query]` | `no_resolved_model_feature_sources=1` |
| dataset montado | `[query]` | `gate_records=1225; built_rows=1225; positive_labels=483` |
| neutralização | `[query]` | `rows_neutralized=1225; neutralized_cells=9800` |
| conta da neutralização | `[calc: 1225×8]` | `9800` |
| causalidade | `[query]` | `source_at_after_decision=0; label_not_after_decision=0` |
| imutabilidade | `[query]` | `shadow_mutations=0` |
| relação entrada/decisão | `[query]` | `entry_before=1218; entry_after=8; entry_equal=0` |
| barreiras TP/SL | `[query]` | `tp_sl_rows=1223; with_barrier_touched_at=1223` |
| saída posterior | `[query]` | `exit_after_decision=1226` |
| gate de treino | `[config: ml]` | `ml_catboost_retrain_min_eligible_rows=2000` |
| testes focais | `[pytest]` | `38 passed` |
| testes ampliados | `[pytest]` | `83 passed; 2 failed` |
| schema crítico | `[query]` | `32/32 present` |
