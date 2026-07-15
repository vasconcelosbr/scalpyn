# Auditoria complementar e correcao unificada v50/v52

Data: 2026-07-02  
Prompt: `C:\Users\ricar\Downloads\PROMPT_CORRECAO_UNIFICADA_V50_V52.md`  
Escopo: modelos ativos `v50/L3_PROFILE` e `v52/L1_SPECTRUM`, caminho `MLChallengerService`.

## Sumario executivo

| Item | Veredito | Evidencia principal | Bloco B |
|---|---|---|---|
| A1 | Violado | `MLChallengerService` usa `build_training_dataframe`; label primario vem de `ttt_fast_win_bucket` e fallback de PnL, nao de `outcome` puro. | B2 habilitado |
| A2 | H0: temporal | `_load_shadow_data` ordena `created_at ASC`; `_chronological_split_with_test` faz 60/20/20 por ordem. Sem purge/embargo no challenger. | B4 apenas registrar |
| A3 | Holdout existe, mas ha dupla selecao na validacao | Optuna otimiza AUC de validacao e threshold final usa mediana da mesma validacao; test so avalia no fim. | B6 deve preservar test intocado |
| A4 | Gate frouxo explica v50 | Gate aprova `test_auc >= 0.55`, gap <= 0.15, `test_fpr <= 0.55`; v50 passou com 0.5582. | B5 habilitado |
| A5 | Exposicao real confirmada | `L3_PROFILE`: 1.304 rankings/decisoes `used_by_gate=true` nos ultimos 7 dias. | B1 executado |
| A6 | Vies de selecao e feature constante | `profile_id IS NULL`: TP 37,67%, PnL -0,1255; `profile_id IS NOT NULL`: TP 60,00%, PnL +0,2073; `source='L3'` 100%. | B6 deve remover/mitigar |
| A7 | Drift, nao decisao formal por lane | Config ativa `ml_win_fast_threshold_seconds=14400`; v50 gravado com 1800; scripts/docs contraditorios. | B2 habilitado |
| A8 | Violado no challenger | Query do challenger nao aplica `ml_dataset_valid_from`; reconstrucao v50: 1.457 linhas pre-fronteira em 8.244 sob filtro L3. | B3 habilitado |
| A9 | Contrato violado | L3 tem cobertura baixa para grande parte do `BASE_FEATURE_COLUMNS`; `volume_24h_usdt` L3 7,06%, L1 50,70%; `atr_pct <= 0` aparece. | B8 habilitado |

## B1 executado

Condicao A5 satisfeita: `L3_PROFILE` influenciou producao. Foi executada uma unica statement SQL de UPDATE em `ml_models`:

```sql
UPDATE ml_models
SET status='rejected',
    retired_at=COALESCE(retired_at, NOW()),
    notes=COALESCE(notes,'') || ' | B1 audit 2026-07-02: rebaixado de ACTIVE para rejected; L3_PROFILE influenciou 1304 decisoes nos ultimos 7 dias; test_auc=0.5582/test_recall=0.2513; risco operacional ativo.'
WHERE id='83eafd35-a3eb-4c22-bb22-b0ab084a59b6'
  AND version='50'
  AND status='active'
  AND model_lane='L3_PROFILE'
RETURNING id::text, version, status, model_lane, retired_at, notes;
```

Resultado:

```json
{
  "id": "83eafd35-a3eb-4c22-bb22-b0ab084a59b6",
  "version": "50",
  "status": "rejected",
  "model_lane": "L3_PROFILE",
  "retired_at": "2026-07-02 21:12:00.098417+00:00"
}
```

Verificacao posterior:

```json
[
  {"version":"50","status":"rejected","model_lane":"L3_PROFILE","retired_at":"2026-07-02 21:12:00.098417+00:00"},
  {"version":"52","status":"active","model_lane":"L1_SPECTRUM","retired_at":null}
]
```

Comportamento sem modelo elegivel: `backend/app/ml/prediction_service.py` retorna `status=no_active_model` e nao produz score quando o loader nao encontra `status='active'`, `model_lane` correto e `promotion_gate APPROVED`; o consumo em `backend/app/tasks/pipeline_scan.py` registra `score_status` e segue o comportamento de gate ja existente.

## Evidencias por questao

### A1 - Caminho MLChallengerService

Hipoteses: H0 label canonico por `outcome` + `holding_seconds`; H1 label usa TTT/PnL.  
Veredito: H1 confirmado.

