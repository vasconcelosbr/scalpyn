# Relatório de ajuste, deploy e tentativa de treinamento — L3_PROFILE

## Resultado executivo

O ajuste foi implantado em produção como contrato de treinamento causal e `CANDIDATE_ONLY`. O comando de treinamento solicitado foi executado contra o banco produtivo com cutoff congelado em `2026-08-03T13:04:54.655394Z` `[query]`.

O treino foi corretamente interrompido pelo gate `insufficient_retrain_eligible_rows`: havia `0` `[query]` outcomes L3_PROFILE elegíveis sob o novo contrato causal, contra o mínimo de `2.000` `[config: ml]`. Nenhum modelo foi criado e nenhuma autoridade de execução foi concedida: `0` `[query]` escritas CatBoost e `0` `[query]` modelos com `execution_authority=true` após o cutoff.

## Por que apareceu “apenas 10 amostras”

O modelo v95 **não foi treinado com 10 amostras**. A persistência produtiva mostra:

| Partição | Amostras |
|---|---:|
| Treino | `205` `[query]` |
| Validação | `10` `[query]` |
| Teste | `315` `[query]` |
| Total particionado | `530` `[calc: 205+10+315]` |

A causa técnica foi uma colisão semântica na chamada do split: `ml_threshold_min_positives=10` `[config: ml anterior]`, destinado à calibração do threshold, também era passado como `min_validation_size`. Depois do agrupamento temporal, purge e embargo, uma fronteira com apenas `10` `[query]` casos de validação ainda era considerada válida.

Usar `100%` `[inferência técnica]` do volume para ajustar o modelo também não seria correto: isso eliminaria o holdout independente e impediria medir generalização. O volume elegível deve ser integralmente aproveitado **entre** treino, validação e teste, mas somente a partição de treino pode ajustar os parâmetros. O contrato novo fixa `60%` / `20%` / `20%` `[config: ml]`, preservando validação e teste fora do fit.

## Correção implantada

- Dataset L3_PROFILE dos últimos `30` `[comando]` dias, com cutoff imutável.
- Contrato de captura `point-in-time-v2` `[deploy]`, exigindo `feature_source_at` e um mapa `feature_source_times` por feature.
- Split `60%` / `20%` / `20%` `[config: ml]`.
- Mínimo total: `2.000` `[config: ml]`.
- Mínimo pós-purge no treino: `1.000` `[config: ml]`.
- Mínimos de validação e teste: `200` `[config: ml]` em cada partição.
- `ml_threshold_min_positives=10` `[config: ml]` mantido somente para calibração econômica do threshold.
- Optuna: `100` `[config: ml]` trials, timeout de `600` `[config: ml]` segundos e seed `42` `[config: ml]`.
- Early stopping: `30` `[config: ml]` rounds.
- CatBoost `CPU`, `Logloss`, `AUC`, `MVS`, subsample `0,8` `[config: ml]`, `use_best_model=true`.
- Overlap de trades entre partições validado como zero pelo contrato de membership; hashes exatos de dataset e partições são persistidos quando um modelo chega a ser treinado.
- O teste permanece fora da seleção de hiperparâmetros; Optuna continua selecionando pelo net EV da validação.

O requisito do prompt de `500` `[ABERTO: prompt]` amostras mínimas de treino conflitava com o checklist posterior de `1.000` `[config: ml adotada]`. Foi adotado o valor mais conservador.

## População produtiva

Na auditoria pré-deploy do contrato anterior, o funil possuía `1.221` `[query]` outcomes L3 maduros/elegíveis, distribuídos em `25` `[query]` ativos e `25` `[query]` perfis. Desses, `479` `[query]` tinham retorno positivo e `742` `[query]` retorno não positivo.

Entretanto, `0` `[query]` desses snapshots históricos possuíam a linhagem temporal por feature exigida. Aceitá-los retroativamente exigiria inventar source time ou mutar snapshots point-in-time, ambos proibidos. Por isso, após a ativação do contrato novo, a população causal é:

| Campo | Valor |
|---|---:|
| Elegíveis | `0` `[query]` |
| Ativos | `0` `[query]` |
| Perfis | `0` `[query]` |
| Positivos | `0` `[query]` |
| Não positivos | `0` `[query]` |

Isso não significa descarte físico do histórico. As linhas antigas permanecem preservadas, mas não podem ser usadas como evidência causal no novo treinamento.

## Execução do treino

Comando executado:

```powershell
python scripts/run_catboost_retrain.py --lane L3_PROFILE --days 30 --trials 100 --timeout 600 --candidate-only --save-logs --dataset-cutoff 2026-08-03T13:04:54.655394Z
```

Resultado literal relevante:

```json
{
  "status": "skipped",
  "reason": "insufficient_retrain_eligible_rows",
  "records": 0,
  "min_required": 2000,
  "deficit": 2000,
  "sources": ["L3"],
  "dataset_query_cutoff": "2026-08-03T13:04:54.655394+00:00"
}
```

