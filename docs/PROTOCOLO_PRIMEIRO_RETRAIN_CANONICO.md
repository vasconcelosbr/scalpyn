# Protocolo do Primeiro Retrain Canônico (Lane 1 / LightGBM)

> Documento executável. **Não dispara retrain.** Fonte única dos valores: `config_profiles(config_type='ml')`.
> Todos os thresholds abaixo são `[query]` (copiados da config ativa em 2026-07-16) — reconferir antes de executar.

## 0. Escopo

- Alvo: **Lane 1 — LightGBM / `L1_SPECTRUM` / label `is_tp_4h_v2_sim_outcome`**.
- Entrypoint: `backend/scripts/run_lgbm_retrain.py` (standalone, sem rede interna Railway).
- **Não** promove modelo (nasce sempre `candidate`), **não** altera outcomes/shadow_trades/estratégias/Auto-Pilot.

## 1. Pré-condições verificáveis (TODAS obrigatórias)

Cada item tem o comando de checagem. Se qualquer um falhar → **não executar**.

| # | Pré-condição | Comando de checagem | Passa quando |
|---|---|---|---|
| PC1 | Marco de elegíveis atingido | `python backend/scripts/run_lgbm_retrain.py --dry-run` | `records >= ml_retrain_min_eligible_rows` (**3000** `[query]`); dry-run retorna `status != "skipped"` |
| PC2 | Certificação GREEN **e recente** na última run | `SELECT status, run_at, EXTRACT(EPOCH FROM (now()-run_at))/3600 AS idade_h FROM ml_data_certification_runs ORDER BY run_at DESC LIMIT 1;` OU `GET /api/ml/readiness/latest` | `status='GREEN'` **E** `status_effective != 'STALE'` (idade < `ml_readiness_staleness_threshold_hours`=**3** `[query]`). Job morto/velho ⇒ STALE/JOB_ERROR ⇒ **bloqueado** (Fase 1.7). Nunca usar uma run velha como "atual". |
| PC3 | I12 = 0 violações de contrato de barreira | inspecionar `invariants->'I12'` da última cert run | `I12` sem violações (0 linhas de contrato divergente na janela) |
| PC4 | Chaves de aprovação presentes | query PC4 abaixo | as 8 chaves presentes (todas `[query]` presentes hoje) |
| PC5 | Seed determinística confirmada | `SELECT config_json->'ml_training_seed' ...` | `ml_training_seed = 42` `[query]` |
| PC6 | `git status` limpo | `git status --porcelain` | saída vazia |

**Query PC4 — chaves de aprovação (fail-closed no promotion gate):**
```sql
SELECT key, config_json ? key AS present, config_json->key AS value
FROM config_profiles,
  LATERAL (VALUES
    ('ml_promotion_min_test_auc'),           -- 0.6
    ('ml_promotion_min_test_samples'),       -- 300
    ('ml_promotion_max_val_test_gap'),       -- 0.05
    ('ml_promotion_max_test_fpr'),           -- 0.5
    ('ml_promotion_require_positive_net_ev'),-- true
    ('ml_approval_test_auc_ci_excludes_half'),-- true
    ('ml_approval_min_distinct_days'),        -- 5
    ('ml_retrain_min_eligible_rows')          -- 3000
  ) AS k(key)
WHERE config_type='ml' AND is_active=true;
```

## 2. Comando exato de disparo

```bash
# De DENTRO do repo (raiz do projeto). DATABASE_PUBLIC_URL do Railway (Postgres público).
export DATABASE_URL="postgresql+asyncpg://<...>@zephyr.proxy.rlwy.net:23422/railway"
# opcional: ML_CHALLENGER_LOOKBACK_DAYS (default 60)
python backend/scripts/run_lgbm_retrain.py            # persiste candidate em ml_models
# validar antes SEM persistir:
python backend/scripts/run_lgbm_retrain.py --dry-run  # só carrega dataset e checa o marco
```

