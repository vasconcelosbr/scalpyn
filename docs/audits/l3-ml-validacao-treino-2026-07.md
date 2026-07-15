# L3 ML ? Valida??o de Dataset, Treino CatBoost e Plano de Corre??o

Data: 2026-07-08 13:25:33 UTC
Escopo: **100% L3**. L1 n?o foi treinado, consultado para decis?o, nem alterado nesta rodada.

## Sum?rio Executivo

- Dataset L3 strict p?s-fronteira est? operacionalmente saud?vel para tentativa de treino: `852` linhas, `25` perfis distintos, `0` linhas exclu?das por `profile_id` nulo, coverage de features do `FEATURE_COLUMNS` em `100%` nas 33 features.
- Treino CatBoost L3-only executado uma ?nica vez: candidate `v64`, `model_id=c76115a0-4c58-418b-bfe3-eda6a3b14d2e`, `source_filter=L3`, `label=is_tp_4h_v2_sim_outcome`, `target_window_seconds=14400`.
- Resultado: **REJECTED** pelo Promotion Gate. Motivos literais: `test_samples_below_minimum:171<300`, `generalization_gap_exceeded:0.0699>0.05`, `test_net_ev_not_positive:-0.794366`.
- Interpreta??o: n?o ? um problema de captura b?sica de features ou profile_id; ? principalmente volume de holdout + desalinhamento entre AUC e EV operacional.

## C?digo e Pol?tica Confirmados

- `backend/app/services/ml_challenger_service.py:1489-1519`: CatBoost ? Lane 2 e usa `L3_ONLY`/`L3_LAB_ONLY`; L3+L3_LAB combinado ? bloqueado por padr?o.
- `backend/app/services/ml_challenger_service.py:1529-1537`: m?nimo operacional do trainer ? `MIN_RECORDS=200` ap?s filtro strict de `profile_id`.
- `backend/app/services/ml_challenger_service.py:1590-1618`: candidato CatBoost ? salvo em `ml_models`, com `dataset_policy`, `source_filter`, `label_version`, test metrics e promotion gate.
- `backend/app/ml/dataset_policy.py:43-60`: readiness conservador pede `min_total_rows=1500`, `min_test_rows=250`, `min_profile_overlap=0.70` e limites de feature/source drift.
- `backend/app/ml/promotion_gate.py:93-134`: gate reprova por AUC/test samples/gap/FPR/EV conforme config.

## Valida??o Pr?via do Dataset L3

Conex?o: `production_psycopg2_ok` via Railway `Postgres` / `DATABASE_PUBLIC_URL` (segredo omitido).

Config ativa:

| item | valor |
| --- | --- |
| `ml_dataset_valid_from` | `2026-07-05T19:45:49+00:00` |
| `ml_win_fast_threshold_seconds` | `14400` |
| `ml_promotion_min_test_auc` | `0.6` |
| `ml_promotion_min_test_samples` | `300` |
| `ml_promotion_max_val_test_gap` | `0.05` |
| `ml_promotion_max_test_fpr` | `0.5` |
| `ml_promotion_require_positive_net_ev` | `true` |

Volume por source p?s-fronteira:

| source | eleg?veis 60d | strict profile eligible | perfis distintos |
| --- | ---: | ---: | ---: |
| L3 | 852 | 852 | 25 |
| L3_LAB | 317 | 317 | 17 |
| L3_REJECTED | 20601 | 20601 | 30 |

Sa?de L3 strict:

| m?trica | valor |
| --- | ---: |
| linhas strict | 852 |
| positivos label 4h | 220 |
| positive rate 4h | 25.82% |
| positivos label 30m | 68 |
| positive rate 30m | 7.98% |
| retorno l?quido m?dio | -0.5955 |
| range temporal | 2026-07-05 19:49:51 ? 2026-07-08 12:23:07 UTC |
| perfis distintos | 25 |
| profile overlap aproximado train?test | 1.00 |
| worst coverage FEATURE_COLUMNS | 100.0% |

Inflow L3 strict:

| dia UTC | linhas strict |
| --- | ---: |
| 2026-07-05 | 120 |
| 2026-07-06 | 309 |
| 2026-07-07 | 282 |
| 2026-07-08 parcial | 141 |

Proje??o de volume:

| alvo | c?lculo | proje??o |
| --- | --- | --- |
| test m?nimo 300 | 300 / share observado 0.2007 = 1495 linhas totais | d?ficit 643 linhas |
| readiness conservador 1500 | 1500 - 852 | d?ficit 648 linhas |
| ritmo observado | 852 / 2.6898 dias = 316.8 linhas/dia | ~2.0 dias para ambos |

Conclus?o pr?-treino: **dataset apto para uma tentativa operacional do trainer (`>=200`)**, mas **ainda abaixo do volume recomendado para aprova??o est?vel (`~1500` strict / test >=300)**.

## Treino Executado

Comando l?gico executado via `MLChallengerService.train_challengers`:

```python
enable_lightgbm=False
enable_catboost=True
catboost_source_filter=['L3']
allow_mixed_source=False
lookback_days=60
n_trials_cb=20
win_fast_threshold_s=14400.0
```

Output resumido:

| item | valor |
| --- | --- |
| model_id | `c76115a0-4c58-418b-bfe3-eda6a3b14d2e` |
| version | `64` |
| status | `candidate` |
| lane | `L3_PROFILE` |
| source_filter | `L3` |
| dataset_policy | `L3_PROFILE_STRICT` |
| included_trade_count | `852` |
| excluded_null_profile_id | `0` |
| distinct_profiles | `25` |
| feature_count | `35` |
| rows_with_backfill_neutralized | `0` |

Valida??o:

| m?trica | valor |
| --- | ---: |
| val samples | 104 |
| val ROC AUC | 0.6916 |
| val PR AUC | 0.5991 |
| val precision | 0.7097 |
| val recall | 0.7097 |
| val FPR | 0.1233 |
| threshold escolhido | 0.45 |
| val net EV no threshold 0.45 | +0.2194 |

Test:

| m?trica | valor | gate |
| --- | ---: | --- |
| test samples | 171 | FAIL `<300` |
| test ROC AUC | 0.6217 | PASS `>=0.6` |
| val-test gap | 0.0699 | FAIL `>0.05` |
| test precision | 0.2394 | diagn?stico |
| test recall | 0.5862 | diagn?stico |
| test FPR | 0.3803 | PASS `<0.5` |
| test net EV | -0.7944 | FAIL `<=0` |

Promotion Gate:

```json
{
  "status": "REJECTED",
  "reasons": [
    "test_samples_below_minimum:171<300",
    "generalization_gap_exceeded:0.0699>0.05",
    "test_net_ev_not_positive:-0.794366"
  ]
}
```

## Diagn?stico da Reprova??o

1. **Volume insuficiente de holdout.** O modelo treinou com 852 linhas strict, mas o split/embargo deixou apenas 171 amostras no test. A config exige 300. Relaxar esse gate seria errado: com 171 amostras, a estimativa de EV e precis?o ainda ? fr?gil.

2. **Generaliza??o ainda inst?vel.** Val AUC 0.6916 contra test AUC 0.6217 produz gap 0.0699, acima do m?ximo 0.05. Isso ? compat?vel com dataset jovem p?s-fronteira: s? ~2.69 dias de dados.

3. **AUC n?o est? virando dinheiro.** O test AUC passou o m?nimo, mas o threshold calibrado por EV em valida??o virou EV negativo no test. O modelo est? ordenando algum sinal, mas a pol?tica de sele??o n?o entrega retorno l?quido no holdout.

4. **CatBoost ainda otimiza Optuna por AUC.** O trecho `backend/app/services/ml_challenger_service.py:415-432` escolhe trials por `roc_auc_score(y_val, preds)`. Para L1 o regime recente j? migrou para sele??o por EV l?quido; no CatBoost L3 essa corre??o ainda n?o existe. Isso explica parte do desalinhamento AUC?EV.

5. **N?o ? falha prim?ria de feature coverage.** As 33 `FEATURE_COLUMNS` aparecem com 100% de non-null no dataset L3 strict. As features booleanas naturalmente t?m muitos zeros, mas isso n?o ? aus?ncia de dado.

## Corre??es Propostas Para Aprova??o L3

Prioridade 1 ? **Aguardar volume m?nimo antes de novo treino de aprova??o**:

- Crit?rio: `strict_profile_eligible >= 1500` ou, no m?nimo, proje??o de `test_samples >= 300` no split real.
- Estado atual: 852 strict; d?ficit ~643-648 linhas.
- Proje??o: ~2 dias no ritmo observado.
- A??o: n?o retreinar por resultado antes desse marco. O candidate v64 fica como evid?ncia diagn?stica, n?o como candidato aprov?vel.

