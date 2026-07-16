# RELATÓRIO FASE 1.6 — INTEGRIDADE DO LOOP — 2026-07-16 ~03:40 UTC

Branch `feat/fase1-integridade-monitoracao`. Evidência DB via `DATABASE_PUBLIC_URL` (read-only). Testes via `pytest` local.

## P1 — Fail-open do resolver de barreiras → fail-closed

**Fallbacks mapeados** (`backend/app/services/shadow_trade_service.py`):
- `_apply_barrier_params:94-98` — `shadow_barrier_mode`→"FIXED", `shadow_atr_multiplier_tp`→None, `sl`→1.5, clamps→0.5/3.0 (defaults silenciosos).
- `_resolve_atr_barriers:115-116` — `atr_pct<=0` → barreira fixa **mas linha carimbada v2** (54 rows / 1,66% hoje `[query]`).
- `_resolve_barrier_contract_version:133-135` — mode≠ATR_DYNAMIC ou tp_mult None → carimbo v1 silencioso.

**Fail-closed implementado** (commit `141d917`): sob contrato ativo `shadow_atr_dynamic_v2`, `_apply_barrier_params` não aplica defaults silenciosos e `_create_from_decision` exige (via `_require_v2_barrier_config`) todas as chaves + modo ATR_DYNAMIC + atr>0; senão `ValueError` e **linha NÃO criada** (raise dentro do try/except dos callers `safe_*_create` → blast radius de 1 linha). Paths v1/legacy inalterados.

**Teste** (`backend/tests/test_shadow_barrier_v2_fail_closed.py`): `8 passed`. Suíte de barreira/shadow existente: `94 passed` (app-style) + `4 passed` (backend-style). Única falha `test_p0_dataset_l3_split::test_lightgbm_respects_configured_retrain_minimum` = **pré-existente** (confirmado via `git stash`; independe do P1).

**Chaves prod verificadas** `[query]` (todas presentes → deploy não derruba criação de shadows):
`ml_active_barrier_contract_version=shadow_atr_dynamic_v2`, `shadow_barrier_mode=ATR_DYNAMIC`, `shadow_atr_multiplier_tp=1.5`, `shadow_atr_multiplier_sl=1.5`, `shadow_barrier_min_pct=0.5`, `shadow_barrier_max_pct=3.0`.

**Deploy (1.5): ✅ FEITO via `railway up` no `scalpyn-worker-structural`.** (Rota GitHub bloqueada: `graphify-out/graph.json`=167 MB > 100 MB, tracked em `610e04e` — motivo estrutural de a fase deployar por `railway up`; `.railwayignore`+`.gitignore` excluem graph.json do upload.) Build OK (imagem `2026-07-16T04:08:49Z`, HEAD `79f6d02`), worker subiu limpo ("Application startup complete", sem erro). **Pós-verificação `[query]`**: baseline pré-deploy 107 shadows/95 v2 em 30min (03:38 UTC); pós-deploy primeiras linhas novas às 04:13:11 = **4/4 carimbadas `shadow_atr_dynamic_v2`** → criação v2 retomada normal. Logs mostram o fail-closed operando: `barrier_v2_atr_unavailable symbol=PEPE_USDT atr_pct=0.0 — linha NÃO criada` (esperado; **zero** `barrier_v2_missing_*` de config → prod completa). **Observação**: símbolos ultra-low-price com atr→0 (ex. PEPE_USDT) são agora excluídos todo ciclo, gerando exceção recorrente no log (correto funcionalmente; opção de refino: rebaixar o caso atr==0 de exceção para skip WARNING, já que é condição de dado, não erro de config).

## P2 — Furo A: consumo do modelo em decisão

**Cadeia de decisão** (`[file:line]`):
- Seleção do campeão: `prediction_service.py:62,76` — `SELECT id, decision_threshold, version FROM ml_models WHERE status='active'` (por lane).
- Load: `prediction_service.py:121` `get_model(...)` (BYTEA/joblib); sem modelo → `NoEligibleModelError` → `_fail_closed_result` (`model_approved=False`).
- Predict: `prediction_service.py:276` `approved = proba >= threshold`.
- Uso na decisão: `pipeline_scan.py:3196` gate `if _ml_gate_enabled:` → `3416` `_approved=model_approved` → **`3501-3503` `if not _approved: _d["decision"]="BLOCK"; _d["l3_pass"]=False`**. Wiring REAL, não teatro.

**Prova de runtime** `[query]`:
- `ml_models`: **ZERO active** (candidate 31 / rejected 21 / retired 17).
- `ml_opportunity_rankings` (48h): 475 linhas, **100% `score_status=SKIPPED`, `gate_action=BLOCK`, `model_id NULL`, `used_by_gate=TRUE`** (última 03:13).
- `decisions_log` (24h) — **colunas dedicadas** (não `metrics` JSONB): 207 linhas `ml_gate_enabled=TRUE`, todas `decision_before_ml=ALLOW → decision_after_ml=BLOCK`, `gate_action=BLOCK`, `score_status=SKIPPED`, `reason_code=NO_ELIGIBLE_MODEL_FOR_LANE`, `ranking_id` presente. `model_id`/`probability` NULL só porque não há modelo.

