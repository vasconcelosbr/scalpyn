# Retreino Nº 2 — Pendências Pré-Treino (R1–R4) e Gate de Marco

Data: 2026-07-05
Executor: Claude (conforme `PROMPT_RETREINO_2_FINAL.md`)
Status: **AGUARDANDO MARCO** — R1–R4 executados e provados; R5 **NÃO executado** (gate de marco: elegíveis `2457 < 2800`). A tabela de veredito pré-registrada permanece IMUTÁVEL e não consumida; R5+R6 (veredito) executam na próxima rodada deste mesmo prompt.

---

## Sumário

| Fase | Resultado |
|---|---|
| R1 | Optuna 100% config-driven, fail-closed; seleção de trial por **EV líquido de validação** (implementada, não ficou em AUC); smoke 3 passed sem tocar test |
| R2 | Suíte ML completa **153 passed / 0 failed** — 12 testes atualizados (7 label v2 + 1 registry + 1 macro patch + 3 paths de migration), zero asserção enfraquecida, zero skip silencioso |
| R3 | TIMEOUT precede TP/SL para trades vencidos (`_resolve_expired_timeout`); 7 testes unitários passed |
| R4 | Checklist completo; contrato vigente colado (25 required / 8 optional); marco NÃO atingido → PARAR |
| R5 | NÃO EXECUTADO — proibido treinar abaixo do marco. Test set intocado; zero consumo do holdout |
| R6 | Este relatório. Reprojeção do marco: **2026-07-06/07** (inflow observado) a **2026-07-08** (premissa conservadora) |

Commit do código: `45c1fff`.

---

## R1 — Optuna reduzido via config (fail-closed)

### 1.1 Consumo (diff `path:line`, `backend/app/services/ml_challenger_service.py`)

- `:217` — `_suggest_params_from_space(trial, search_space)`: constrói `suggest_int`/`suggest_float(log=)` a partir do JSONB da config; type inválido → `ValueError`. Zero range em código.
- `:257` — `_train_lgbm_sync` fail-closed: `raise ValueError("missing_ml_optuna_search_space_lightgbm")` quando o espaço não é fornecido.
- `:1355-1372` — lane LightGBM em `train_challengers`: lê `ml_optuna_max_trials` (ausente → `missing_ml_optuna_max_trials`) e `ml_optuna_search_space.lightgbm` (ausente → `missing_ml_optuna_search_space_lightgbm`) ANTES de montar o dataset; erro aborta a lane com mensagem — mesmo padrão da fronteira `ml_dataset_valid_from`.
- Proveniência: `metrics.trial_selection_objective` e `metrics.optuna_search_space` gravados no modelo (`:355-356`).

### 1.2 Espaço conservador gravado em config (anterior vs novo, lado a lado)

Anterior = hardcoded nas linhas 244-252 (removidas). Novo = `ml_optuna_search_space.lightgbm` (config, RETURNING colado na execução):

| Hiperparâmetro | Anterior (código) | Novo (config) | Direção |
|---|---|---|---|
| n_estimators | 100–600 | 100–400 | menos capacidade |
| learning_rate | 0.01–0.30 log | 0.02–0.10 log | estreito |
| num_leaves | 15–127 | 15–63 | menos folhas |
| min_child_samples | 10–100 | 20–100 | mais regularização |
| feature_fraction | 0.4–1.0 | 0.5–0.9 | menos variância |
| bagging_fraction | 0.4–1.0 | 0.5–0.9 | menos variância |
| bagging_freq | 1–7 | 1–7 | inalterado |
| reg_alpha | 1e-8–10 log | 1e-3–10 log | piso de regularização ↑ |
| reg_lambda | 1e-8–10 log | 1e-3–10 log | piso de regularização ↑ |

`ml_optuna_max_trials=15` (gravado no fechamento, RETURNING `15`; vs default anterior `N_TRIALS_LGBM=30` via env, `ml_challenger_service.py:47`).

### 1.3 Seleção do trial por EV líquido — IMPLEMENTADA

O objective (`:279-297`) agora calcula, por trial, a curva de EV de validação via `_calibrate_ev_threshold` (o MESMO helper do threshold final) e retorna o melhor EV líquido — não val AUC. Trial sem threshold elegível (min_positives) retorna `-inf` (trial ruim, study continua). O insumo (`val_returns`) já era computado por build — refactor pequeno, sem limitação residual a registrar. Test set jamais é lido no objective.

