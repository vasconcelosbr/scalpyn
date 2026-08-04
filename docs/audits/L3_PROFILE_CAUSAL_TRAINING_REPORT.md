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

## Gate original preservado na primeira execução

Na primeira execução, o reparo aumentou a população causal recuperável sem reduzir o gate mínimo. O contrato então vigente exigia `2.000` registros elegíveis `[config: ml]`; a população pré-deploy resolvida era `1.225` `[query]`. Por isso, aquele treino candidato foi interrompido por insuficiência de dados. A alteração posterior, autorizada para o experimento de `1.200` registros `[config/query]`, está documentada na atualização ao fim deste relatório e não concedeu autorização para promoção, ativação ou execução real.

## Validação local

- Suíte focal da correção e contratos relacionados: `39 passed` `[pytest]`.
- Suíte compatível ampliada: `83 passed; 2 failed` `[pytest]`. As duas falhas são de testes legados desatualizados: mock sem `ml_win_fast_threshold_seconds` e expectativa de caminho antigo de migração; não indicam falha do resolver novo.
- Contratos de retrain isolados: `12 passed; 4 failed` `[pytest]`. As quatro falhas também são mocks legados incompletos para chaves de governança já exigidas.
- Compilação Python: concluída sem erro `[compileall]`.
- Auditoria de schema produtivo antes da publicação: `32/32` colunas críticas presentes `[query]`.

## Publicação e treino

Revisão de código publicada: `ddd250091e2ba12f150d767825c38376c54bb068` `[git]`.

A cadeia Alembic foi reconciliada com a revisão social já ativa. O head final é `145_l3_historical_lineage`, descendente de `144_social_intelligence`, sem head paralelo `[alembic/query]`.

Os sete deploys explícitos concluíram com `SUCCESS` `[Railway]`:

| Serviço | Deployment |
|---|---|
| `scalpyn` | `be6ac495-695f-437f-a9e3-aa9bae29a852` `[Railway]` |
| `scalpyn-beat` | `c156b86d-2b52-4b0e-932d-b38fdd841d34` `[Railway]` |
| `scalpyn-ml-trainer` | `855f1826-6530-4caf-97fe-094683227f15` `[Railway]` |
| `scalpyn-worker-compute` | `9e8a815f-644f-4bd1-bdaf-f363e5c3564d` `[Railway]` |
| `scalpyn-worker-execution` | `f777c052-7bee-491b-8466-44f09d518657` `[Railway]` |
| `scalpyn-worker-micro` | `c7107f11-31a4-4a2d-a722-7cf769622aaa` `[Railway]` |
| `scalpyn-worker-structural` | `3f86ff21-ef4f-4f90-aa5b-d4bbc9fc2c06` `[Railway]` |

Saúde pós-deploy: `/api/health` retornou `200` e `/api/health/schema` retornou `200`, `schema_ok=true`, `checked_count=32`, `missing=[]` `[HTTP]`. O log da API registra a aplicação `144_social_intelligence -> 145_l3_historical_lineage` e a conclusão do startup `[Railway logs]`.

Cutoff congelado do treino: `2026-08-04T14:00:23.648128+00:00` `[query/comando]`.

Comando executado:

```powershell
python scripts/run_catboost_retrain.py --lane L3_PROFILE --days 30 --trials 100 --timeout 600 --candidate-only --save-logs --dataset-cutoff 2026-08-04T14:00:23.648128+00:00
```

Resultado literal relevante:

```json
{
  "status": "skipped",
  "reason": "insufficient_retrain_eligible_rows",
  "records": 1225,
  "min_required": 2000,
  "deficit": 775,
  "dataset_query_cutoff": "2026-08-04T14:00:23.648128+00:00",
  "historical_record_count": 1225,
  "shadow_mutations": 0
}
```

O gate interrompeu o fluxo antes de Optuna e do fit. Não foi criado, ativado nem promovido modelo: `l3_models_created_since_cutoff=0`, `active_models_created_since_cutoff=0` e `execution_authority_true_since_cutoff=0` `[query]`.