**VEREDITO CORRIGIDO = CONSOME (wired end-to-end).** ⚠️ Meu veredito inicial ("CONSOME PARCIAL, fio se rompe, 0/1945") estava **errado** — consultei `metrics` JSONB, mas a atribuição ML vai para **colunas dedicadas** do `decisions_log` (`_persist_decision_logs:1723-1733`: `decision/gate_action/ml_gate_enabled/ranking_id`). Cadeia completa e viva: campeão (`status='active'`) → `predict` → flip ALLOW→BLOCK (pipeline_scan:3501) → **persistido no `decisions_log`** → execução (BLOCK = não negocia). **O que a correção revelou:** com 0 modelos active o gate estava **bloqueando 207 L3 ALLOW reais/24h** por `NO_ELIGIBLE_MODEL_FOR_LANE` (fail-closed sem modelo), contrariando o contrato do `prediction_service:125` ("SKIPPED não bloqueia por si só").

**FIX IMPLEMENTADO (operador opção 1, commit `5097b4c`):** novo helper `_ml_gate_should_block` — o gate só rebaixa ALLOW→BLOCK quando um modelo active de fato rejeita (`score_status=OK` + not approved) ou em falha de infra com modelo presente (`ML_EXCEPTION_FAIL_CLOSED`); **`SKIPPED`/no-model → pass-through** (ALLOW). `gate_action`/`decision_after_ml`/`reason_codes`/ranking passam a refletir a ação EFETIVA (ALLOW no pass-through); `model_approved`/`score_status` preservados como telemetria crua. **7 testes novos + suíte gate audit (12) verdes.** Deployado via push→auto-deploy (`5097b4c` SUCCESS no structural, `ML_GATE_ENABLED=true` armado); pipeline saudável pós-deploy. Confirmação runtime do pass-through pendente do próximo ciclo L3-gated (esparso ~30-60min; verificar: `SELECT decision,gate_action,score_status,count(*) FROM decisions_log WHERE ml_gate_enabled AND created_at>'2026-07-16 12:06Z' GROUP BY 1,2,3` — esperado `ALLOW/ALLOW/SKIPPED`).

## P3 — Circuit breaker do Autopilot: net vs gross

**Métrica** (`backend/app/services/autopilot_engine.py`): breaker de performance lê `approved_ev = AVG(COALESCE(net_return_pct, pnl_pct - fee))` = **NET** (`:353,370`); `_check_regression:506` compara `new_ev` (net) vs baseline (`EV_REGRESSION_DELTA=0.20`); triggers de mutação usam `ev=perf["approved_ev"]` (net, `:791,806`). `fee_limited_guard` (`:798-804`) bloqueia mutação quando `gross_ev>0 AND net_ev<threshold` (fee drag). Janela = `AUTOPILOT_SOURCE=L1_SPECTRUM` (default), fee de config.

**Gross vs net na janela** `[query]`:
| janela | n | gross_ev | net_ev | win_rate |
|---|---|---|---|---|
| 7d | 1790 | −0.0337% | −0.2337% | 47.82% |
| 30d | 4536 | −0.0381% | −0.2381% | 41.78% |

**Divergência material = NÃO.** Gross e net estão **ambos negativos** → o breaker (net) e um hipotético breaker gross concordariam ("sangrando"). O gap gross−net = 0.20 (= fee flat); gross>0/net<0 só ocorreria na banda 0<gross<0.2%, coberta pelo `fee_limited_guard`. **Fix = N/A** — o breaker mede net e já trata fee drag. (Observação de negócio, não de breaker: L1_SPECTRUM está net-negativo em 4536 trades.)

## P4 — Funil L3 ⊆ L1

**Definições** (`shadow_trade_service.py`): `L1_SPECTRUM` capturado na promoção L1 (todos os símbolos pós-filtro estrutural), **deduped** (migration 135 `l1_dedup_constraint`, ~1 linha/símbolo/ciclo). `L3`/`L3_LAB` via `_create_from_decision`, por-decisão/por-profile (múltiplas linhas/símbolo). Populações geradas por paths distintos com granularidade distinta.

**Contagens pareadas** `[query]`:
- Linhas/dia: L3 > L1_SPECTRUM em todos os dias (ex. 15/jul L1=461, L3=808) — artefato de granularidade.
- **Símbolos distintos/dia (7 dias consecutivos): `l3_sym_outside_l1 = 0` sempre.** Ex. 15/jul: L1=30, L3+L3_LAB=24, fora de L1 = 0.