### 1.4 Prova (smoke sem tocar test)

`backend/tests/test_ml_optuna_config.py` — dataset sintético, val apenas, `X_test=None`:
```
tests/test_ml_optuna_config.py::TestFailClosed::test_missing_search_space_aborts PASSED
tests/test_ml_optuna_config.py::TestFailClosed::test_invalid_type_in_space_aborts PASSED
tests/test_ml_optuna_config.py::TestSmokeConfigDriven::test_n_trials_and_space_from_config_ev_selection PASSED
3 passed in 2.55s
```
O smoke assere: `metrics.n_trials==2` (propagado da "config"), `trial_selection_objective=='net_ev'`, `optuna_search_space` idêntico ao fornecido, todos os `best_params` dentro dos ranges, `test_metrics=={}` (test não consumido).

---

## R2 — Higiene de testes (suíte ML verde)

### Classificação por teste (12 atualizações; nenhuma OBSOLETO-removido, zero skip)

| Teste | Classificação | Ação |
|---|---|---|
| `TestTttLabelPriority` (7 testes) | ATUALIZAR — asseriam prioridade TTT-bucket + fallback pnl, mecanismo removido deliberadamente pelo label v2 | Reescritos como `TestLabelV2SimOutcome` (7 testes): bucket IGNORADO, pnl não define label, TP lento=0, TP rápido=1, pnl NULL derruba linha, positive_rate por outcome+holding. Mesma força; mudanças intencionais asseridas explicitamente (ex.: pnl 0.5% + TP rápido agora é 1) |
| `test_14400s_maps_to_is_tp_4h_v1` | ATUALIZAR — registry renomeado para v2 | Assere `is_tp_4h_v2_sim_outcome`; docstring registra v1 como alias legado de leitura |
| `test_predict_proba_exception_returns_fail_closed_contract` | ATUALIZAR — patch de `fetch_macro_context` inexistente (enriquecimento macro removido da inferência) | Patch removido; contrato fail-closed asserido com a mesma força |
| `test_migration_111/112` (2), `TestMigration106` (4), `TestMigration105...` (1) | ATUALIZAR — migrations pré-baseline movidas para `alembic/versions/legacy/` (repo com `000_baseline_prod_schema`) | Paths corrigidos para `legacy/`; asserções de DDL inalteradas |

### Suíte ML completa (13 arquivos)

```
python -m pytest tests/test_ml_correction_plan_june30.py tests/test_ml_dataset_config.py
  tests/test_ml_directional_features.py tests/test_ml_optuna_config.py
  tests/test_shadow_timeout_precedence.py tests/test_pi_ml_challenger_flags.py
  tests/test_ml_lane_eligibility.py tests/test_p0_ml_corrections.py
  tests/test_ml_gate_fail_closed_audit.py tests/test_ml_gate_blocked_decision_persistence.py
  tests/test_ml_opportunity_ranking_producer.py tests/test_ml_prediction_probability_adapter.py
  tests/test_shadow_ml_lineage.py -q
153 passed in 4.10s
```
Nota: além das 7 falhas conhecidas, a varredura completa revelou mais 5 pré-existentes (registry, macro patch, 3 paths de migration em outros arquivos) — todas corrigidas acima. Antes: 12 failed / 141 passed. Depois: **153 passed**.

---

## R3 — TIMEOUT precede TP/SL para trades vencidos

Evidência-gatilho (F2 do encerramento): 48 TP_HITs com holding 24–31h em 2026-07-03 — trades além da janela fechados como TP a preço corrente porque o live-close precedia qualquer noção de expiração.

### Diff (`backend/app/tasks/shadow_trade_monitor.py`)

- `:122-156` — `_resolve_expired_timeout(shadow, mm_price, ohlcv_price, entry_price, now_utc)`: para `elapsed >= timeout_candles` retorna o exit_price (precedência mm > ohlcv > entry, a mesma do timeout-elapsed); senão `None`. Normaliza `entry_timestamp` naive → UTC.
- `:1013-1031` — em `_advance_shadow`, o check roda DEPOIS da atualização MAE/MFE (observacional preservada) e ANTES da determinação de `live_outcome`: trade vencido → `_finalize_outcome(shadow, "TIMEOUT", ...)` e retorna, sem nunca avaliar barreira a preço corrente. Log `timeout-precedence`.

