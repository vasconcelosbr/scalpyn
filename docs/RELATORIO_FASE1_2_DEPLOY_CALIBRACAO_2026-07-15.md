# RELATÓRIO FASE 1.2 — 2026-07-15 ~10:10 UTC

Contrato: `PROMPT_FASE1_2_DEPLOY_CALIBRACAO.md`. Estado: **Passos 1–3 executados; PARADO no 🛑 Passo 4 (deploy) aguardando aprovação do operador.**

## P2: valor decidido = **14400** [operador]; gravado (NO-OP) [query]
- 1.1 valor vivo `[query]`: `ml_win_fast_threshold_seconds = 14400` (config `ml` ativa, `updated_at=2026-07-13 18:14 UTC`).
- 1.2 `[file]`: 1800 e 14400 são **labels distintos** — `_LABEL_THRESHOLD_REGISTRY = {1800.0:"is_win_fast_v1", 14400.0:"is_tp_4h_v1"}` (`docs/AUDITORIA_COMPLETA_POOL_..._2026-06-24.md:558`). 1800 marcado `[OK]` em `HEALTH_CHECK_SHADOW_2026-06-11.md:204`; v50 original usou 1800 (`docs/audits/correcao-unificada-v50-v52-2026-07.md:224`). **Nunca houve decisão canônica formal** — A7 do mesmo doc (linha 17) classifica como "Drift, não decisão formal por lane; scripts/docs contraditórios".
- 1.3 operador respondeu: **14400 (`is_tp_4h_v1`)** `[operador]`.
- 1.4 `[query]`: valor vivo já era 14400 = decidido → **UPDATE NO-OP, não executado** (minimal change; não tocar `updated_at` à toa). Config confirmada em 14400.
- 1.5 Nota: como a decisão foi 14400 (não 1800), **a ressalva de não-comparabilidade não se aplica** — a escolha preserva continuidade com v80 e todo o histórico sob 14400.

## P3: chaves semeadas [query]
Pré-verificação: ambas AUSENTES. Gravadas na config `ml` (1 UPDATE commitado):
- `ml_certification_generation_floor = 80` (pós-verify `[query]`: 80)
- `ml_certification_alert_channel = 'LOG_ONLY'` (pós-verify `[query]`: LOG_ONLY)

## CALIBRAÇÃO: chaves gravadas [query]; consumo verificado [file:line]
Racional: mediana 229 elegíveis/dia; purge+embargo consomem ~32% nas fronteiras → primeiro retrain com poder mínimo gateado em **3.000 brutos** (~2.000 úteis).
- 3.1 pré-verificação `[query]`: existia `ml_retrain_min_eligible_rows = 2800` e `ml_catboost_retrain_min_eligible_rows = 200`; milestone/purge inexistentes.
- 3.2 gravadas (1 UPDATE commitado, pós-verify `[query]`):
  - `ml_retrain_min_eligible_rows`: **2800 → 3000** (estende chave existente, não duplica)
  - `ml_readiness_milestone_rows = 1500` (novo, informativo)
  - `ml_retrain_purge_overhead_pct = 32` (novo, documenta premissa)
  - Intocados: `ml_catboost_retrain_min_eligible_rows=200`, `ml_win_fast_threshold_seconds=14400`.
- 3.3 **consumo confirmado (sem 🛑)** `[file]`: o gate lê a chave em `backend/app/services/ml_challenger_service.py:2278` (`_require_positive_int_config(ml_config, "ml_retrain_min_eligible_rows")`) e `backend/scripts/run_lgbm_retrain.py:84` (`int(ml_config["ml_retrain_min_eligible_rows"])`). Elevar para 3000 gateia o retrain diretamente.
- 3.4 `[file]`: o readiness computa `dias_para_3000` em `backend/app/services/ml_data_certification_service.py:124` (**literal `3000.0`**, ao lado de 1500/5000). Já projeta contra 3.000, mas como constante SQL — **não lê `ml_retrain_min_eligible_rows`**. Reportado sem corrigir (fora do escopo deste prompt).

## E.2: DEPLOY EXECUTADO (aprovação do operador: "eu rodo railway up")
Infra é **Railway** (contrato dizia `gcloud run deploy` — [INCORRETO], não executado). Deploy via `railway up --service <s> --ci`. 4 serviços na ordem: API → compute → structural → beat.

**Incidente — revision id > 32 chars (pitfall conhecido, recorreu):** primeiro deploy da API entrou em retry-loop no boot: `StringDataRightTruncationError` — `134_fase1_integrity_certification` (33 chars) excede `alembic_version.version_num VARCHAR(32)`. A migration faz rollback a cada tentativa → **sem estado parcial** (tabela não criada). Fix: revision id → `134_fase1_integrity_cert` (24 chars), API re-deployada. `[file backend/alembic/versions/134_fase1_integrity_certification.py:35]`