**VEREDITO = FUNIL VÁLIDO** (nível de símbolo). L3/L3_LAB ⊆ L1_SPECTRUM em símbolos distintos (0 fora, 7/7 dias). A "anomalia L3 > L1" é artefato de comparar **contagem de linhas** entre populações de granularidade diferente, não violação de funil. **Consequências para L1×L3:** comparações de contagem bruta/agregados sem normalizar granularidade são **inválidas**; a comparação de edge deve ser no **nível de símbolo** (mesmo símbolo+dia nos dois streams) ou normalizada por símbolo. O funil é rastreável (símbolo+dia). Decisão de desenho da análise = do operador.

## P5 — Protocolo do primeiro retrain canônico

Commitado (`b340700`): `docs/PROTOCOLO_PRIMEIRO_RETRAIN_CANONICO.md`. Pré-condições (gate 3000 / cert GREEN / I12=0 / 8 chaves de aprovação / seed 42 / git limpo), comando `python backend/scripts/run_lgbm_retrain.py [--dry-run]`, critérios de aceite (min_test_auc=0.6, min_test_samples=300, max_val_test_gap=0.05, max_test_fpr=0.5, require_positive_net_ev=true, ci_excludes_half=true, min_distinct_days=5, capture preenchido — todos `[query]`), e o que NÃO fazer.
- **Rotação de hold-out: proposta `ml_max_candidates_per_holdout=3` — 🛑 operador confirma o valor antes de gravar a chave.**

## PROJEÇÃO

- Elegíveis v2 (pós `ml_dataset_valid_from=2026-07-15T20:20:53Z`): **~101 fechados** `[query]` (contagem exata via `run_lgbm_retrain.py --dry-run`; sujeita a filtro de contrato + embargo de maturidade).
- Taxa/dia (L1 eligible closed, últimos 3 dias cheios): 376/455/457 → **~429/dia** `[calc]`.
- Gate = `ml_retrain_min_eligible_rows=3000` `[query]`. Data estimada: `(3000-101)/429 ≈ 6,8 dias` → **~2026-07-23** (conservador ~300/dia → ~2026-07-26). `[NÃO VERIFICADO]` a estabilidade da taxa pós-reset de valid_from.

## STOPs / [NÃO VERIFICADO]

1. **P1.5 deploy** — ✅ RESOLVIDO (railway up structural, pós-verif 4/4 v2) **+ regularização GitHub concluída** (opção 4): `graph.json` purgado do histórico via BFG (`--strip-blobs-bigger-than 100M --no-blob-protection`; blobs 167MB+133MB removidos; `.git` 322MB→42MB; `graphify-out` destrackeado em commit dedicado). feat e main pushados (history reescrita, 1755 commits); auto-deploy GitHub de `329c1e4` **SUCCESS** no structural → serviços alinhados com o código atual. Backup: bundle `fase1.6-fullrepo-backup.bundle` (scratchpad). Opção de refino remanescente: atr==0 fail-closed como WARNING em vez de exceção (reduz ruído de log em símbolos ultra-low-price como PEPE).
2. **P2 wiring** — ✅ RESOLVIDO. Veredito corrigido para CONSOME (o fio já estava wired; erro de medição meu). Fix do gate modelless (pass-through em SKIPPED) implementado + deployado (`5097b4c`). Confirmação runtime do pass-through pendente do próximo ciclo L3-gated.
3. **P4 análise** — desenho da comparação L1×L3 (símbolo-nível) é decisão do operador.
4. **P5 hold-out** — `ml_max_candidates_per_holdout=3` aguarda confirmação.
5. **P2 pass-through runtime** — fix deployado (`5097b4c`); falta observar um ciclo L3-gated pós-deploy confirmar `ALLOW/ALLOW/SKIPPED` (esparso). O "[NÃO VERIFICADO] fio se rompe" do rascunho inicial foi **resolvido**: o fio JÁ estava wired (colunas dedicadas); o 0/1945 era erro de medição (campo `metrics` JSONB).

## DECLARAÇÃO

Escritas de código restritas a: `shadow_trade_service.py` (P1 fail-closed) + `test_shadow_barrier_v2_fail_closed.py`; `pipeline_scan.py` (P2 pass-through gate) + `test_ml_gate_no_model_passthrough.py`; docs (`PROTOCOLO_PRIMEIRO_RETRAIN_CANONICO.md`, este relatório) + `graphify-out` destrackeado. `shadow_trades` **READ-ONLY** (só SELECT). **NENHUM retrain disparado. Nenhum modelo promovido/demovido.** Nenhum backfill. Commits com `git status` limpo `[literal: git status --porcelain vazio]` antes de cada deploy (Regra 6). **Deploys:** P1 via `railway up` no structural (pós-verif 4/4 v2); regularização GitHub via BFG (graph.json purgado) → feat+main pushados → auto-deploy geral; P2 via push→auto-deploy (`5097b4c` SUCCESS). Backup pré-rewrite: bundle `fase1.6-fullrepo-backup.bundle`.
