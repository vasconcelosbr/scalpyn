# RELATÓRIO FASE 1.7 — JOB DE CERTIFICAÇÃO MORTO — 2026-07-16 ~18:55 UTC

Branch `feat/fase1-integridade-monitoracao`. Diagnóstico por LOG (railway logs `--since/--until`), broker via Redis público (read-only), DB via `DATABASE_PUBLIC_URL` (read-only).

## P1 — Causa = **C3** (task recebida e explode) + silêncio

**Evidência de LOG `[literal]`** (não deduzida do código):
- Beat **envia** nos horários pares: `16:00:00,005 Scheduler: Sending due task ml_data_certification (app.tasks.ml_data_certification.run)` → **não é C1**.
- Worker-compute (fila `structural_compute`) **recebe e roda**: `14:00:00,015 Task app.tasks.ml_data_certification.run[58dc79ab] received` → `14:00:00,527 ... succeeded in 0.51s: None` → **não é C2**.
- **Exceção real:** `[ml-certification] execução falhou` → `asyncpg.exceptions.PostgresSyntaxError: syntax error at or near ":"` em `run_certification` (`ml_data_certification_service.py:314`, execução do `_CUMULATIVE_SQL`).

**Causa exata `[file:line]`:** `_CUMULATIVE_SQL:155-156` usava `:milestone_rows::numeric` / `:retrain_rows::numeric`. O padrão `:param::cast` quebra o parser de bind-param do SQLAlchemy — verificado empiricamente: `text(":milestone_rows::numeric")._bindparams` → `{'milestone_row'}` (nome truncado/errado), o `:` vaza literal ao asyncpg → syntax error. `_INVARIANTS_SQL` (297) passou por não ter o padrão; `_INSERT_RUN_SQL` já usava a forma segura `CAST(:x AS JSONB)`.

**Ponto de silêncio `[file:line]`:** `ml_data_certification.py:112-115` — `except Exception:` + `logger.exception(...)` sem re-raise e sem persistir. A task retorna `None` ("succeeded"), o run nunca é gravado, nada sinaliza a falha → 28h de silêncio.

## P2 — Fix + prova de vida

**Fix `[file]`** (commit `128fe00`): `_CUMULATIVE_SQL` → `CAST(:milestone_rows AS numeric)` / `CAST(:retrain_rows AS numeric)`. Escopo mínimo, nada adjacente tocado.

**Teste `[teste]`** (`test_certification_cumulative_sql_binds.py`, 3): trava o contrato de binds (`_CUMULATIVE_SQL._bindparams == {src,bmode,valid_from,milestone_rows,retrain_rows}`) e reproduz a causa (`:name::cast` não produz o bind `name`). Suíte de certificação: `27 passed`.

**Deploy `[literal]`:** push→auto-deploy, `128fe00` **SUCCESS** no worker-compute. `git status` limpo antes.

**PROVA DE VIDA `[query]`** (trigger manual via caminho legítimo Celery `send_task('app.tasks.ml_data_certification.run', queue='structural_compute')`, aprovado pelo operador — sem esperar a janela 2h):
```
run_at=2026-07-16 18:20:53.936455+00  status=RED
window_from=2026-07-15 16:20:53  window_to=2026-07-16 18:20:53
cumulative: elegiveis_maturados_pos_boundary=305, mediana_diaria_7d=45.0, valid_from=2026-07-15T20:20:53Z
```
Antes: 0 runs em 28h. Depois: linha persistida pelo job real, população v2.

**Conteúdo = RED → 🛑 reportado (2.5)**: diagnóstico NOVO, não falha do fix. Invariantes que falharam `[query]`:
- **I03_elegivel_pre_valid_from = 39.161** — artefato do reset do `valid_from` (L1_SPECTRUM acumulou `eligible_for_training=true` antes da fronteira v2; não entram no treino, que filtra por `valid_from`).
- **I12_l3_economic_contract = 368** — dívida de contrato pré-fix (o fail-closed do P1/Fase 1.6 só entrou hoje 04:08; deve decair).
- PASS em I01,I02,I04–I11. WARN `ATR_NULL_IN_RUNNING=7` (in-flight, esperado). Operador decidiu tratar o RED como dívida separada e seguir para P3.

## P3 — Guardas anti-silêncio

**3.1 Staleness `[file]`** (`ml_data_certification_service.py`): `latest_certification` (endpoint `/api/ml/readiness/latest`) agora compara `now()-run_at` com `ml_readiness_staleness_threshold_hours` (**=3** `[query]`, chave criada — verificada inexistente antes). Run velha → `status_effective='STALE'` preservando `status` original; sem threshold → STALE (fail-closed). `[teste]` `test_readiness_staleness_guard.py` (6): recente→status original; velha→STALE; no-threshold→STALE.

**3.2 Heartbeat = (b)** `[operador]`: a task, ao falhar, grava `status='JOB_ERROR'` com o erro resumido em `invariants` (sessão dedicada, best-effort, sem re-raise — mantém "nunca afeta captura"). Falha vira visível na hora (última run = JOB_ERROR), não só após 3h. `[teste]` `test_certification_job_error_heartbeat.py` (2). Deploy `db552f6` SUCCESS (API + worker-compute).