- Roda **localmente** ou em qualquer host com acesso ao Postgres público (o script não depende de hostname interno Railway).
- `USER_ID` fixo no script: `8080110c-ee9d-4a2b-a53f-6bef86dd8867`.
- Win threshold vem **exclusivamente** de `ml_win_fast_threshold_seconds` (config) — nunca literal.
- Optuna: `ml_optuna_max_trials=15` `[query]`, espaço de busca em `ml_optuna_search_space.lightgbm`; seleção de trial por **EV líquido de validação** (fail-closed se o espaço não estiver na config).

## 3. Critérios de aceite do candidato (promotion gate — valores vigentes `[query]`)

O modelo nasce `candidate`. Para ser elegível a `active`, o promotion gate (`app/ml/promotion_gate.py`) exige TODOS:

| Gate | Chave de config | Valor vigente | Regra |
|---|---|---|---|
| AUC de teste ≥ mínimo | `ml_promotion_min_test_auc` | **0.6** | `test.roc_auc >= 0.6` E `> 0.5` (piso absoluto) |
| Amostras de teste ≥ mínimo | `ml_promotion_min_test_samples` | **300** | `test.samples >= 300` |
| Gap de generalização | `ml_promotion_max_val_test_gap` | **0.05** | `abs(val_auc - test_auc) <= 0.05` |
| FPR de teste ≤ máximo | `ml_promotion_max_test_fpr` | **0.5** | `test.fpr <= 0.5` |
| Net EV positivo | `ml_promotion_require_positive_net_ev` | **true** | `test.net_ev > 0` |
| CI bootstrap exclui 0.5 | `ml_approval_test_auc_ci_excludes_half` | **true** | `roc_auc_ci_low > 0.5` (bootstrap, `_bootstrap_auc_ci_low`, 2000 iters) |
| Dias distintos ≥ mínimo | `ml_approval_min_distinct_days` | **5** | `test.distinct_days >= 5` (0 desliga o gate — não usar 0) |
| Capture preenchido | — | — | `win_fast_capture_rate` / métricas de captura persistidas em `metrics_json` (não NULL) |

Qualquer chave ausente → gate retorna `BLOCKED` (fail-closed), status ≠ APPROVED.

## 4. O que NÃO fazer

1. **Sem retrain se a certificação estiver RED ou YELLOW.** Só GREEN (PC2). Hoje = RED.
2. **Sem promoção manual bypassando o gate.** Nunca `UPDATE ml_models SET status='active'` por SQL — precedente v52 (promovido por migration burlando o gate) já causou modelo anti-preditivo em produção. Promoção só via `_transition_model_status` após `evaluate_promotion_gate` → APPROVED.
3. **Sem reuso do test set para decisões de tuning.** Optuna seleciona por **EV de validação**, nunca por teste. O test set é tocado uma única vez, no gate final.
4. **Rotação obrigatória do hold-out.** Máximo de candidatos avaliados contra o MESMO test set antes de rotacionar para um hold-out temporal fresco:
   - **Proposta: `ml_max_candidates_per_holdout = 3`** (nova chave em `config_profiles(config_type='ml')`).
   - Motivo: cada candidato comparado contra o mesmo teste consome independência estatística (peeking / múltiplas comparações). Após 3 rejeições, avançar o `ml_dataset_valid_from` / test window para dados não vistos.
   - 🛑 **Operador confirma o valor `3` antes de gravar a chave.**

## 5. Referências

- Entrypoint: `backend/scripts/run_lgbm_retrain.py`
- Gate: `backend/app/ml/promotion_gate.py` (`evaluate_promotion_gate`, `REQUIRED_CONFIG_KEYS`)
- Treino/seleção: `backend/app/services/ml_challenger_service.py` (`_train_lgbm_sync`, `_bootstrap_auc_ci_low`, `_suggest_params_from_space`)
- Certificação: `backend/app/services/ml_data_certification_service.py` (`run_certification`, tabela `ml_data_certification_runs`)