Evidencia:

- `backend/app/services/ml_challenger_service.py:365`: query em `shadow_trades`.
- `backend/app/services/ml_challenger_service.py:381`: `source IN (...)`, `outcome IN ('TP_HIT','SL_HIT','TIMEOUT')`, `pnl_pct IS NOT NULL`, `features_snapshot` nao vazio, `created_at >= :cutoff`.
- `backend/app/services/ml_challenger_service.py:429`: `_build_dataset` chama `build_training_dataframe`.
- `backend/app/services/ml_challenger_service.py:458`: `_build_l3_dataset` tambem chama `build_training_dataframe`.
- `backend/app/ml/feature_extractor.py:390`: se `ttt_fast_win_bucket` existe, `WIN_0_15M`/`WIN_15_30M` vira label 1.
- `backend/app/ml/feature_extractor.py:399`: fallback usa PnL/fee threshold e `holding_seconds`.

Correcao habilitada: B2. Motivo: label atual viola a invariante do prompt: `ttt_analyzer` nao pode ser fonte de label.

### A2 - Metodo de split

Hipoteses: H0 temporal walk-forward; H1 random/estratificado.  
Veredito: H0 confirmado para o challenger, com ressalva: nao ha purge/embargo nesse caminho.

Evidencia:

- `backend/app/services/ml_challenger_service.py:388`: `ORDER BY created_at ASC`.
- `backend/app/services/ml_challenger_service.py:523`: `_chronological_split_with_test`.
- `backend/app/services/ml_challenger_service.py:526`: comentario: "60/20/20 temporal split".
- `backend/app/services/ml_challenger_service.py:545`: retorna fatias sequenciais `X[:train_end]`, `X[train_end:val_end]`, `X[val_end:]`.
- `backend/app/ml/feature_extractor.py:505`: trainer global tem `train_val_test_split` com purge/embargo; challenger nao usa essa funcao.

Correcao B4: bloqueada para troca de split, porque H1 nao confirmou. Recomendacao: portar purge/embargo do trainer global para o challenger em mudanca separada.

### A3 - Optuna

Veredito: ha holdout final, mas validacao acumula selecao de hiperparametros e threshold.

Evidencia de codigo:

- LGBM: objetivo Optuna retorna `roc_auc_score(y_val, preds)`; final threshold usa `np.median(val_preds)`.
- CatBoost: mesmo padrao de AUC em validacao e threshold pela mediana das predicoes de validacao.
- `backend/app/services/ml_challenger_service.py:523` separa test antes de treino; `metrics_json.test` e preenchido depois.

Correcao B6: habilitada somente depois de B2/B3/B5; manter test intocado.

### A4 - Gates e "Etapa4 council plan"

Pergunta central: como v50 virou ACTIVE com test AUC 0.5582?  
Resposta: o gate atual aceita `test_auc >= 0.55` e gap ate 0.15; v50 passou por margem minima.

Evidencia:

- `backend/app/ml/promotion_gate.py:21`: `DEFAULT_MIN_TEST_AUC=0.55`.
- `backend/app/ml/promotion_gate.py:23`: `DEFAULT_MAX_GENERALIZATION_GAP=0.15`.
- `backend/app/ml/promotion_gate.py:24`: `DEFAULT_MAX_TEST_FPR=0.55`.
- v50 `promotion_gate`: `test_roc_auc=0.5582307303`, `val_roc_auc=0.6890176707`, `test_fpr=0.1400742115`, status `APPROVED`.
- v52 `notes`: `promoted: Etapa4 council plan 2026-06-30`.
- Busca em `backend/` por `council|Etapa4|promote|promotion|ACTIVE|activate` nao encontrou fluxo padrao auditavel que altere o gate; ha endpoint/gate, mas nao mecanismo robusto de promocao baseado em holdout configuravel.

Correcao habilitada: B5.

### A5 - Exposicao em producao

Veredito: `L3_PROFILE` influenciou decisoes reais; B1 urgente.

SQL de exposicao:

```sql
SELECT model_lane, COUNT(*) AS n,
       COUNT(*) FILTER (WHERE score_status='OK') AS ok,
       COUNT(*) FILTER (WHERE used_by_gate=true) AS used_by_gate,
       MIN(ranked_at) AS first_ranked, MAX(ranked_at) AS last_ranked
FROM ml_opportunity_rankings
WHERE ranked_at >= NOW() - INTERVAL '7 days'
GROUP BY model_lane ORDER BY model_lane;
```