Como o gate ocorreu antes do split e do Optuna, métricas, hiperparâmetros vencedores e versão CatBoost de um novo artefato estão `NÃO DISPONÍVEIS`. Não existe um “novo modelo ruim”; não existe novo modelo.

## Comparação produtiva disponível

| Modelo | Treino | Validação | Teste | Test AUC | Test EV | Test FPR | Status |
|---|---:|---:|---:|---:|---:|---:|---|
| v95 | `205` `[query]` | `10` `[query]` | `315` `[query]` | `0,5056099463` `[query]` | `-0,4423488764` `[query]` | `0,8341708543` `[query]` | candidate |
| v89 | `241` `[query]` | `109` `[query]` | `303` `[query]` | `0,4914996026` `[query]` | `-0,4519935484` `[query]` | `0,1005917160` `[query]` | candidate |
| v88 | `241` `[query]` | `109` `[query]` | `303` `[query]` | `0,2999205158` `[query]` | `-0,3291200000` `[query]` | `0,1420118343` `[query]` | candidate |
| Novo | `NÃO DISPONÍVEL` | `NÃO DISPONÍVEL` | `NÃO DISPONÍVEL` | `NÃO DISPONÍVEL` | `NÃO DISPONÍVEL` | `NÃO DISPONÍVEL` | não criado |

## Evidência de deploy

- Alembic ativo: `143_l3_training_governance` `[query/deploy log]`.
- Gate de schema: `32/32` `[HTTP: /api/health/schema]` colunas críticas presentes.
- HTTP da API: `200` `[HTTP: /api/health]` e `200` `[HTTP: /api/health/schema]`.
- Deploys `SUCCESS` `[Railway]`: API, worker estrutural, worker de compute, worker de execução, worker de microestrutura e beat.
- `ML_GATE_ENABLED=false` `[Railway config]`.
- Migrações de linhagem e configuração foram auditadas em `config_audit_log` pelo próprio upgrade.
- Checkout original sujo permaneceu intocado; implementação realizada em worktree isolado.

Validação local:

- `81 passed` `[pytest]` no conjunto focal de ML, captura, Strategy Lab e Profile Intelligence.
- `5 passed` `[pytest]` nos testes direcionados adicionais de governança/configuração.
- `2 passed` `[pytest]` no gate de schema crítico.
- Alembic possui `1` `[alembic heads]` head: `143_l3_training_governance` `[alembic heads]`.
- Graphify atualizado após as alterações.

## Recomendação

- Não promover modelo: nenhum candidato novo foi criado.
- Manter coleta causal ativa até alcançar `2.000` `[config: ml]` outcomes maduros e elegíveis.
- Executar novamente o mesmo comando com um novo cutoff somente após o gate de readiness confirmar os mínimos.
- Não redefinir a fronteira nem fabricar timestamps para aproveitar linhas antigas.
- Manter `execution_authority=false` e exigir decisão humana para qualquer promoção futura.

## Ledger de Evidências

| NÚMERO REPORTADO | ORIGEM | VALOR LITERAL DA FONTE |
|---|---|---|
| cutoff produtivo | `[query/comando]` | `2026-08-03T13:04:54.655394Z` |
| contrato ativo | `[query/deploy log]` | `143_l3_training_governance` |
| schema crítico | `[HTTP]` | `checked_count=32; missing=[]` |
| HTTP API/schema | `[HTTP]` | `status=200; status=200` |
| população causal | `[query]` | `eligible_rows=0; assets=0; profiles=0; positive_rows=0; nonpositive_rows=0` |
| população anterior | `[query]` | `eligible=1221; assets=25; profiles=25; positive=479; nonpositive=742; source_time=0` |
| gate total | `[config: ml]` | `ml_catboost_retrain_min_eligible_rows=2000` |
| mínimos das partições | `[config: ml]` | `train=1000; validation=200; test=200` |
| proporções | `[config: ml]` | `train=0.60; validation=0.20; test=0.20` |
| calibração | `[config: ml]` | `ml_threshold_min_positives=10` |
| Optuna | `[config: ml]` | `trials=100; timeout=600; seed=42` |
| early stopping/subsample | `[config: ml]` | `rounds=30; subsample=0.8` |
| resultado do comando | `[comando]` | `records=0; min_required=2000; deficit=2000; status=skipped` |
| escritas pós-cutoff | `[query]` | `catboost_models_created_since_cutoff=0; execution_authority_true_since_cutoff=0` |
| v95 | `[query]` | `train=205; val=10; test=315; auc=0.5056099462831398; ev=-0.4423488764044959; fpr=0.8341708542713567` |
| v89 | `[query]` | `train=241; val=109; test=303; auc=0.4914996025788219; ev=-0.45199354838710104; fpr=0.10059171597633136` |
| v88 | `[query]` | `train=241; val=109; test=303; auc=0.2999205157643734; ev=-0.3291200000000009; fpr=0.14201183431952663` |
| total particionado v95 | `[calc: 205+10+315]` | `530` |
| testes focais | `[pytest]` | `81 passed; 5 passed; 2 passed` |
| heads Alembic | `[alembic heads]` | `1 head: 143_l3_training_governance` |
