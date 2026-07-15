# RELATÓRIO FASE 1.3 — 2026-07-15 ~15:40 UTC

Contrato: `PROMPT_FASE1_3_FECHAMENTO_POS_DEPLOY.md`. Estado: **todos os passos concluídos e deployados.**

## PASSO 0 — confirmação da primeira run + B.1 em produção (READ-ONLY)
- **0.1 run 16:00** `[query]`: o job já rodou 2× antes das 16:00 (cadência normal de 2h): `run_at=2026-07-15 14:00:00` e `12:00:00`, ambas `status=RED` (`failed=[I04_snapshot_incompleto, I10_duplicidade_elegivel]`). Job confirmado rodando — sem 🛑. (A "run de 16:00" do enunciado ainda não ocorrera às 14:33; as duas runs anteriores já provam o pipeline.)
- **0.2 endpoint** `[query]`: `GET /api/ml/readiness/latest` (JWT cunhado) → **HTTP 200**, retorna a run 14:00 (I04=424, I10=2, `elegiveis_maturados_pos_boundary=1194` sob valid_from antigo, pré-bump).
- **0.3 B.1 em produção (gate)** `[query]`: população canônica L1_SPECTRUM+ATR_DYNAMIC com `entry_timestamp >= 2026-07-15T14:22:37Z` = **2/2 = 100% snapshot completo**. **PASS** (> 95%), sem 🛑. Observação: L3/L3_LAB 0/2 completos (path inline não seta o contrato econômico) — fora da população canônica do I04, não bloqueia.
- **0.4 acumulação** `[query]`/`[calc]`: 0 elegíveis maturados desde o novo valid_from (~11 min à época); base de geração ~229/dia (E.1).

## PASSO 1 — readiness config-driven
- **1.1 literais** `[file]`: `ml_data_certification_service.py:123-125` (1500.0/3000.0/5000.0) + chaves de saída `dias_para_1500/3000/5000` em :254-256.
- **1.2 chaves** `[query]`: 3000→`ml_retrain_min_eligible_rows` (existe=3000), 1500→`ml_readiness_milestone_rows` (existe=1500), 5000→**sem chave** → 🛑 operador: **remover 5000 do display** `[operador]`.
- **1.3 refactor** `[file]`: query lê `:milestone_rows`/`:retrain_rows` via `_require_positive_int_config` (mesmo helper do gate, `ml_challenger_service.py:143`, import local para não pesar o path do endpoint), fail-closed. Payload renomeado `dias_para_milestone`/`dias_para_retrain` + `milestone_rows`/`retrain_gate_rows`; 5000 removido.
- **1.4 testes** `[teste]`: `test_readiness_targets_come_from_config_not_hardcoded` (muda config → param muda) + `test_readiness_fail_closed_when_target_key_absent`. Suíte `test_fase1_integrity_certification`: **21 passed**.
- **1.5 Select-String** `[literal]`: `grep -E '\b(1500|3000|5000)\b' ml_data_certification_service.py` → **vazio** (grep exit=1). PASS.

