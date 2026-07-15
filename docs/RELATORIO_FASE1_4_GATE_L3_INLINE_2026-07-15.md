# RELATÓRIO FASE 1.4 — 2026-07-15 ~20:00 UTC

Contrato: `PROMPT_FASE1_4_GATE_L3_INLINE.md`. Estado: **concluído e deployado.**

## PASSO 1 — gate P1: path inline L3 → loader → dano

**Paths de escrita L3** `[file]`:
- L3 canônico: `_create_from_decision` (`shadow_trade_service.py:848`, INSERT `_INSERT_SHADOW_SQL:792`), decision_id setado, usa `_build_economic_config_snapshot` (B.1) — mas o config_snapshot econômico sai vazio porque o caller L3 não injeta `_apply_barrier_params` (o L3_REJECTED injeta → completo).
- L3_LAB inline: `create_strategy_lab_shadows` (`:3314`, INSERT `_INSERT_STRATEGY_LAB_SQL:3265`), decision_id=NULL, monta `config_snap:3443` **sem** as chaves econômicas.
- **Ambos os INSERTs setam as COLUNAS DEDICADAS** `barrier_mode, tp_pct_applied, sl_pct_applied, barrier_contract_version`.

**Loader consome** `[file+query]`: `_load_shadow_data` consome L3/L3_LAB (elegíveis + native capture + lineage EXACT). Lê o contrato econômico das **colunas dedicadas**: `_filter_l3_barrier_contract` lê `barrier_mode`/`tp_pct_applied`/`barrier_contract_version` (`ml_challenger_service.py:175-188`); `_economic_contract_features` lê `tp_pct_applied`/`sl_pct_applied`. **`config_snapshot` aparece 1× (`:850`, no SELECT) e nunca é lido.**

**Dano** `[query]` (entry_timestamp ≥ 2026-07-15T14:22:37Z): L3 142/142 e L3_LAB 90/90 com colunas dedicadas + barrier_contract + native capture + elegível = **100%**. Só o JSONB `config_snapshot` econômico está vazio (cópia redundante, ignorada pelo treino). **Contaminação = ZERO.**

**1.4** `[file]`: I04 usa `CANONICAL_SOURCE='L1_SPECTRUM'` (`ml_data_certification_service.py:36`, hardcoded) — escopo deliberado à lane primária LightGBM. L3 sem certificação = **lacuna de cobertura**, não exclusão por contaminação.

**VEREDITO = 🟢 VERDE** `[operador]`. Ação escolhida: **A — certificar L3 via colunas dedicadas** (não mexer no config_snapshot; treino o ignora).

**1.6-A executado**: invariante **DEDICADO** `I12_l3_economic_contract` (escolha registrada: não estende o I04, preservando sua série histórica L1_SPECTRUM+ATR_DYNAMIC). Checa colunas dedicadas em L3/L3_LAB elegíveis na janela; FAIL se alguma nula. Fail-closed. `[teste]` `test_certification_i12_l3_contract_covered_and_fails_closed` (suíte 22 passed). Verificado em prod `[query]`: **0 violações, n=1714** (janela 26h). Deploy: compute worker SUCCESS.

## PASSO 2 — verificação retroativa + regularização Codex

**2.1a** `[literal]`: inventário dos untracked que passam `.railwayignore` = 278 arquivos; núcleo = **17 módulos backend** (crypto_ev+config+score+service, calibration_evolution/orchestrator_v2, model_governance, native_capture_governance, grouped_purged_split, feature_contract_v2, economic_targets, evidence_registry, ev_score_v2, profile_versioning_v2, profile_intelligence_contract, fix) + migrations 131-133 + testes + docs. Hashes gravados como baseline.

**2.1b** `[NÃO VERIFICADO]`: Railway não expõe hash do bundle de upload (só digest da imagem construída, que difere pelas minhas mudanças). Identidade Codex 1.2→1.3 não comprovável retroativamente; inventário atual gravado como baseline.

**Achado decisivo** `[query]`: os módulos são **load-bearing** — código tracked (`main.py`, `ml.py`, e os próprios `ml_challenger_service.py`/`shadow_trade_service.py` da Fase 1) os importa. **Opção 2 (remover de prod) INFEASÍVEL** — quebra o boot.

**2.2 🛑 → operador escolheu: commitar Codex como first-class** `[operador]`.

**2.3 ✍️ executado**: commit `45813a3` adota 17 módulos + migrations 131-133 + testes + scripts + docs + frontend/lib (98 arquivos), sem segredos/blobs. `.gitignore` estendido (`.codex/`, `.codex_tmp/`, `graphify-out/`, `.vercel-deploy-staging/`, scripts de debug). **`git status` agora LIMPO (0 linhas)** — **regra 6 plena a partir do commit `45813a3` / 2026-07-15**.

## PASSO 3 — consumidores do payload antigo do readiness

`[literal]` `git grep -E "dias_para_(1500|3000|5000)" HEAD`: nenhum consumidor programático. Frontend: **zero** referências a `readiness`. `git grep "readiness/latest|dias_para_milestone|dias_para_retrain"`: nenhum consumidor externo. **Rename seguro.** Único artefato dessincronizado: `backend/sql/fase1_certification_integrity.sql` (espelho de DBA, não executado por código) — ressincronizado (commit `19fb38e`): + I12, cumulativa → `dias_para_milestone`/`dias_para_retrain`, 5000 removido.

## PASSO 4 — confirmação de B.1 com n real (READ-ONLY)

**4.1** `[query]`: população canônica L1+ATR desde valid_from = **n=130, 100% completos** → **PASS** (n≥30, ≥95%). B.1 confirmado em produção com n real.

**4.2** `[query]`/`[calc]`: 110 elegíveis maturados desde valid_from (5,6h). Geração diária L1+ATR elegível: 07-11=29, 07-12=81, **07-13=376, 07-14=458, 07-15=409** — salto pós-lineage-V2 (11/jul). A premissa de **229/dia foi puxada para baixo** pelos dias pré-lineage-V2 (07-07 a 07-11 com 0-29 elegíveis). **Taxa sustentada real ≈ 418/dia elegível** (média 07-13/14/15), desvio **+82%** (>20% → recalibrar).

**PROJEÇÃO RECALIBRADA** `[calc]`: a ~418/dia elegível: **1.500 ≈ 2026-07-18/19**; **3.000 (gate de retrain) ≈ 2026-07-22** — cerca de uma semana antes da estimativa original de 29/07. (O endpoint readiness auto-ajusta via mediana 7d.)

## STOPs / [NÃO VERIFICADO]
- 🛑 Passo 1.5 (veredito) — respondido: VERDE, ação A.
- 🛑 Passo 2.2 (regularização) — respondido: commitar Codex first-class.
- 2.1b identidade bundle 1.2→1.3 — [NÃO VERIFICADO], baseline gravado.

## DECLARAÇÃO
Escritas executadas: (1) I12 no serviço + espelho SQL + testes (commits `f51fc33`, `19fb38e`); (2) adoção Codex (commit `45813a3`) + `.gitignore`; (3) deploy compute worker (`railway up`, git status limpo colado no Passo 2.3). **`shadow_trades` READ-ONLY confirmado** — zero DELETE/UPDATE nesta fase. Nenhum modelo promovido/demovido. Nenhum backfill. Regra 6 plena a partir de `45813a3`.