Racional: sem candles 1m no banco (provado no F2: só 5m/30m), o cruzamento histórico é indeterminável — um "TP corrente" pós-janela é epistemicamente ambíguo e infla win-rate/EV. TIMEOUT é o veredito honesto do contrato. **Labels v2 não são afetados** (holding > 4h ⇒ label 0 em qualquer caso); a correção protege win-rate/EV daqui em diante.

### Teste

`backend/tests/test_shadow_timeout_precedence.py` — inclui o caso pedido (vencido 28h com preço corrente ≥ TP → `outcome == 'TIMEOUT'`, exit a preço corrente e não a tp_price) + boundary (`==` vence), precedência de exit price, não-vencido preserva live-close, naive timestamp:
```
7 passed in 1.48s
```

---

## R4 — Pré-voo e gate de marco

### 4.1 Checklist

| Item | Prova |
|---|---|
| R1 consumido | Smoke 3 passed (log do espaço via `[MLChallenger] Optuna(R1): n_trials=... search_space=...` em `:262-265`); fail-closed testado |
| R2 suíte verde | `153 passed in 4.10s` (output acima) |
| R3 aplicado | `7 passed` (output acima) |
| Fronteira fail-closed | `test_ml_dataset_config.py` incluído na suíte (3 testes) — verde |
| Zero `active` | `SELECT status, COUNT(*) FROM ml_models GROUP BY 1` → `candidate 31 / rejected 18 / retired 17` (0 active). Nota: rejected 17→18 vs 2026-07-04 — trainer noturno de profile auto-rejeitou 1 modelo novo; zero active mantido |
| `ml_forward_scoring_enabled` | `false` [query] |
| Exclusão F3 | `ml_feature_exclusion_apply=true`, proposta `["trend_alignment", "ema50_gt_ema200"]` [query] |
| Optuna config | `ml_optuna_max_trials=15` + `ml_optuna_search_space.lightgbm` (9 hiperparâmetros) [query] |
| Force-close | `shadow_max_open_age_hours=48` [query] |

### 4.2 Contrato vigente e resolução da discrepância (25/8 vs "28 required do E7")

Contagem autoritativa hoje (`jsonb_array_length` na config ativa):
```
 l1_required | l1_optional | l3_required |          updated_at
-------------+-------------+-------------+-------------------------------
          25 |           8 |           4 | 2026-07-03 21:00:50.914721+00
```
**L1_SPECTRUM vigente — required (25):** taker_ratio, volume_delta, rsi, macd_histogram_pct, macd_histogram_slope, adx, adx_acceleration, spread_pct, volume_spike, bb_width, atr_pct, ema9_gt_ema21, ema50_gt_ema200, orderbook_depth_usdt, vwap_distance_pct, rsi_slope_3, rsi_slope_5, macd_hist_slope_3, macd_hist_slope_5, ema21_ema50_distance_pct, di_plus_minus_diff, adx_slope_3, vwap_reclaim_bool, higher_highs_5, higher_lows_5.
**Optional (8):** volume_24h_usdt, flow_strength, trend_alignment, momentum_strength, delta_normalized, ema_distance_pct, ema50_distance_pct, ema200_distance_pct. Total 33 = feature set completo.

**Resolução da discrepância:** o relatório do E7 (sessão 3, 2026-07-03) escreveu `"required": ["taker_ratio","volume_delta","rsi","macd_histogram_pct","adx",...28 total]` — lista ELIDIDA com contagem não verificável, e o próprio relatório declara que a config "já existia" (E7 só implementou o enforcement). Não há histórico de mudança do contrato: `config_profiles` não tem tabela de versões e `updated_at` (2026-07-03 21:00:50 = timestamp do H4) não é tocado pelos UPDATEs raw posteriores. As leituras verbatim de 2026-07-04 (encerramento, jsonb_pretty completo) e de hoje dão **25/8 idênticos**. Conclusão: **erro de contagem no texto do relatório E7; o vigente — usado pelo E9 e pelo funil do R5 — é 25 required / 8 optional**. [inferência: sem histórico de config, a hipótese alternativa (contrato editado entre 03 e 04/jul sem registro) não tem evidência e nenhuma sessão documentou tal edição]