Os logs pós-deploy também mostram ocorrências operacionais fora deste escopo: o worker estrutural recusou criar alguns shadows de rejeição por `barrier_v2_atr_unavailable` quando `atr_pct=0.0`, e o worker de execução registrou uma falha de assinatura WebSocket `spot.orders` `[Railway logs]`. Esses eventos não são causados pelo resolver histórico e não foram alterados nesta correção.

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
| testes focais | `[pytest]` | `39 passed` |
| testes ampliados | `[pytest]` | `83 passed; 2 failed` |
| schema crítico | `[query]` | `32/32 present` |
| head Alembic | `[query]` | `145_l3_historical_lineage` |
| configuração auditada | `[query]` | `active_ml_config_count=1; config_audit_rows=1` |
| deploys | `[Railway]` | `7 SUCCESS` |
| saúde HTTP | `[HTTP]` | `health=200; schema=200; schema_ok=true; checked_count=32; missing=[]` |
| treino candidato | `[comando]` | `status=skipped; records=1225; min_required=2000; deficit=775` |
| déficit | `[calc: 2000-1225]` | `775` |
| modelos pós-cutoff | `[query]` | `created=0; active=0; execution_authority=0` |

## Atualização autorizada — experimento com gate de 1.200

### Resultado executivo do experimento

O gate total foi alterado de `2.000` para `1.200` registros elegíveis `[config/query]` e o treino candidato foi executado depois do deploy da migração. O modelo foi efetivamente treinado, persistido como `candidate` e rejeitado pelo gate de promoção `[comando/query]`. Nenhum modelo L3 foi ativado e nenhuma autoridade de execução foi concedida `[query]`.

O resultado fora da amostra não validou capacidade preditiva suficiente: ROC AUC de teste `0,49316553544494723` com `N=214` `[query]`, abaixo do piso `0,5` e do mínimo de promoção `N=300` `[config/query]`. O EV líquido de teste persistido foi `-0,628500000000003` `[query]`. Portanto, este artefato permanece exclusivamente experimental e não deve ser promovido.

### Ajuste coerente do gate

Alterar apenas o total para `1.200` não seria operacionalmente suficiente, porque os mínimos antigos de treino, validação e teste eram `1.000 + 200 + 200 = 1.400` `[config/query/calc]`. Na simulação congelada anterior à mudança, a população era `1.225` e nenhum par de fronteiras era viável (`evaluated_boundary_pairs=0`) `[query]`.

Foi aplicada a seguinte configuração candidata, mantendo as proporções, o orçamento do Optuna e o gate de promoção:

| Parâmetro | Antes | Depois |
|---|---:|---:|
| Gate total | `2.000` `[config/query]` | `1.200` `[config/query]` |
| Mínimo de treino | `1.000` `[config/query]` | `600` `[config/query]` |
| Mínimo de validação | `200` `[config/query]` | `200` `[config/query]` |
| Mínimo de teste | `200` `[config/query]` | `200` `[config/query]` |
| Proporções | `0,6 / 0,2 / 0,2` `[config/query]` | `0,6 / 0,2 / 0,2` `[config/query]` |
| Trials Optuna | `100` `[config/query]` | `100` `[config/query]` |
| Timeout Optuna | `600 s` `[config/query]` | `600 s` `[config/query]` |
| Mínimo de teste para promoção | `300` `[config/query]` | `300` `[config/query]` |

No gate exato de `1.200`, a alocação nominal é `720 / 240 / 240` `[calc: 1200×0,6; 1200×0,2; 1200×0,2]`. A soma dos mínimos de partição é `1.000` e deixa uma margem pré-purge de `200` linhas `[calc: 1200-1000]`. O teste nominal ainda fica `60` linhas abaixo do mínimo de promoção `[calc: 300-240]`; assim, reduzir o gate de treino não reduziu o gate de promoção.

### Implementação, validação e deploy

- Migração aplicada: `146_l3_1200_validation`, descendente direta de `145_l3_historical_lineage` `[alembic/query]`.
- Contrato de configuração: `l3_profile_30d_causal_1200_validation_v1` `[config/query]`.
- Auditoria de configuração: `c7c184d2-60ed-4ac6-a3bf-97f6263fa5e2`, registrada em `2026-08-04T16:54:16.770250+00:00` `[query]`.
- Revisão publicada: `48ccf5b0b806c4c90a8ad7a0b572294cec15bb94` `[git]`.
- Testes focais e do validador: `41 passed` `[pytest]`.
- Deployment da API: `c51397c7-5c7f-442d-a16a-e5bd1d98ba17`, estado `SUCCESS` `[Railway]`.
- Log de migração: `Running upgrade 145_l3_historical_lineage -> 146_l3_1200_validation` `[Railway logs]`.
- Saúde pós-deploy: `/api/health=200`; `/api/health/schema=200`, `schema_ok=true`, `checked_count=32`, `missing=[]` `[HTTP]`.

O deploy foi restrito à API porque a mudança é uma migração de configuração no banco; não houve alteração de código de execução dos workers nesta etapa.

### Treino executado após o deploy