Pós-deploy `[query]`:
- `alembic_version = 134_fase1_integrity_cert` ✅; `ml_data_certification_runs` existe ✅; `ml_training_dataset.win_threshold_s` adicionada ✅.
- Seeds 134: `ml_dataset_contracts` = 6 ids (inclui `ds_l1_spectrum_atrdyn_v2`); `ml_label_contracts.positive_net_return_v1` v1.0 ✅.
- API saudável: `/docs`=200, `/api/ml/readiness/latest`=401 (auth-gated, esperado). 
- Serviços: `scalpyn`, `scalpyn-worker-compute`, `scalpyn-worker-structural`, `scalpyn-beat` — todos deployment **SUCCESS/RUNNING**; beat escalonando normalmente.
- **[CAVEAT]** `railway up` sobe o working tree inteiro (Fase 1 + trabalho Codex); `.railwayignore` exclui `.codex/`, `graphify-out/`, frontends. Serviços não tocados (micro/execution workers) seguem no código anterior — tasks deles inalteradas, sem skew funcional.

## P4: valid_from = 2026-07-15T14:22:37+00:00 [query]; custo registrado: 1.107 elegíveis descartados
Dry-run: atual `2026-07-01T00:00:00+00:00` → proposto `2026-07-15T14:22:37+00:00` (timestamp do deploy). UPDATE (rowcount=1) + pós-verify `[query]`. Custo consciente (5.1): os 1.107 elegíveis maturados sob contrato antigo saem da população canônica (incompatíveis com D1=A).

## E.3: verificação pós-deploy (READ-ONLY)
- **6.1 primeira run do job:** PENDENTE — beat agenda `ml_data_certification` em `crontab(minute=0, hour="*/2")`; próximo disparo **16:00 UTC** (agora ~14:24 UTC). Não forçado: SSH exigiria chave nova na conta e INSERT manual violaria o read-only do Passo 6. O job produzirá a linha às 16:00.
- **6.2 endpoint:** `GET /api/ml/readiness/latest` responde 401 sem auth (existe); retornará a run após 16:00.
- **6.3 sanidade I09×I04** `[query]`: a coincidência `2997=2997` do E.1 era **população idêntica** (100% do pop tinha snapshot incompleto), não predicado errado. Agora: pop=3092, I04_incompleto=3002, **snapshot_completo=90** → I09 (total) e I04 (subconjunto incompleto) divergem, confirmando semânticas distintas. Reportado sem corrigir.
- **6.4 I10** `[query]`: **4 grupos** duplicados (8 linhas) `(symbol, entry_timestamp)` elegíveis:
  - DEXE_USDT @ 2026-07-13 16:25:00 → ids `5c507265…`, `d88a97d5…`
  - DEXE_USDT @ 2026-07-14 00:35:00 → ids `996aeb39…`, `241fe161…`
  - HYPE_USDT @ 2026-07-15 03:00:00 → ids `97475061…`, `63fc120a…`
  - NEAR_USDT @ 2026-07-14 15:35:00 → ids `a237219d…`, `05b0aaaf…`
  - Duplicação real (2 passes de pipeline_scan, criados ~7-10 min à parte). **Candidata a constraint único** antes do dataset atingir 1.500 — decisão do operador em prompt futuro.

## PROJEÇÃO RECALIBRADA
Meta 3.000 elegíveis a partir do novo `valid_from` (2026-07-15 14:22 UTC), mediana 229/dia → **~14 dias** (≈2026-07-29) [calc]. Marco informativo 1.500 em ~7 dias (≈2026-07-22). Os 1.107 do contrato antigo NÃO contam.

## ITENS [NÃO VERIFICADO] / STOPs
- 🛑 Passo 1.3 P2 — acionado e respondido (14400).
- 🛑 Passo 4.1 E.2 — acionado; operador aprovou "eu rodo railway up"; executado.
- `gcloud run deploy` do contrato: [INCORRETO] — infra é Railway.
- E.3.1 / 6.2 (primeira run + endpoint com dados): [PENDENTE] execução agendada 16:00 UTC.

## Declaração
Nenhum backfill executado. `shadow_trades` intocada (zero UPDATE/DELETE). Nenhum modelo promovido/demovido. Escritas desta fase: 3 UPDATEs em `config_profiles`/config `ml` (P3 seed, calibração, bump valid_from) — todos aditivos/pontuais, com dry-run + pós-verificação. 1 alteração de código (revision id da migration 134). Deploy de 4 serviços via `railway up`. Migration 134 aplicada (idempotente; rollback limpo na tentativa com id longo).