⚠️ Nota para o R5: com `ml_feature_exclusion_apply=true`, `ema50_gt_ema200` (required) e `trend_alignment` (optional) saem das FEATURES DE TREINO (33→31), mas o contrato de LINHAS continua exigindo `ema50_gt_ema200` não-NaN — a exclusão remove a coluna do X, não relaxa a validação de completude da linha. Comportamento intencional (a linha continua íntegra; a feature só não é aprendida).

### 4.3 Gate de marco → PARAR

```
SELECT COUNT(*) FROM shadow_trades WHERE source='L1_SPECTRUM'
  AND outcome IN ('TP_HIT','SL_HIT','TIMEOUT')
  AND created_at >= '2026-06-14 21:33:10.277143+00'
  AND created_at >= NOW() - INTERVAL '30 days';
-- elegiveis: 2457
```
Marco: `ml_retrain_min_eligible_rows = 2800` [query]. **2457 < 2800 → R5 NÃO executa.** Test set intocado; nenhum treino realizado.

---

## R5 — NÃO EXECUTADO (AGUARDANDO MARCO)

Déficit: 2800 − 2457 = **343 elegíveis**. Inflow diário (por `created_at`, últimos 6 dias completos):
```
 2026-06-29 | 224   2026-06-30 | 166   2026-07-01 | 329
 2026-07-02 | 255   2026-07-03 | 178   2026-07-04 | 164
```
Média = 1316/6 ≈ **219/dia** [calc] → marco em ~1,6 dias ≈ **2026-07-06/07**. Premissa conservadora (~109/dia) → ~3,1 dias ≈ **2026-07-08**.

**Data reprojetada do marco: 2026-07-06 a 2026-07-08.** R5+R6 (retrato do dataset, painel diagnóstico `diag_mp30`/`diag_cleanwin` com pré-validação da fonte, treino, gate, veredito literal contra a tabela pré-registrada e comparação com candidate nº 1 sob disciplina de IC) ficam para a próxima execução deste mesmo prompt — nada da preparação precisa ser refeito.

---

## Pendências para a próxima execução

1. Rodar o gate de marco (mesma query); se ≥ 2800 → R5 completo (retrato + painel diagnóstico + treino + gate + veredito R6).
2. Veredito: citar LITERALMENTE a linha da tabela pré-registrada; comparação com candidate nº 1 (test AUC 0,5784 / net EV −0,373 / gap 0,111) com ressalva de IC ±0,05 (~560 test samples) — diferenças dentro do IC são indistinguíveis.
3. `ml_forward_scoring_enabled` permanece `false` mesmo se APPROVED — go-live é decisão humana.
4. Deploy: o código R1/R3 (commit `45c1fff`) precisa estar em produção antes do treino se o retreino rodar via Railway (localmente o repo já contém).

## Ledger de evidências

| NÚMERO | ORIGEM | VALOR LITERAL DA FONTE |
|---|---|---|
| elegíveis = 2457 | [query] | `2457` |
| marco = 2800 | [config: ml] | `ml_retrain_min_eligible_rows: 2800` |
| inflow ≈ 219/dia | [calc] | (224+166+329+255+178+164)/6 = 219,3 — insumos colados |
| suíte ML = 153 passed | [pytest] | `153 passed in 4.10s` |
| smoke R1 = 3 passed | [pytest] | `3 passed in 2.55s` |
| teste R3 = 7 passed | [pytest] | `7 passed in 1.48s` |
| contrato L1 = 25/8 | [query] | `25 | 8 | 4` (jsonb_array_length) |
| zero active | [query] | `candidate 31 / rejected 18 / retired 17` |
| n_trials anterior = 30 | [código] | `N_TRIALS_LGBM = ... "30"` (`ml_challenger_service.py:47`) |
| ranges anteriores | [código] | linhas 244-252 pré-diff (removidas no commit `45c1fff`) |
| "28 total" E7 | [doc] | `execucao-b2-b8-sessao3-2026-07-03.md` linha 68 (lista elidida) |