Prioridade 2 ? **Migrar CatBoost L3 para sele??o de trial por EV l?quido, n?o AUC**:

- Alterar `_train_catboost_sync` para usar objetivo de Optuna baseado em EV l?quido de valida??o, an?logo ao regime L1 recente.
- Registrar em `metrics_json`: `trial_selection_objective=net_ev`, curva de thresholds, selected_count, net EV por valida??o/test e IC.
- Motivo: v64 passou AUC test, mas falhou EV. Otimizar AUC ? insuficiente para aprova??o operacional.

Prioridade 3 ? **Adicionar gate de robustez do threshold L3**:

- O threshold 0.45 teve EV positivo em valida??o com 31 selecionados, mas falhou no test.
- Proposta: `ml_threshold_min_positives` L3-specific ou m?nimo de cobertura selecionada por split, vindo de config, para evitar thresholds fr?geis em valida??o pequena.
- N?o reduzir `ml_promotion_min_test_samples`; o problema ? amostra, n?o gate severo.

Prioridade 4 ? **Revisar alvo L3 para alinhar com EV por perfil**:

- O label atual `TP_HIT <= 4h` gera sinal estat?stico, mas n?o garante net EV positivo.
- Pr?ximo experimento recomendado: label/objetivo L3 net-of-fees ou regress?o/ranking por `net_return_pct`, mantendo perfil como feature categ?rica e avaliando EV por profile.
- Crit?rio de sucesso: test EV positivo com IC e test_samples >=300; AUC passa a ser m?trica secund?ria.

Prioridade 5 ? **Manter L3/L3_LAB separados**:

- L3_LAB tem s? 317 linhas p?s-fronteira e hist?rico de mixed-source drift.
- N?o combinar `L3 + L3_LAB` para ?ganhar volume?. Isso violaria o bloqueio de source composition que j? existe.

## Estado Operacional Final

- L1 n?o foi executado.
- L3 treinou uma vez e gravou candidate v64.
- Candidate v64 ficou `candidate`, com gate `REJECTED`.
- Nenhum modelo foi promovido para `active`.
- `ml_forward_scoring_enabled` n?o foi alterado.
- Nenhuma corre??o foi implementada nesta rodada; as corre??es acima s?o proposta execut?vel para a pr?xima etapa.

## Ledger de Evid?ncias

| n?mero/reportado | origem | valor literal |
| --- | --- | --- |
| conex?o | [query] psycopg2 readonly | production_psycopg2_ok |
| L3 strict eligible | [query] shadow_trades | 852 |
| perfis distintos | [query] COUNT DISTINCT profile_id | 25 |
| feature coverage | [calc] extract_features(FEATURE_COLUMNS) | 100% nas 33 features |
| candidate | [train output/query] ml_models | v64 / c76115a0-4c58-418b-bfe3-eda6a3b14d2e |
| val AUC | [train output/query] | 0.6915598762704375 |
| test AUC | [query] metrics_json.test.roc_auc | 0.6216610004856726 |
| test samples | [query] ml_models.test_samples | 171 |
| test net EV | [query] metrics_json.test.net_ev | -0.7943661971831001 |
| gate status | [query] metrics_json.promotion_gate.status | REJECTED |
| gate reasons | [query] metrics_json.promotion_gate.reasons | test_samples_below_minimum; generalization_gap_exceeded; test_net_ev_not_positive |

## Runner Output Verbatim

```json
{
  "model_id": "c76115a0-4c58-418b-bfe3-eda6a3b14d2e",
  "version": "64",
  "status": "candidate",
  "lane": "L3_PROFILE",
  "sources": [
    "L3"
  ],
  "label_version": "is_tp_4h_v2_sim_outcome",
  "target_window_seconds": 14400,
  "train_samples": 439,
  "val_samples": 104,
  "test_samples": 171,
  "val_roc_auc": 0.6915598762704375,
  "test_roc_auc": 0.6216610004856726,
  "test_precision": 0.23943661971830985,
  "test_recall": 0.5862068965517241,
  "test_fpr": 0.38028169014084506,
  "test_net_ev": -0.7943661971831001,
  "threshold": 0.45,
  "promotion_gate": {
    "status": "REJECTED",
    "reasons": [
      "test_samples_below_minimum:171<300",
      "generalization_gap_exceeded:0.0699>0.05",
      "test_net_ev_not_positive:-0.794366"
    ]
  }
}
```