## PASSO 2 — I10 causa-raiz + constraint (escopo B aprovado)
- **2.1 diagnóstico** `[query]`/`[file]`: os 8 pares têm `event_id` distintos, deltas 1,5–8 min, mesma watchlist; alguns com outcomes divergentes (entry price difere). `pipeline_scan.scan` cadência 5 min, `acks_late=False`.
- **Hipóteses:** H-retry **REFUTADA** (event_ids distintos + acks_late=False); H-doubleworker **REFUTADA** (deltas de minutos, não sub-segundo); **H-overlap CONFIRMADA** — `create_l1_spectrum_shadows` (`shadow_trade_service.py:1911`) amostra com `hash(symbol:execution_id)` e o execution_id muda por scan → decisão independente entre ciclos; sem dedup por chave natural.
- **2.2 🛑** — chave natural `(user_id, symbol, entry_timestamp, source)` validada `[query]` (0 grupos cruzam watchlist/profile/user; 0 entry_timestamp NULL). Achado material: **16 grupos duplicados** (37 linhas), não só os 4 elegíveis. Operador escolheu **opção B: full L1** `[operador]`.
- **2.3 dedup** `[query]`: DELETE por id de **21 linhas** (mantém menor created_at por grupo); FK `crypto_ev_l3_replay_flags` é **ON DELETE CASCADE** → 3 flags cascatearam. 16 grupos → **0**, verificado.
- **2.4 migration** `[file]`: `135_l1_dedup_constraint` (rev id 23 chars ≤ 32) — índice único parcial `ux_shadow_l1_symbol_entry (user_id, symbol, entry_timestamp) WHERE source='L1_SPECTRUM' AND entry_timestamp IS NOT NULL` + dedup defensivo keep-min (guard contra corrida na janela de deploy). Fix: `_is_l1_duplicate_conflict` + branch no handler de `IntegrityError` → skip idempotente (espelha `uq_shadow_lab_active_profile_symbol`).
- **2.5 teste idempotência** `[teste]`: `test_create_from_decision_idempotent_on_l1_duplicate` (2º INSERT colide → return None, sem exceção) + `test_l1_duplicate_conflict_detection`. Total `test_shadow_profile_attribution`: **7 passed**.
- **2.6 deploy** `[literal]`: commit `5f796e1` (14 arquivos Fase 1). **Regra 6 — conflito reportado:** working tree tem ~18k untracked do Codex **já em prod** (deployado de tree untracked); stashar regrediria prod → `git status` literalmente limpo é inatingível sem regressão. Operador aprovou **deploy do working tree** `[operador]`. `railway up` (Railway, não gcloud): API (migration 135 → `alembic_version=135_l1_dedup_constraint`, índice criado), structural (fix L1), compute (readiness). 3 serviços **SUCCESS**, API `/docs`=200, **0 grupos duplicados L1**, índice ativo `[query]`.

## PASSO 3 — docs superseded
- **3.1/3.2 banners** `[file]`: banner SUPERSEDED (14400 canônico, sem apagar histórico) em `docs/HEALTH_CHECK_SHADOW_2026-06-11.md` (config [OK]), `docs/FIX_B123_2026-06-10.md` (config JSON), `docs/audits/correcao-unificada-v50-v52-2026-07.md` (drift v50). Demais ocorrências de 1800 (análises, queries, default de código, registros de modelos históricos) não são asserção canônica — mantidas.
- **3.3 commit isolado** `[literal]`: commit `176d282` (só docs, sem código).

## PROJEÇÃO ATUALIZADA
`valid_from = 2026-07-15T14:22:37Z`; elegíveis maturados desde então ≈ 0 (curva recém iniciada); base ~229/dia `[calc]`. **1.500 em ~7 dias (≈2026-07-22)**; **3.000 (gate de retrain) em ~14 dias (≈2026-07-29)**. Os 1.194 do contrato antigo NÃO contam.

## STOPs / [NÃO VERIFICADO]
- 🛑 Passo 1.2 (meta 5000) — respondido: remover.
- 🛑 Passo 2.2 (escopo constraint) — respondido: B (full L1).
- Deploy 2.6 (regra 6) — respondido: deploy do working tree.
- Passo 0 gate B.1 — PASS (100% canônico), sem STOP.
- `gcloud` do contrato base: [INCORRETO] — infra é Railway.

## DECLARAÇÃO
Escritas executadas: (1) 3 config UPDATEs na Fase 1.2 (não nesta fase); (2) **DELETE de 21 linhas em `shadow_trades` por id** (Passo 2.3) — cascade removeu 3 `crypto_ev_l3_replay_flags`; (3) migration 135 (índice + dedup defensivo); (4) commits `5f796e1` (código) e `176d282` (docs). `shadow_trades` tocada SOMENTE nos 21 DELETEs por id aprovados (opção B). Nenhum modelo promovido/demovido. Nenhum backfill.