Cutoff congelado: `2026-08-04T16:54:59.750008+00:00` `[comando/query]`.

```powershell
python scripts/run_catboost_retrain.py --lane L3_PROFILE --days 30 --trials 100 --timeout 600 --candidate-only --save-logs --dataset-cutoff 2026-08-04T16:54:59.750008+00:00
```

O processo concluiu com código de saída `0` em `62,5 s` `[comando]`. Foram solicitados e executados `100` trials `[comando/query]`; o melhor foi o trial `22`, com objetivo de seleção `net_ev=1,2239500000000036`, seed `42` e timeout `600 s` `[query]`.

Modelo persistido:

| Campo | Resultado |
|---|---:|
| ID | `27dd19c2-39e9-491c-9f85-73d86ac7007d` `[query]` |
| Versão física | `98` `[query]` |
| Lane | `L3_PROFILE` `[query]` |
| Status | `candidate` `[query]` |
| Label | `positive_net_return_v1` `[query]` |
| Features | `28` `[query]` |
| Artefato | `78.446 bytes` `[query]` |
| Registros causais | `1.225` `[comando/query]` |
| Shadows consultados | `1.226` `[comando/query]` |
| Registros excluídos | `1` `[comando/query]` |
| Motivo da exclusão | `no_resolved_model_feature_sources` `[comando/query]` |
| Linhas neutralizadas | `1.225` `[comando/query]` |
| Células neutralizadas | `9.800` `[comando/query]` |
| Mutações em shadows | `0` `[comando/query]` |

### Split temporal efetivo

| Partição/controle | Resultado |
|---|---:|
| Treino bruto | `737` `[query]` |
| Treino efetivo | `643` `[query]` |
| Treino purgado por label | `94` `[query]` |
| Validação bruta | `246` `[query]` |
| Validação efetiva | `213` `[query]` |
| Validação purgada por label | `33` `[query]` |
| Teste bruto | `214` `[query]` |
| Teste efetivo | `214` `[query]` |
| Teste embargado | `28` `[query]` |
| Excluídos do split efetivo | `155` `[query]` |
| Pares de fronteiras avaliados | `528` `[query]` |
| Sobreposição de grupo | `0` `[query]` |
| Sobreposição de trade | `0` `[query]` |
| Sobreposição do horizonte do label | `0` `[query]` |

Janelas persistidas `[query]`:

- treino: `2026-07-24T20:08:51.149191+00:00` a `2026-07-30T14:10:15.194879+00:00`;
- validação: `2026-07-30T15:25:50.164578+00:00` a `2026-08-01T05:26:32.264799+00:00`;
- teste: `2026-08-01T11:23:59.047000+00:00` a `2026-08-03T12:30:08.701274+00:00`.

### Métricas observadas

| Métrica | Validação | Teste |
|---|---:|---:|
| Amostras | `213` `[query]` | `214` `[query]` |
| ROC AUC | `0,5811688311688313` `[query]` | `0,49316553544494723` `[query]` |
| PR AUC | `0,4525324612454358` `[comando]` | `0,37348593731411595` `[query]` |
| Precisão | `0,75` `[query]` | `0,16666666666666666` `[query]` |
| Recall | `0,13636363636363635` `[query]` | `0,01282051282051282` `[query]` |
| F1 | `0,23076923076923078` `[query]` | `0,023809523809523808` `[query]` |
| FPR | `0,02040816326530612` `[query]` | `0,03676470588235294` `[query]` |
| Taxa positiva | `NÃO DISPONÍVEL` | `0,3644859813084112; N=214` `[query]` |
| EV líquido | `NÃO DISPONÍVEL` | `-0,628500000000003; N=214` `[query]` |
| Brier ponderado | `NÃO DISPONÍVEL` | `0,24172647773031689; N=214` `[query]` |

O gap absoluto de ROC AUC é `0,08800329572388407` `[calc: abs(0,5811688311688313-0,49316553544494723)]`, acima do limite `0,05` `[config/query]`.

### Gate de promoção e autoridade

O gate persistiu `REJECTED` por seis motivos literais `[query]`:

1. `test_roc_auc_below_absolute_floor:0.4932<0.5`;
2. `test_samples_below_minimum:214<300`;
3. `generalization_gap_exceeded:0.0880>0.05`;
4. `missing_test_roc_auc_ci_low`;
5. `missing_test_distinct_days`;
6. `test_net_ev_not_positive:-0.628500`.