**3.4 Protocolo atualizado `[literal commit `db552f6`]`**: PC2 do `PROTOCOLO_PRIMEIRO_RETRAIN_CANONICO.md` passa a exigir `status_effective != 'STALE'` (run recente < 3h) **além** de GREEN.

## LACUNA DE MONITORAMENTO

Período cego = **2026-07-15 14:00:00 → 2026-07-16 18:20:53 = 28,35h** `[calc]` (~14 runs de 2h perdidas). **O que a certificação teria visto:** a run retroativa (window 07-15 16:20→07-16 18:20 cobre a janela cega) veio **RED** com I03=39.161 e I12=368 — violações **cumulativas/estruturais** (filtram por `valid_from`, não pela janela), logo a certificação teria mostrado RED de forma consistente por todo o período. Nenhuma condição transitória foi perdida.

## PROJEÇÃO ATUALIZADA

- Elegíveis v2 (cert, população L1_SPECTRUM): **305 maturados pós-boundary** `[query]`.
- `dias_para_retrain` da run nova = **67** `[query]` (CEIL(3000/45)). **Distorcido**: a mediana 45/dia é puxada para baixo porque o `valid_from` foi resetado ontem (a maioria dos 7 dias tem 0 elegíveis pós-boundary). O gate real do LGBM (L1, `run_lgbm_retrain --dry-run`) e a taxa bruta recente (~429/dia, Fase 1.6) apontam timeline bem mais curta (~2026-07-23/26). A mediana normaliza conforme dias pós-v2 acumulam.

## STOPs / [NÃO VERIFICADO]

1. **RED da run nova** (I03=39161, I12=368) — dívida pré-existente/separada, decisão do operador (seguir P3 escolhida). I12 deve decair pós-fix P1; I03 é o reset de valid_from.
2. **Backlog gigante das filas** (fora do escopo 1.7, mas grave): `structural`=143k, `execution`=87k mensagens `[query]`; `scalpyn-worker-execution` com "missed heartbeat" repetido (provavelmente morto). Não afeta a cert (`structural_compute`=0, saudável). **Recomendo incidente separado.**
3. **[NÃO VERIFICADO]** run natural pelo beat (próxima 20:00 UTC) — a prova de vida foi via trigger manual (aprovado); a run natural usa o mesmo código corrigido.
4. **[NÃO VERIFICADO]** endpoint `/ml/readiness/latest` chamado com auth em runtime — lógica coberta por unit test + chave presente + deploy SUCCESS; a run atual (<3h) retornaria `status_effective=RED, is_stale=false`.

## ADENDO — pós-relatório (backlog + RED tratados) 2026-07-16 ~23:00 UTC

**Backlog de filas (incidente separado, investigado a pedido):** diagnóstico invertido do alarme inicial — **não é executor cego**. Workers vivos e processando (trade_monitor fechando trades às 21:59; shadows fecham 200-647/h, 0 abertos >24h); **`live_trading_enabled=0`** em todos os 33 profiles; 57 posições "open" são stale (desde mar/2026, 0 tocadas em 48h). As filas `structural`=143k + `execution`=88k eram **relíquia estável** (+0,7 e +2,3/min ≈ flat) de tasks idempotentes acumuladas em downtimes passados; ~227MB Redis (sem maxmemory). **Purgadas** (DEL → 231M→3,8M; pós-purga fila fica em 0-2, confirmando que os workers acompanham a produção). **Guard** (commit `1d7498a`): `expires=max(3×intervalo,60)` em todo schedule numérico do beat → backlog não cresce indefinidamente em downtime futuro.

**RED da run (I03/I12) tratado** (commit `8d747a9`, deployado; decisões do operador):
- **I03** redefinido para a janela `[w_from, valid_from)` (não todo o histórico) — legado pré-boundary é filtrado pelo loader, não contamina. Prod: 39161→**0**.
- **I12** (a) loader `_filter_l3_barrier_contract` exclui ATR_DYNAMIC não-v2 (degradadas, TP fixo pré-fix); (b) invariante espelha o loader. Prod: 368→**0**. As 368 eram L3 v1 de 20:25→11:25 (cessou no deploy-geral ~11:44). 2 testes atualizados; 4 falhas catboost restantes são pré-existentes.
- **Prova:** cert nova 22:59:59 = **YELLOW, `failed=[]`** (só warn transitório ATR_NULL_IN_RUNNING). RED permanente destravado; GREEN alcançável.

**Chaves gravadas** (config_profiles, idempotente): `ml_readiness_staleness_threshold_hours=3`, `ml_max_candidates_per_holdout=3`.

## DECLARAÇÃO

Escritas: código (`ml_data_certification_service.py`, `ml_data_certification.py`) + 3 arquivos de teste + protocolo + este relatório; config key `ml_readiness_staleness_threshold_hours=3` em `config_profiles` (idempotente, guard NOT-exists). `shadow_trades` **READ-ONLY**. **Zero INSERT manual em `ml_data_certification_runs`** — a run nova foi gravada pela execução legítima do job (trigger via `send_task`); os JOB_ERROR só o próprio job grava ao falhar. **Nenhum retrain; nenhum modelo promovido/demovido.** Deploys via push→auto-deploy com `git status` limpo `[literal: git status --porcelain vazio]`. Commits: `128fe00` (fix), `d65b968` (guardas), `db552f6` (protocolo).