Resultado:

```json
[{"model_lane":"L3_PROFILE","n":1304,"ok":1304,"used_by_gate":1304,"first_ranked":"2026-06-30 12:23:35.864409+00:00","last_ranked":"2026-06-30 19:14:38.385349+00:00"}]
```

SQL complementar:

```sql
SELECT model_lane, ml_gate_enabled, COUNT(*) AS n,
       COUNT(*) FILTER (WHERE score_status='OK') AS ok,
       MIN(created_at) AS first_created, MAX(created_at) AS last_created
FROM decisions_log
WHERE created_at >= NOW() - INTERVAL '7 days'
GROUP BY model_lane, ml_gate_enabled ORDER BY model_lane, ml_gate_enabled;
```

Resultado:

```json
[
  {"model_lane":"L3_PROFILE","ml_gate_enabled":true,"n":1304,"ok":1304},
  {"model_lane":null,"ml_gate_enabled":false,"n":30952,"ok":0}
]
```

Consumo em codigo:

- `backend/app/tasks/pipeline_scan.py:3193`: L3 chama predictor com `model_lane="L3_PROFILE"`.
- `backend/app/tasks/pipeline_scan.py:3225`: L1 chama predictor com `model_lane="L1_SPECTRUM"`.
- `backend/app/tasks/pipeline_scan.py:3267`: decisao pos-ML usa `ALLOW`/`BLOCK`.

### A6 - `profile_id`: leakage/vies v50

Veredito: vies de selecao confirmado; `source_encoded` e constante no dataset L3 strict.

SQL top perfis:

```sql
SELECT profile_id::text, COUNT(*) AS n,
       COUNT(*) FILTER (WHERE outcome='TP_HIT') AS tp,
       ROUND(AVG(CASE WHEN outcome='TP_HIT' THEN 1.0 ELSE 0.0 END)::numeric,4) AS tp_rate,
       ROUND(AVG(pnl_pct)::numeric,4) AS avg_pnl
FROM shadow_trades
WHERE user_id='8080110c-ee9d-4a2b-a53f-6bef86dd8867'
  AND source='L3'
  AND created_at >= NOW() - INTERVAL '60 days'
  AND outcome IN ('TP_HIT','SL_HIT','TIMEOUT')
  AND pnl_pct IS NOT NULL
  AND features_snapshot IS NOT NULL
  AND features_snapshot::text <> '{}'
  AND profile_id IS NOT NULL
GROUP BY profile_id ORDER BY n DESC LIMIT 10;
```

Top 3: `0b71657c...` n=51 TP=31 TP rate 0,6078; `9f1663fa...` n=50 TP=36 TP rate 0,7200; `4355a790...` n=49 TP=28 TP rate 0,5714.

Cauda:

```json
{"profiles":25,"min_n":2,"p25":10.0,"p50":27.0,"p75":41.0,"max_n":51}
```

Comparacao incluido/excluido:

```json
[
  {"bucket":"excluded_null_profile_id","n":15288,"tp":5759,"tp_rate":"0.3767","avg_pnl":"-0.1255","avg_holding_seconds":"21984"},
  {"bucket":"included_profile_id","n":650,"tp":390,"tp_rate":"0.6000","avg_pnl":"0.2073","avg_holding_seconds":"6632"}
]
```

`source_encoded` constante:

```json
[{"source":"L3","n":650}]
```

Correcao B6: remover `source_encoded` quando constante e nao usar `profile_id_encoded` como atalho de epoca/perfil sem validacao robusta.

### A7 - Divergencia de label por lane

Veredito: drift operacional.

> ⚠️ SUPERSEDED (2026-07-15): o valor canônico de `ml_win_fast_threshold_seconds` é **14400** (`is_tp_4h_v1`), decisão formal do operador na Fase 1.2 (P2), coerente com o contrato D1=A / TP ATR-dinâmico 240 min. O `win_threshold_s=1800` do v50 abaixo reflete estado anterior ao contrato `shadow_atr_dynamic_v2` — o "drift" documentado aqui foi formalmente resolvido a favor de 14400. Ver `RELATORIO_FASE1_2_DEPLOY_CALIBRACAO_2026-07-15.md`.

Evidencia:

- Config ativa `config_type='ml'`: `ml_win_fast_threshold_seconds=14400`, `ml_dataset_valid_from=2026-06-14 21:33:10.277143+00`.
- v50 ativo original: `label=is_win_fast_v1`, `win_threshold_s=1800`.
- v52: `label=is_tp_4h_v1`, `win_threshold_s=14400`.
- `backend/sql/update_ml_label_to_tp_4h.sql` altera config para 14400.
- `backend/scripts/run_lgbm_retrain.py` e `backend/scripts/run_catboost_retrain.py` contem comentarios contraditorios entre 1800 e `is_tp_4h_v1`.

Correcao B2: habilitada.

### A8 - Fronteira limpa challenger

Veredito: violada. O challenger usa `datetime.now() - lookback_days`, nao `ml_dataset_valid_from`.

Evidencia de codigo:

- `backend/app/services/ml_challenger_service.py:386`: `AND created_at >= :cutoff`.
- `backend/app/services/ml_challenger_service.py:389`: parametro `cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)`.
- Nao ha `ml_dataset_valid_from` na query do challenger.

Reconstrucao v50 pelo `created_at` real do modelo:

```json
{
  "model_created_at":"2026-06-25 18:09:40.861663+00:00",
  "total":8244,
  "pre_boundary":1457,
  "post_boundary":6787,
  "null_profile":8244,
  "strict_profile":0
}
```

Observacao: a reconstrucao temporal por estado atual nao reproduz os 3.794/1.265/1.265 strict do v50 porque `profile_id` e/ou dados foram populados depois; isso reforca a necessidade de persistir `train_from`, `train_to`, `dataset_query_cutoff` e `dataset_hash`.

Correcao B3: habilitada.

### A9 - Contrato de dados

Veredito: contrato violado.

Requerido por codigo:

- `backend/app/ml/feature_extractor.py:17`: `BASE_FEATURE_COLUMNS` com 33 features.
- `backend/app/ml/feature_extractor.py:159`: `extract_features`.
- Chave ausente vira `NaN` por `_float(default=_nan)`.
- O challenger converte `NaN` para zero em `backend/app/services/ml_challenger_service.py:442` e `backend/app/services/ml_challenger_service.py:478`.

Cobertura requerida, resumo:

- L1: maioria das features tecnicas em ~97,4%-97,6%; `volume_24h_usdt` so 50,70%; features engenheiradas `flow_strength`, `trend_alignment`, `momentum_strength`, `delta_normalized`, `ema_distance_pct`, `ema50_distance_pct`, `ema200_distance_pct` nao aparecem no JSON porque sao computadas pelo extractor.
- L3: `taker_ratio` 98,71%, `volume_delta` 98,71%, `rsi` 99,97%, `adx` 99,97%; mas varias features do contrato ficam em ~10%-11%, e `volume_24h_usdt` apenas 7,06%.
- L3 extras ignorados: 71 chaves, incluindo macro/contexto como `dxy_value`, `vix_value`, `sp500_change_1h`, `fear_greed_index`, alem de campos de mercado nao usados.

Range/tipo:

- `atr_pct <= 0`: L1 9 ocorrencias; L3 4 ocorrencias.
- Booleanos (`ema9_gt_ema21`, `ema50_gt_ema200`, `vwap_reclaim_bool`, `higher_highs_5`, `higher_lows_5`) foram marcados como nao numericos pela checagem SQL generica, mas o extractor trata booleano corretamente como 1.0/0.0.
- Nao foram encontrados `NaN`/`Infinity` textuais nas features testadas.

Paridade entrada/saida:

- `shadow_trades` contem `features_snapshot` e campos pos-entrada: `features_snapshot_exit`, `exit_metrics_json`, `mae_pct`, `mfe_pct`, `max_profit_*`, `ttt_*`.
- O `BASE_FEATURE_COLUMNS` nao inclui esses nomes pos-entrada. Leakage por coluna de saida em X nao foi confirmado.

IDs das 20 amostras fixas consultadas:

L1:

```text
36e0d228-8fc3-44d1-90cf-6b0c79a08e6e
1bc5a725-e784-4ae0-b600-867b45ac588d
3ba4ef9e-1dbf-465f-9ff4-e4829d5328ec
5570a259-071d-4632-a843-fcdb9dc31d57
004ea574-20ad-4e8e-b412-fc59224aa5b8
f6dffac8-1dd0-48a5-89cb-0dc5ff531898
6fc3b05c-931e-4569-9e79-e2c9799f193d
80b255e2-dd35-4c83-86aa-4d34db191579
f90f5d6b-7289-444a-aa7c-c3c6e62d73de
df433883-2925-4a95-8d37-fb0e734a0758
```