O registro ficou com `DESCRIPTIVE_REJECTED` e `PREDICTIVE_REJECTED` `[query]`. As autoridades `calibration`, `rule_generation`, `autopilot` e `execution` permaneceram `false` `[query]`. Após o cutoff, houve `1` modelo L3 criado, `0` ativos e `0` com autoridade de execução; no total produtivo também há `0` modelos L3 ativos e `0` com autoridade de execução `[query]`.

### Lacunas de persistência observadas

Na tabela física, `query_hash`, `validation_ev_score` e `test_metrics_json` ficaram `NULL` `[query]`. As métricas de teste estão preservadas em `hyperparams.test_metrics`, e o veredito está em `metrics_json.promotion_gate` `[query]`, mas os campos canônicos nulos reduzem a completude da trilha de auditoria. Esta lacuna não foi corrigida neste experimento e não autoriza promoção.

Os hashes persistidos são: dataset `fbac927b80e5cbc67a91190db4f65fc5f2d27708649ce85b2064aa82354b6e82`, features `2fea22aa707403b1bbf2b6d2732756c4029f8c9b77d64a7658ad13c4a4563dcc`, população Optuna `8e5d31c44c5bae2a4fbd4ebdce898b128bf9fe385c8984edfeaa6878f1a722d5`, split `5158bd3e60ba59d35b4f7fd44a3f61e6e2fe702bdf5690c89b233326624fe5ed` e configuração de treino `36db058872dd0b6629a267500ea275d69c4ab2518ac690cfa6164442428ee494` `[query]`.

### Ledger de Evidências — experimento de 1.200

| NÚMERO REPORTADO | ORIGEM | VALOR LITERAL DA FONTE |
|---|---|---|
| gate total | `[config/query]` | `before=2000; after=1200` |
| mínimos de partição | `[config/query]` | `before=1000/200/200; after=600/200/200` |
| soma antiga incompatível | `[calc: 1000+200+200]` | `1400` |
| alocação nominal | `[calc: 1200×0,6/0,2/0,2]` | `720/240/240` |
| margem pré-purge | `[calc: 1200-(600+200+200)]` | `200` |
| déficit nominal para promoção | `[calc: 300-240]` | `60` |
| simulação pré-mudança | `[query]` | `records=1225; evaluated_boundary_pairs=0` |
| migração | `[query/Railway logs]` | `145_l3_historical_lineage -> 146_l3_1200_validation` |
| auditoria config | `[query]` | `id=c7c184d2-60ed-4ac6-a3bf-97f6263fa5e2; changed_at=2026-08-04T16:54:16.770250+00:00` |
| testes | `[pytest]` | `41 passed` |
| deployment | `[Railway]` | `c51397c7-5c7f-442d-a16a-e5bd1d98ba17; SUCCESS` |
| saúde | `[HTTP]` | `health=200; schema=200; checked_count=32; missing=[]` |
| duração | `[comando]` | `exit_code=0; duration_seconds=62.5` |
| Optuna | `[query]` | `requested=100; executed=100; best_trial=22; best_value=1.2239500000000036; seed=42; timeout=600` |
| modelo | `[query]` | `id=27dd19c2-39e9-491c-9f85-73d86ac7007d; version=98; status=candidate` |
| dataset | `[comando/query]` | `queried=1226; included=1225; excluded=1; neutralized_rows=1225; neutralized_cells=9800; shadow_mutations=0` |
| split bruto | `[query]` | `train=737; validation=246; test=214` |
| split efetivo | `[query]` | `train=643; validation=213; test=214` |
| purge/embargo | `[query]` | `train=94; validation=33; embargo_test=28; excluded=155` |
| sobreposições | `[query]` | `group=0; trade=0; label_horizon=0` |
| validação | `[query/comando]` | `N=213; ROC_AUC=0.5811688311688313; PR_AUC=0.4525324612454358; precision=0.75; recall=0.13636363636363635; F1=0.23076923076923078; FPR=0.02040816326530612` |
| teste | `[query]` | `N=214; ROC_AUC=0.49316553544494723; PR_AUC=0.37348593731411595; precision=0.16666666666666666; recall=0.01282051282051282; F1=0.023809523809523808; FPR=0.03676470588235294; EV=-0.628500000000003` |
| gap de AUC | `[calc: abs(0,5811688311688313-0,49316553544494723)]` | `0,08800329572388407` |
| promoção | `[query]` | `status=REJECTED; reasons=6` |
| autoridade | `[query]` | `created_since_cutoff=1; active_since_cutoff=0; authority_since_cutoff=0; active_l3=0; authority_l3=0` |
| campos canônicos ausentes | `[query]` | `query_hash=NULL; validation_ev_score=NULL; test_metrics_json=NULL` |
