# RELATÓRIO FASE 1.5 — 2026-07-15 ~20:45 UTC

Contrato: `PROMPT_FASE1_5_PRONTIDAO_RETRAIN.md`. Estado: **concluído e deployado.** Ordem P1→P2→P3→P4 cumprida (P2 informou P3).

## P1 — I12 coerência de valor + ATIVAÇÃO DO CONTRATO v2 (bloqueante)

**Achado (bloqueante):** `[query]` todos os elegíveis pós-valid_from tinham contrato **v1** (`shadow_atr_dynamic_v1`, tp_pct_applied=**0,600 fixo**), não o **v2** que o D1=A decidiu. Causa `[file]`: a chave `shadow_atr_multiplier_tp` **nunca foi gravada** → `_resolve_barrier_contract_version` carimba v1 e o TP fica fixo em 0,6% (o artefato estrutural que o D1=A eliminaria). Todo o dataset até então era v1.

**Decisão do operador:** **ativar v2** `[operador]`. Escritas de config: `+shadow_atr_multiplier_tp=1.5` (D4), `+ml_active_barrier_contract_version=shadow_atr_dynamic_v2`; produção confirmada `[query]` carimbando v2 (tp ATR-escalado com clamp, ≠ 0,6). valid_from re-bumpado p/ **2026-07-15T20:20:53Z** (canônico = só v2).

**I12 estendido** `[file]`: além de NOT NULL, FAIL quando (b) linha ATR_DYNAMIC não carrega o contrato ativo (`:active_contract_version`, de config, nunca literal) ou (c) tp/sl fora dos clamps D4 (`:clamp_min/:clamp_max`, de config). Escopo mudado p/ `entry_timestamp >= :valid_from`. Helpers fail-closed `_require_str_config`/`_require_float_config`.

**Prova de detecção de valor (1.3)** `[query]`: sobre a população v1 antiga o I12 **detectou 146 violações** (linhas ATR_DYNAMIC-v1); sobre a v2 nova, **0**. Testes: `test_i12_coherence_params_are_config_driven`, `test_i12_fail_closed_without_active_contract_version` + suíte (24 passed). Deploy compute worker. **1.4 prod: 0 violações na população canônica.**

## P2 — cadeia de retrain (READ-ONLY)

**Cadeia** `[file]`: `run_lgbm_retrain.py:137` → `MLChallengerService.train_challengers` → `_chronological_split_with_embargo:2393` (usa `grouped_purged_split` p/ **UM** split leak-free 60/20/20; purge/embargo=`ml_split_embargo_seconds=14400` de config) → `_train_lgbm_sync:2422` (Optuna) → `_save_to_db` → `evaluate_promotion_gate:1846`.

**Módulos Codex na cadeia:** `grouped_purged_split` (split único leak-free, NÃO k-fold), `model_governance` (autoridades pós-gate), `feature_contract_v2`/`native_capture_governance` (captura). `economic_targets` **NÃO** na cadeia.

**Veredito 2.2a = 🟡 Caso B** `[file:438-460]`: a seleção Optuna roda sobre o **X_va fixo único** (`model.predict(X_val)`), **não sobre folds**. É o mecanismo do defeito do v80, **mitigado** (split leak-free + val ~600 a 3.000 elegíveis, não 32) mas **não resolvido** (sem CV).

**Achados 2.3:** IMPORTANTE — seleção em val fixo (Caso B; rede: promotion gate `max_val_test_gap=0.05` + `min_test_auc=0.6` rejeitariam o v80). IMPORTANTE — Optuna **não-determinístico** (`create_study()` sem seed). NOTA — `timeout=180` hardcoded. NOTA — `economic_targets` não wired.

**Gate de promoção** `[query]`: config-driven e não-trivial (`min_test_auc=0.6`, `max_val_test_gap=0.05`, `max_test_fpr=0.5`, `min_test_samples=300`, `require_positive_net_ev=true`).

## P3 — gates de aprovação (Caso B → opção degradada explícita `[operador]`)

**Escolha do operador:** aceitar o split único leak-free + **gate estatístico duro no test** (sem implementar CV antes de 22/07). Config (verificadas ausentes, gravadas): `ml_approval_test_auc_ci_excludes_half=true`, `ml_approval_auc_ci_level=0.95`, `ml_approval_min_distinct_days=5`, `ml_approval_bootstrap_iterations=2000`, `ml_training_seed=42`.

**Gates (config-driven, fail-closed)** `[file promotion_gate.py]`:
- CI bootstrap do AUC de teste **exclui 0.5** (limite inferior do IC > 0.5) — pega o mecanismo do v80 (ponto alto, IC largo). `min_distinct_days=0` desliga o gate.
- **cobertura temporal mínima** (dias distintos no test).
- Treino `[file]`: `_bootstrap_auc_ci_low` (determinístico) grava `roc_auc_ci_low`; caller grava `distinct_days` do split.

**Determinismo (achado do P2):** Optuna `TPESampler(seed)` + LightGBM `seed/bagging_seed/feature_fraction_seed/deterministic` — seleção reproduzível. `timeout` via config (era hardcoded). Testes: `test_fase15_approval_gates` (9) + `test_promotion_gate` atualizado. **58 passed.** Deploy compute worker + API.

## P4 — capture / gate econômico

**Hipótese confirmada** `[query/file]`: `capture` = coluna `win_fast_capture_rate` = **recall do positivo (win rápido) no test** (`trainer.py:497`). Computada no trainer **legado**, persistida (coluna) e exibida (`frontend/app/ml-models/page.tsx:59`), mas o **path atual `MLChallengerService` não a gravava** → NULL (`—`) em v74-v81. É **(b/c): definida+persistida+exibida, mas não computada pelo path atual.**

**Correção** `[operador]` `[file]`: `_save_to_db` grava `win_fast_capture_rate = test_metrics["recall"]` (valor já computado; semântica idêntica ao legado, não inventada). Teste `test_save_to_db_persists_capture_as_test_recall`. Deployado.

## PROJEÇÃO
`[query]` valid_from v2 = 2026-07-15T20:20:53Z; elegíveis v2 acumulados = 3 (recém-recomeçado); taxa sustentada ~278-418/dia elegível `[calc]`. **Gate 3.000 ≈ 2026-07-22 a 07-26** (o re-bump p/ v2 recomeçou a contagem — custo consciente da ativação D1=A).

## STOPs / [NÃO VERIFICADO]
- P1 — decisão contrato v1/v2 = ativar v2.
- P2 2.2a veredito = Caso B (com evidência de código).
- P3 escopo = degradada explícita (val fixo + gates duros).
- P4 hipótese = (b/c) + correção = gravar test recall.

## DECLARAÇÃO
Escritas: config (P1: shadow_atr_multiplier_tp, ml_active_barrier_contract_version, re-bump valid_from; P3: 5 chaves de aprovação/seed); código (I12 coerência, promotion_gate estatístico, determinismo Optuna/LGBM, win_fast_capture_rate) — commits `f9f4016`, `b1e3ced`, `427a706`. `shadow_trades` **READ-ONLY** confirmado (zero DELETE/UPDATE nesta fase). **NENHUM retrain executado.** Nenhum modelo promovido/demovido. Nenhum backfill. Todos os deploys com `git status` limpo (regra 6, vigente desde `45813a3`).