L3:

```text
41a0b33f-d977-461e-90ca-89d913b49120
7f8a3d7b-452c-4b22-9d2f-deb1b9d9b87c
4ffbae5c-b4f3-4e74-aad1-83b0f35c5808
039ba549-2a6e-4e36-a909-d212ee9d1c7f
463f5894-e228-4f39-9cb6-9bd848179829
084f7b00-c157-4c43-9277-c4ab5eddb31f
3a80c4eb-beed-470e-98ff-1a942dc193b3
5d48da55-93e2-4b6d-ae51-1f4f6ce01e03
ba622df9-3c07-4315-990b-6a341b674b6d
e75711d3-8980-416f-a66d-4dc8598da43b
```

Limitacao: os `features_snapshot` completos foram consultados e inspecionados no terminal, mas nao foram colados integralmente aqui para manter o relatorio manejavel. A amostra L3 mostra snapshots macro/minimos no inicio da fronteira; a amostra L1 mostra snapshots tecnicos completos, com `volume_24h_usdt` presente nas primeiras linhas e ausente nas seguintes.

## Status das correcoes B2-B8

| Correcao | Status | Motivo |
|---|---|---|
| B2 label v2 | Nao executada | Requer alteracao de `feature_extractor.py`, formalizacao por config e retreino; A1/A7 habilitam, mas ainda sem migracao/config de contrato v2. |
| B3 fronteira limpa | Nao executada | A8 habilita; requer mudanca em `ml_trainer/job.py` e `MLChallengerService` lendo `config_profiles` sem hardcode. |
| B4 split temporal | Nao executada | H0 confirmado no challenger; restaria melhoria de purge/embargo, nao troca emergencial. |
| B5 gates holdout/config | Nao executada | A4 habilita; exige mover thresholds para `config_profiles` e tornar proveniencia obrigatoria. |
| B6 retreino v2 | Bloqueada | Depende de B2-B5 e de contrato A9/B8. |
| B7 proveniencia fail-closed | Nao executada | Schema ja tem colunas, mas populacao/fail-closed precisa alteracao de codigo e testes. |
| B8 contrato fail-closed | Nao executada | A9 habilita; requer config `ml_feature_contract`/ranges e validação no builder. |

## Status final das lanes

| Lane | Modelo | Status apos auditoria | Observacao |
|---|---|---|---|
| L3_PROFILE | v50 CatBoost | `rejected` | Rebaixado por B1; nao deve gatear novas decisoes como active. |
| L1_SPECTRUM | v52 LightGBM | `active` | Mantido por ora; test AUC 0,6532 e recall 0,7372, mas requer substituicao via retreino v2. |

## Impacto no canary

- L3 nao deve avancar canary com v50: lane sem modelo ativo confiavel ate candidate v2 passar gates holdout.
- L1 pode permanecer como incumbente temporario, mas o canary deve considerar risco de label legado misto ate B2/B3/B5/B7/B8.
- Dataset limpo estimado por fonte pos-fronteira: `L1_SPECTRUM` 2.057 linhas elegiveis; `L3` 14.481 linhas elegiveis, apenas 650 com `profile_id` no recorte recente consultado.
- A9 indica que um contrato fail-closed reduziria fortemente L3 se exigir todas as features tecnicas atuais; o contrato deve distinguir obrigatorias/opcionais por lane antes do retreino.

## Questoes nao respondidas integralmente

1. A9.6 nao esta integral no formato exigido pelo prompt: os 20 `features_snapshot` completos foram consultados, mas o Markdown registra os IDs fixos e resumo, nao o JSON completo de todos.
2. B2-B8 nao foram executadas nesta rodada porque envolvem mudancas estruturais e/ou retreino dependentes das proprias evidencias A1-A9. Executar essas mudancas sem config/migracao/testes violaria os invariantes "ZERO HARDCODE" e "nenhuma correcao B antes da A correspondente".
3. O mecanismo exato humano/automatizado do texto "Etapa4 council plan" nao foi localizado em codigo executavel; a evidencia disponivel e a nota persistida no v52 e a ausencia de fluxo auditavel encontrado por `rg`.
