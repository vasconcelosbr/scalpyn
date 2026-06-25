# Validação Geral — Profile Intelligence Adaptive Loop

**Data da auditoria:** 2026-06-24 (~23:30–23:50 UTC)
**Auditor:** Claude Sonnet 4.6, modo read-only
**Regra seguida:** nenhuma escrita no banco durante esta auditoria. Todas as queries rodaram dentro de `BEGIN; SET TRANSACTION READ ONLY;` seguido de `ROLLBACK`. Nenhum `alembic upgrade`, nenhum script com `--commit`, nenhum `git commit`/`push` foi executado nesta auditoria.

---

## ADENDO 2026-06-25 00:21 UTC — pós-commit, pós-deploy

Esta auditoria foi seguida, na mesma sessão, por uma execução autorizada do punch list de remediação. Os itens abaixo **alteram a premissa central do relatório original** (que permanece abaixo, intacto, como registro histórico do estado "antes"):

1. **Commitado**: 3 commits na branch `fix/profile-intelligence-adaptive-loop` (`68dc216`, `9f71865`, `faedd62`) — incluindo o lote original (Promotion Gate/model_lane/Shadow lineage/Label Lab/Feedback Engine) + correção de `decision_id` duplicado (migrations 109/110, ver Fase 14 abaixo).
2. **Regressão completa rodada** com `pytest-asyncio`/`fakeredis` instalados: **64 failed, 901 passed, 12 errors** — confirmado via comparação rigorosa (reverter os 9 arquivos modificados para o commit-base e re-rodar a suíte completa) que **o conjunto de FAILED/ERROR é idêntico antes/depois**. Nenhuma regressão introduzida por este lote.
3. **`decision_id` duplicado corrigido sem DELETE**: 38 grupos duplicados (17 com outcomes conflitantes) marcados via `shadow_trades.superseded_by_id` + `shadow_trade_duplicate_audit` (migration 109); índice único parcial `ux_shadow_trades_decision_id_canonical` criado (migration 110) após confirmar 0 grupos restantes. Total de linhas em `shadow_trades` apenas cresceu (13.540 → 13.591+) — nenhuma linha removida.
4. **Deploy realizado**: `git checkout main && git merge fix/profile-intelligence-adaptive-loop --ff-only && git push origin main` (commit base `872d7fe` → `faedd62`). Confirmado via Railway: os 6 serviços (`scalpyn`, 4 workers, `beat`) reiniciaram no novo commit e estão `RUNNING`. Confirmado via HTTP: `GET /api/ml/models/eligible` retorna `401` (rota existe, exige auth) em vez de `404` — prova decisiva de que o código está servindo requisições reais, não apenas "deployado" no sentido abstrato.
5. **Validação pós-deploy de lineage**: shadows criados após o deploy (`created_at >= 2026-06-25 00:18:37+00`) — **4 novos, 0 com `ml_model_id`/`model_lane`**. Isso é **esperado e correto**, não uma falha: `ML_GATE_ENABLED` continua `false` em produção (não alterado nesta sessão), então o bloco que computa e propaga o score ML para o Shadow nunca executa. O código está correto e implantado; a feature está, por desenho, desligada até decisão explícita de ligá-la.

**Conclusão do adendo:** o "Achado Central" da auditoria original ("nenhum código está em produção") **não é mais verdadeiro** a partir de `faedd62`. As perguntas "pode ligar ML Gate / promover modelo / promover profile / ativar live trading" **não foram re-decididas neste adendo** — permanecem como estavam (Não, Não, Não, Não) até nova validação explícita, agora que o enforcement do Promotion Gate está de fato no caminho de execução real assim que `ML_GATE_ENABLED=true` for setado. Pendente: item 7 (produtor real de `ml_opportunity_rankings`) e re-execução completa desta auditoria com `ML_GATE_ENABLED=true` em um ambiente controlado antes de ligar isso em produção.

---

## ACHADO CENTRAL (leia isto primeiro)

> **Nenhuma alteração de código desta implementação está rodando em produção.**

Evidência direta (Railway API, somente leitura, sem MCP — `railway status --json`):

```
service "scalpyn" (API) — activeDeployment:
  branch: "main"
  commitHash: "872d7fe7ef87ca0dd196c417a77f5d2443119243"
  status: RUNNING (instance 5883768b-ba33-458f-994e-c57fb1bc1798)
  createdAt: 2026-06-23T21:30:45Z
```

Evidência git (local):

```
$ git rev-parse HEAD
872d7fe7ef87ca0dd196c417a77f5d2443119243
$ git merge-base fix/profile-intelligence-adaptive-loop main
872d7fe7ef87ca0dd196c417a77f5d2443119243
$ git rev-parse --abbrev-ref --symbolic-full-name main@{u}
origin/main
$ git log origin/main..main --oneline   # vazio
$ git log main..origin/main --oneline   # vazio
$ git rev-parse --abbrev-ref --symbolic-full-name fix/profile-intelligence-adaptive-loop@{u}
fatal: no upstream configured for branch 'fix/profile-intelligence-adaptive-loop'
```

**Conclusão objetiva:** `HEAD` do branch de trabalho == `main` local == `origin/main` == commit rodando em produção. O branch nunca foi commitado (zero commits ahead) nem enviado ao remoto. Todo o trabalho desta implementação existe **apenas como alterações não-commitadas na working tree** desta máquina local.

**Isso significa, sem exceção, que:**
- Toda alteração em arquivos `.py` (model_lane, Promotion Gate, lineage no Shadow, Label Lab, Feedback Engine) está **IMPLEMENTADO MAS NÃO OPERACIONAL** — existe como código correto e testado, mas o processo Python que roda em produção (API, 5 workers Celery, beat) é o código de antes, sem nenhuma dessas mudanças.
- As **migrations 105–108 FORAM aplicadas de fato no banco de produção compartilhado** (mesmo Postgres que a API usa) — isso é real e não depende de deploy de aplicação, pois `alembic upgrade head` foi rodado localmente contra a `DATABASE_PUBLIC_URL` de produção.
- Os **scripts standalone** (`backfill_model_promotion_gate.py --commit`, `run_label_lab_report.py --commit`, `run_profile_suggestion_feedback.py --commit`, `consolidate_ml_pi_flags.sql`) **rodaram de fato e persistiram dados reais** no mesmo banco — isso também não depende de deploy, pois conectaram via `psycopg2` direto ao `DATABASE_PUBLIC_URL`.

Esta distinção — **schema + dados anotados = reais; código de aplicação = não deployado** — é a chave para interpretar corretamente cada item da matriz abaixo.

---

## FASE 1 — Git / Arquivos Alterados

| Item | Valor |
|---|---|
| Branch atual | `fix/profile-intelligence-adaptive-loop` |
| HEAD (commit) | `872d7fe7ef87ca0dd196c417a77f5d2443119243` |
| Commit base (premissa) | mesmo commit — branch nunca avançou via commit; declarado porque `git merge-base` confirma `main` == branch == HEAD |
| Commits ahead de main | **0** |
| Upstream do branch | **nenhum** (nunca pushado) |
| `git status --short` | 727 entradas (maioria pré-existente: `graphify-out/*` cache e `docs/runbooks/*.md` deletados, não relacionados a este lote) |

### Arquivos modificados (tracked, `M`) — relevantes a este lote

| Arquivo | Status |
|---|---|
| `backend/app/api/ml.py` | M — não commitado |
| `backend/app/api/watchlists.py` | M — não commitado |
| `backend/app/ml/gcs_model_loader.py` | M — não commitado |
| `backend/app/ml/prediction_service.py` | M — não commitado |
| `backend/app/schemas/watchlist_lineage_context.py` | M — não commitado |
| `backend/app/services/decision_orchestrator.py` | M — não commitado |
| `backend/app/services/ml_challenger_service.py` | M — não commitado |
| `backend/app/services/shadow_trade_service.py` | M — não commitado |
| `backend/app/tasks/pipeline_scan.py` | M — não commitado |

### Arquivos novos (untracked, `??`) criados nesta implementação

| Arquivo | Finalidade | Testes | Conectado a fluxo real? |
|---|---|---|---|
| `backend/alembic/versions/105_ml_opportunity_rankings.py` | Migration: cria `ml_opportunity_rankings` | n/a (migration) | **Aplicada no DB**; tabela vazia (sem produtor) |
| `backend/alembic/versions/106_shadow_ml_lineage.py` | Migration: `model_lane`/`ranking_id` em `shadow_trades` | n/a | **Aplicada no DB** |
| `backend/alembic/versions/107_label_lab_runs.py` | Migration: cria `label_lab_runs` | n/a | **Aplicada no DB** |
| `backend/alembic/versions/108_suggestion_shadow_feedback.py` | Migration: `shadow_feedback_*` em `profile_suggestions` | n/a | **Aplicada no DB** |
| `backend/app/ml/promotion_gate.py` | Lógica pura do Promotion Gate | `test_promotion_gate.py` (24) | Importado por `ml_challenger_service.py` (não deployado) e `api/ml.py` (não deployado) e pelo script de backfill (rodado manualmente) |
| `backend/app/services/profile_intelligence_label_lab.py` | Lógica pura do Label Lab | `test_label_lab.py` (12) | Importado só pelo script `run_label_lab_report.py` (rodado manualmente). **Não há job/endpoint que o chame.** |
| `backend/app/services/profile_suggestion_feedback_engine.py` | Lógica pura do Feedback Engine | `test_profile_suggestion_feedback_engine.py` (13) | Importado só pelo script `run_profile_suggestion_feedback.py` (rodado manualmente). **Não há job/endpoint que o chame.** |
| `backend/scripts/backfill_model_promotion_gate.py` | Script manual, `--dry-run`/`--commit` | manual, executado | Utilitário manual — não é chamado por nenhum job Celery |
| `backend/scripts/run_label_lab_report.py` | Script manual, `--commit` | manual, executado | Utilitário manual — não é chamado por nenhum job Celery |
| `backend/scripts/run_profile_suggestion_feedback.py` | Script manual, `--commit` | manual, executado | Utilitário manual — não é chamado por nenhum job Celery |
| `backend/sql/consolidate_ml_pi_flags.sql` | SQL idempotente, executado manualmente | n/a | Executado uma vez via psycopg2 |
| `backend/tests/test_label_lab.py` | 12 testes | — | — |
| `backend/tests/test_ml_lane_eligibility.py` | 11 testes | — | — |
| `backend/tests/test_profile_suggestion_feedback_engine.py` | 13 testes | — | — |
| `backend/tests/test_promotion_gate.py` | 24 testes | — | — |
| `backend/tests/test_shadow_ml_lineage.py` | 20 testes | — | — |

**Observação de honestidade:** `backend/scripts/run_lgbm_retrain.py` e vários arquivos em `docs/*.md` aparecem como `??` (untracked) mas **não foram criados nesta sessão** — são artefatos de trabalho anterior não commitado. Não atribuo essas linhas a este lote.

**Veredito Fase 1:** **IMPLEMENTADO MAS NÃO OPERACIONAL** para todo código de aplicação (os 9 arquivos `M` + os 2 services novos de lógica pura). Os scripts manuais e as migrations são **PASS** como "executados e persistidos", mas isso é diferente de "operacional no fluxo automático".

---

## FASE 2 — Migrations / Schema

Query (read-only):
```sql
SELECT * FROM alembic_version;
```
Resultado: `108_suggestion_feedback`

**Confirmado: head atual é `108_suggestion_feedback`.** ✅ PASS

Tabelas verificadas (`information_schema.tables`):

| Tabela pedida | Existe? |
|---|---|
| `label_lab_runs` | ✅ sim |
| `profile_intelligence_label_lab_runs` | ❌ não — **nome diferente do meu design** |
| `profile_intelligence_label_lab_results` | ❌ não — **não criei tabela de resultados separada; resultados ficam em `label_lab_runs.metrics`/`by_source` (JSONB)** |
| `profile_suggestion_feedback` | ❌ não — **implementei como colunas `shadow_feedback_status`/`shadow_feedback_json` direto em `profile_suggestions`, não como tabela separada** |
| `profile_suggestion_feedback_runs` | ❌ não |
| `ml_opportunity_rankings` | ✅ sim |
| `shadow_trade_duplicate_audit` | ❌ não — Fase 9 (dedup de `decision_id`) não foi implementada neste lote (decisão explícita do usuário de não priorizar) |
| `profile_intelligence_autopilot_candidates` | ✅ sim (pré-existente) |
| `profile_suggestions` | ✅ sim (pré-existente) |
| `shadow_trades` | ✅ sim (pré-existente) |
| `ml_models` | ✅ sim (pré-existente) |
| `profiles` | ✅ sim (pré-existente) |
| `real_orders` | ❌ não existe no schema — não há tabela de ordens reais nomeada assim |

**Veredito:** **PARCIAL** quanto à nomenclatura assumida pelo prompt (tabelas de resultado separadas não existem), mas **PASS** quanto à capacidade real: a persistência existe, só que via colunas JSONB em vez de tabelas dedicadas — desenho válido e equivalente em auditabilidade, documentado nas migrations 107/108.

Colunas confirmadas (`information_schema.columns`):

```
label_lab_runs: id uuid NOT NULL, label_version varchar NOT NULL, target_window_seconds int NOT NULL,
  source_filter varchar NULL, status varchar NOT NULL, reasons jsonb NOT NULL, thresholds jsonb NOT NULL,
  metrics jsonb NOT NULL, by_source jsonb NOT NULL, triggered_by varchar NULL, evaluated_at timestamptz NOT NULL

profile_suggestions (novas): shadow_feedback_status varchar NULL, shadow_feedback_json jsonb NULL

ml_opportunity_rankings: id, run_id, symbol, profile_id, watchlist_id, decision_id, model_lane, model_id,
  model_version, dataset_contract_id, promotion_gate_status, win_fast_probability, p_l1_win, p_l3_profile_win,
  final_priority_score, rank_position, score_status, reason_code, source, features_snapshot, ranked_at

shadow_trades (novas): model_lane varchar NULL, ranking_id uuid NULL (FK -> ml_opportunity_rankings.id)
```

Índices confirmados (`pg_indexes`):
```
label_lab_runs: ix_label_lab_runs_label_version_evaluated_at, label_lab_runs_pkey
ml_opportunity_rankings: ix_..._decision_id, ix_..._model_lane, ix_..._run_id, ix_..._symbol_ranked_at, pkey
shadow_trades: ix_shadow_trades_decision_id (NÃO é UNIQUE — ver Fase 14)
```

**Veredito Fase 2: PASS** (schema existe, está correto, suporta persistência; nomenclatura difere do prompt mas é funcionalmente equivalente).

---

## FASE 3 — Validação do Label Lab

Arquivos confirmados: `profile_intelligence_label_lab.py` (contém `is_win_fast_v1`, `is_tp_4h_v1`, `VIABLE`, `INSUFFICIENT_SAMPLES`, `DEGENERATE_CLASS_BALANCE` — grep confirmado), `run_label_lab_report.py` (contém `L1_SPECTRUM`, `L3_PROFILE_STRICT`), migration `107_label_lab_runs.py`, `test_label_lab.py`.

Query real executada (read-only):
```sql
SELECT label_version, target_window_seconds, source_filter, status,
  metrics->>'total_samples', metrics->>'positive_rate', metrics->>'distinct_profiles'
FROM label_lab_runs ORDER BY evaluated_at;
```

**Resultado real (4 linhas, todas persistidas em 2026-06-24 23:05:29 UTC, `triggered_by='run_label_lab_report.py'`):**

| label_version | window(s) | lane | status | total_samples | positive_rate | distinct_profiles |
|---|---|---|---|---|---|---|
| is_win_fast_v1 | 1800 | L1_SPECTRUM | VIABLE | 2020 | 0.1495 | 0 |
| is_tp_4h_v1 | 14400 | L1_SPECTRUM | VIABLE | 2020 | 0.4054 | 0 |
| is_win_fast_v1 | 1800 | L3_PROFILE_STRICT | VIABLE | 6583 | 0.0914 | 44 |
| is_tp_4h_v1 | 14400 | L3_PROFILE_STRICT | VIABLE | 6583 | 0.2836 | 44 |

Os 4 combos exigidos **existem, estão persistidos, têm amostra ≥ 2000 e positive_rate fora da zona degenerada (não próximo de 0 ou 1)**. Nenhum dos 4 tem `reasons` (campo vazio `[]`) — status `VIABLE` é resultado de threshold numérico (`min_total_samples=500`, `min_positive_rate=0.05`, `max_positive_rate=0.95`), não hardcoded.

`distinct_profiles=0` para L1_SPECTRUM é esperado e correto: shadows desse `source` não são `profile`-scoped por desenho (lane L1 não usa `profile_id`).

Testes (`pytest -v`, saída completa coletada):
```
backend/tests/test_label_lab.py::TestInsufficientSamples::test_few_samples_rejected_even_if_balanced PASSED
backend/tests/test_label_lab.py::TestInsufficientSamples::test_exactly_at_threshold_is_not_insufficient PASSED
backend/tests/test_label_lab.py::TestDegenerateClassBalance::test_almost_all_losses_is_degenerate PASSED
backend/tests/test_label_lab.py::TestDegenerateClassBalance::test_almost_all_wins_is_degenerate PASSED
backend/tests/test_label_lab.py::TestViablePath::test_balanced_large_sample_is_viable PASSED
backend/tests/test_label_lab.py::TestWindowSemantics::test_tp_hit_outside_window_counts_as_loss_not_win PASSED
backend/tests/test_label_lab.py::TestWindowSemantics::test_unlabelable_rows_excluded_not_guessed PASSED
backend/tests/test_label_lab.py::TestSourceFilter::test_source_filter_excludes_other_sources PASSED
backend/tests/test_label_lab.py::TestSourceFilter::test_no_source_filter_includes_all PASSED
backend/tests/test_label_lab.py::TestResultShape::test_result_has_required_keys PASSED
backend/tests/test_label_lab.py::TestResultShape::test_empty_rows_is_insufficient_not_a_crash PASSED
backend/tests/test_label_lab.py::TestMigration107::test_revision_chain PASSED
12 passed
```

**Veredito Fase 3: PASS.** Persistência real, rastreável por linha (não por `run_id` explícito — `label_lab_runs` não tem coluna `run_id`, identificação é por `(label_version, target_window_seconds, source_filter, evaluated_at)`; isso é uma limitação menor de rastreabilidade — recomendação abaixo).

---

## FASE 4 — Interpretação do achado do Label Lab

Validação da afirmação "o colapso de AUC de v41/v42 não é explicado por dataset pequeno ou class balance ruim":

1. Amostra ≥ 2000 nos 4 combos? **Sim** (2020 e 6583). ✅
2. `positive_rate` não próximo de 0/1? **Sim** (0.09–0.41). ✅
3. Ambos os lanes avaliados? **Sim** (L1_SPECTRUM e L3_PROFILE_STRICT). ✅
4. Dados reais de produção? **Sim** — query direta em `shadow_trades` com `status='COMPLETED'`, banco de produção via `DATABASE_PUBLIC_URL`. ✅
5. Resultado persistido comprova? **Sim**, tabela `label_lab_runs` consultada acima. ✅

**Veredito Fase 4: PASS** para a afirmação restrita.

Redação correta da conclusão (a única autorizada por esta auditoria):

> "O label `is_tp_4h_v1` e `is_win_fast_v1` são **treináveis/viáveis do ponto de vista de volume e balanceamento de classes**, em ambos os lanes. Isso **descarta** as hipóteses de dataset pequeno e class balance extremo como causa do colapso de AUC de teste em v41/v42. A **qualidade preditiva real ainda não foi validada** — permanecem abertas: features pouco preditivas, feature drift, split temporal inadequado, hiperparâmetros, threshold de decisão, mistura de setups heterogêneos no mesmo modelo, e risco de leakage/desalinhamento temporal entre treino e teste."

Esta auditoria **não aceita** a frase "o label é bom" e ela não aparece em nenhum artefato produzido.

---

## FASE 5 — Validação do Feedback Engine de Suggestions

Arquivos confirmados: `profile_suggestion_feedback_engine.py` (contém `NO_PROFILE_LINKED`, `POOR_PERFORMANCE`, `INSUFFICIENT_EVIDENCE`, `PROMOTE_CANDIDATE`), `run_profile_suggestion_feedback.py`, migration `108_suggestion_shadow_feedback.py`, `test_profile_suggestion_feedback_engine.py`.

Query real (read-only):
```sql
SELECT status, validation_status, actionability_status, COUNT(*)
FROM profile_suggestions GROUP BY 1,2,3 ORDER BY 4 DESC;
```
```
('exploratory_only', 'blocked_no_validation', 'exploratory_only', 99)
('applied',           'blocked_no_validation', 'exploratory_only', 2)
```

```sql
SELECT status, shadow_feedback_status, COUNT(*) FROM profile_suggestions GROUP BY 1,2 ORDER BY 3 DESC;
```
```
('exploratory_only', 'NO_PROFILE_LINKED',      99)
('applied',          'POOR_PERFORMANCE',        1)
('applied',          'INSUFFICIENT_EVIDENCE',   1)
```

```sql
SELECT COUNT(*), COUNT(*) FILTER (WHERE profile_id IS NOT NULL),
  COUNT(*) FILTER (WHERE created_profile_id IS NOT NULL),
  COUNT(*) FILTER (WHERE source_profile_ids IS NOT NULL AND jsonb_array_length(source_profile_ids)>0),
  COUNT(*) FILTER (WHERE shadow_feedback_status = 'NO_PROFILE_LINKED')
FROM profile_suggestions WHERE status='exploratory_only';
```
```
(99, 0, 0, 0, 99)
```

```sql
SELECT id, status, profile_id, created_profile_id, shadow_feedback_status,
  shadow_feedback_json->'metrics', applied_at
FROM profile_suggestions WHERE status='applied';
```
```
(d555023d-..., applied, profile_id=NULL, created_profile_id=88bdb40c-..., feedback=INSUFFICIENT_EVIDENCE, {wins:0, trades:0, win_rate:None}, applied_at=NULL)
(186184b1-..., applied, profile_id=NULL, created_profile_id=f86f47ae-..., feedback=POOR_PERFORMANCE,    {wins:6, trades:38, win_rate:0.1579}, applied_at=NULL)
```

```sql
SELECT COUNT(*) FROM profile_suggestions WHERE shadow_feedback_status='PROMOTE_CANDIDATE';
```
```
(0,)
```

```sql
SELECT COUNT(*), COUNT(*) FILTER (WHERE c.source_profile_ids IS NOT NULL AND jsonb_array_length(c.source_profile_ids)>0)
FROM profile_suggestions s JOIN profile_rule_combinations c ON c.id = s.source_combination_id
WHERE s.status='exploratory_only';
```
```
(99, 0)
```

Checklist do prompt (Fase 5), item por item, com evidência:

| # | Afirmação | Status | Evidência |
|---|---|---|---|
| 1 | 99 exploratory_only analisadas | PASS | `shadow_feedback_status` populado em 99/99 |
| 2 | profile_id NULL | PASS | `(99,0,...)` acima |
| 3 | source_profile_ids NULL/vazio | PASS | `(99,0,0,0,99)` acima |
| 4 | combinations de origem também vazias | PASS | `(99, 0)` — join confirma 0/99 com profiles |
| 5 | sistema marcou NO_PROFILE_LINKED honestamente | PASS | 99/99 |
| 6 | nenhum profile_id inferido por heurística | PASS | código (`resolve_profile_id_for_suggestion`) só lê 2 colunas diretas, sem fallback a combinations |
| 7 | nenhuma das 99 virou candidate | PASS (ver Fase 8 — nenhuma `profile_intelligence_autopilot_candidates.source_suggestion_id` aponta para elas, não houve mutação) |
| 8 | as 2 applied avaliadas | PASS | tabela acima |
| 9 | nenhuma virou PROMOTE_CANDIDATE | PASS | `COUNT=0` |
| 10 | uma POOR_PERFORMANCE | PASS | `186184b1...` |
| 11 | uma INSUFFICIENT_EVIDENCE | PASS | `d555023d...` |
| 12 | nada promovido automaticamente | PASS | nenhum `UPDATE status` ocorreu — script só escreveu as 2 colunas novas |

Testes (`pytest -v`):
```
13 passed (TestResolveProfileId x4, TestEvaluateSuggestionFeedback x6, TestNoProfileLinkedResult x1, TestMigration108 x2)
```

**Observação não-crítica:** `applied_at` é `NULL` para ambas as suggestions `applied` — dado histórico pré-existente, não causado por este lote, mas relevante para qualquer auditoria futura de quando a promoção ocorreu.

**Observação de rastreabilidade:** o `UPDATE` do script não atualizou `updated_at` em `profile_suggestions` — uma query que filtre por "alterado recentemente via `updated_at`" **não encontra** as 101 anotações (confirmado: 0 linhas na Fase 9 abaixo usando esse filtro). A evidência real está em `evaluated_at` dentro de `shadow_feedback_json`, não em `updated_at`. Recomendação: scripts futuros devem tocar `updated_at` explicitamente.

**Veredito Fase 5: PASS**, com a observação de rastreabilidade acima registrada como melhoria recomendada (não bloqueante).

---

## FASE 6 — Validação das 25 novas provas

```
$ pytest backend/tests/test_label_lab.py backend/tests/test_profile_suggestion_feedback_engine.py -v
...
25 passed in 0.11s
```

12 (Label Lab) + 13 (Feedback Engine) = 25. **25/25 passando, 0 falhas, 0 erros.**

**Veredito Fase 6: PASS.**

---

## FASE 7 — Regressão adjacente e pytest-asyncio

```
$ pytest backend/tests/test_create_profile_from_suggestion.py backend/tests/test_profile_intelligence_autopilot.py backend/tests/test_profile_intelligence_indicator_stats.py -q
...
37 failed, 45 passed
```

Causa raiz confirmada (exemplo isolado, `test_profile_intelligence_indicator_stats.py::test_indicator_stats_reject_invalid_run_id -v`):
```
async def functions are not natively supported.
You need to install a suitable plugin for your async framework, for example:
  - anyio
  - pytest-asyncio
  ...
PytestUnknownMarkWarning: Unknown pytest.mark.asyncio - is this a typo?
```

Confirmação ambiental:
```
$ pip show pytest-asyncio
WARNING: Package(s) not found: pytest-asyncio

$ grep -i "pytest" backend/requirements.txt backend/requirements-dev.txt
(nenhum resultado — pytest nem está pinado nos requirements; é dependência ad-hoc do ambiente local de dev)
```

Confirmação de não-causalidade: nenhum dos 3 arquivos de teste falhos está na lista de arquivos modificados desta sessão (Fase 1). A falha é estrutural de coleta (`ImportError`/plugin ausente), não uma asserção de lógica de negócio quebrada por código alterado.

**Veredito Fase 7: REGRESSÃO NÃO COMPROVADA POR AMBIENTE INCOMPLETO** — exatamente o status esperado pelo prompt. Não é PASS pleno porque a suíte real nunca rodou completa neste ambiente (não só nesta sessão — `pytest-asyncio` nunca esteve instalado). Recomendação: adicionar `pytest-asyncio` a `backend/requirements-dev.txt` e rodar a suíte completa antes de qualquer deploy.

---

## FASE 8 — Candidates / Shadow Validation existente

Schema real de `profile_intelligence_autopilot_candidates` (colunas confirmadas via `information_schema`):
```
approval_reason, approval_required, approval_snapshot_json, approval_source, approval_status, approved_at,
approved_by, canonical_rules_json, canonical_signature, created_at, cycle_id, decision_reason, evidence_json,
id, live_activated_at, live_activation_attempted_at, observed_avg_pnl_pct, observed_trades, observed_win_rate,
origin_profile_id, previous_profile_id, profile_id, promoted_at, promotion_avg_pnl_pct,
promotion_blocked_reason, promotion_win_rate, rejected_at, review_after, rollback_at, rollback_payload,
shadow_started_at, shadow_watchlist_id, source_combination_id, source_suggestion_id, state,
target_watchlist_id, updated_at, user_id, version_number
```

**Nota de schema:** as colunas `validation_status`, `candidate_profile_id`, `source_profile_id` assumidas pelo prompt **não existem** — os nomes reais são `state`, `profile_id`, e não há `source_profile_id` direto (há `origin_profile_id`/`previous_profile_id`). Adaptei as queries.

```sql
SELECT state, COUNT(*) FROM profile_intelligence_autopilot_candidates GROUP BY 1 ORDER BY 2 DESC;
```
```
('DISABLED',           61)
('SHADOW_COLLECTING',  30)
```

Validação item a item (código já existente em `profile_intelligence_autopilot_service.py`, **não alterado nesta sessão** — faz parte do commit `872d7fe`, já deployado):

| # | Item | Status | Evidência |
|---|---|---|---|
| 1 | Shadow Validation existe no código | PASS | `_review_shadow`, `promotion_decision`, `evaluation_ready` confirmados no arquivo (linhas citadas na investigação desta sessão: ~1867–1968) |
| 2 | Conectada a job/endpoint real | PASS | chamada por `run_cycle`, disparado pelo beat `profile_intelligence_engine` (schedule `PROFILE_INTELLIGENCE_INTERVAL_S`, default 86400s) — **e este código JÁ ESTÁ deployado (commit 872d7fe)**, diferente do resto deste lote |
| 3 | Possui testes | NÃO COMPROVADO nesta auditoria — não executei a suíte de `test_profile_intelligence_autopilot.py` com sucesso (bloqueada por `pytest-asyncio`, Fase 7) |
| 4 | `promotion_decision` existe | PASS | confirmado por leitura de código |
| 5 | Candidate ruim não é promovido | PARCIAL — lógica existe (`REJECT`/`COLLECT`/`APPROVE` por thresholds), mas não comprovei com teste executado nesta sessão (bloqueado por Fase 7) |
| 6 | Candidate bom não vira LIVE sem aprovação | PARCIAL — mesmo motivo: existe `PENDING_HUMAN_APPROVAL` antes de `LIVE_ACTIVATED`, mas sem teste executado com sucesso nesta auditoria |
| 7 | `DISABLED` tem significado claro | **FALHOU o critério de clareza** — confirmado ambíguo |

**Ambiguidade do estado DISABLED — confirmada:** o código usa `state="DISABLED"` tanto para eviction por capacidade (`_ensure_shadow_capacity`, quando `max_shadow_candidates` é excedido) quanto para outros encerramentos administrativos (`_reconcile_manual_changes` — profile deletado, watchlist reatribuída). As 61 linhas `DISABLED` na produção **não podem ser distinguidas por estado** entre "rejeitado por qualidade" e "descartado por falta de espaço" sem inspecionar `decision_reason`/`rollback_payload` individualmente.

**Veredito Fase 8: PARCIAL COM RISCO DE AMBIGUIDADE DE ESTADO**, exatamente conforme o critério do prompt. Recomendação registrada (separar em `DISABLED_BY_USER` / `REJECTED_POOR_PERFORMANCE` / `REJECTED_INSUFFICIENT_SAMPLE` / `EVICTED_CAPACITY`) **não implementada nesta sessão** — está fora do lote que foi priorizado (Label Lab + Feedback Engine).

---

## FASE 9 — Nenhuma promoção automática

```sql
SELECT version, status, model_lane, created_at, activated_at, metrics_json->'promotion_gate'->>'status'
FROM ml_models WHERE created_at >= '2026-06-24 20:00:00+00' OR activated_at >= '2026-06-24 20:00:00+00'
ORDER BY COALESCE(activated_at, created_at) DESC;
```
**Resultado: 0 linhas.** Nenhum modelo novo foi criado ou ativado desde o início do lote. O backfill do Promotion Gate (rodado nesta sessão) **só tocou `metrics_json`**, nunca `status`/`activated_at`/`created_at` — confirmado pelo próprio código do script e pela ausência de linhas aqui.

```sql
SELECT id, status, profile_id, created_profile_id, applied_at, created_at, updated_at
FROM profile_suggestions WHERE created_at >= '2026-06-24 20:00:00+00' OR updated_at >= '2026-06-24 20:00:00+00';
```
**Resultado: 0 linhas** (ver nota de rastreabilidade na Fase 5 — o `UPDATE` do Feedback Engine não tocou `updated_at`; a ausência de linhas aqui não indica ausência de atividade, e sim a limitação de rastreabilidade já registrada).

```sql
SELECT COUNT(*) FILTER (WHERE live_trading_enabled=true), COUNT(*) FILTER (WHERE auto_pilot_enabled=true), COUNT(*)
FROM profiles;
```
```
(0, 0, 109)
```

**Veredito Fase 9: PASS.** Nenhuma promoção automática de modelo ou de profile ocorreu. Nenhuma suggestion `exploratory_only`/`applied` foi criada/alterada por engano. Nenhum profile tem `live_trading_enabled` ou `auto_pilot_enabled` ligado.

---

## FASE 10 — Nenhum live trading

```sql
SELECT COUNT(*) FILTER (WHERE live_trading_enabled=true), COUNT(*) FILTER (WHERE auto_pilot_enabled=true), COUNT(*) FROM profiles;
```
```
(0, 0, 109)
```

```sql
SELECT config_type, config_json->>'dry_run_mode', config_json->>'autopilot_full_authority',
  config_json->>'enable_catboost', config_json->>'enable_lightgbm'
FROM config_profiles WHERE config_type IN ('ml','profile_intelligence','autopilot_guardrails','orchestrator_weights');
```
```
('autopilot_guardrails',   dry_run_mode='false', autopilot_full_authority='true', -, -)
('ml',                     -, -, NULL, NULL)   -- chaves mortas removidas nesta sessão (Fase 3)
('profile_intelligence',   -, -, 'false', 'false')
```

**⚠️ Achado de risco PRÉ-EXISTENTE (não causado por este lote, mas relevante à pergunta "pode ativar live trading"):** `config_profiles.config_type='autopilot_guardrails'` tem `dry_run_mode='false'` e `autopilot_full_authority='true'`. Isso significa que, **se algum profile tivesse `auto_pilot_enabled=true`**, o Auto-Pilot (`autopilot_engine.py`, sistema pré-existente, não tocado nesta sessão) escreveria mudanças de configuração reais em vez de simular. **Mitigante confirmado:** `auto_pilot_enabled=true` count = **0/109** — o gate está com `dry_run_mode=false`, mas está **inerte** porque nenhum profile o aciona agora. Não há `orchestrator_weights` na lista retornada — **`config_type='orchestrator_weights'` não existe** (Fase 3 do plano original previa criá-lo; não foi feito nesta sessão).

`ML_GATE_ENABLED`: confirmado via Railway CLI (`railway variables --service scalpyn ...`) — **variável não definida** no ambiente de produção → cai no default do código, `"false"` (`pipeline_scan.py:2878`). Override via `pool_config.new_arch_l3_uses_ml_score` também não existe (`SELECT COUNT(*) FROM config_profiles WHERE config_type='pool_config'` → `0`).

Não existe tabela `real_orders` no schema — não há evidência (nem ausência de evidência relevante) de ordens reais, porque o conceito de "ordem real" neste sistema spot é mediado por `live_trading_enabled` em `profiles`, já confirmado `0/109`.

**Veredito Fase 10: PASS** para o objetivo desta auditoria (nenhum live trading ativo, nenhuma ordem real). **Risco residual pré-existente documentado** (`autopilot_guardrails.dry_run_mode=false` + `autopilot_full_authority=true`) — recomendo ao usuário revisar isso separadamente; está fora do escopo deste lote, mas é um risco real e presente no banco hoje, independentemente desta implementação.

---

## FASE 11 — ML Opportunity Ranking

```sql
SELECT COUNT(*) FROM ml_opportunity_rankings;
```
```
(0,)
```

A tabela existe (migration 105, Fase 4 do lote anterior), tem schema completo (score, lane, modelo, reason/risk code via `score_status`/`reason_code`), mas **não há nenhum produtor** — o "ML Opportunity Ranking job" (Fase 6 do plano original) nunca foi implementado nesta sessão.

**Veredito Fase 11: PARCIAL** (tabela existe, schema correto, **zero dados** — exatamente o critério "PARCIAL" do prompt). Registrar como **pendente explícito**, não como concluído.

---

## FASE 12 — Shadow Lineage

```sql
SELECT COUNT(*), COUNT(*) FILTER (WHERE ml_model_id IS NOT NULL), COUNT(*) FILTER (WHERE ml_probability IS NOT NULL),
  COUNT(*) FILTER (WHERE model_lane IS NOT NULL), COUNT(*) FILTER (WHERE final_priority_score IS NOT NULL)
FROM shadow_trades WHERE created_at >= '2026-06-24 20:00:00+00';
```
```
(833, 0, 0, 0, 0)
```

**833 shadow trades novos foram criados em produção durante a janela desta implementação — e nenhum deles (0/833) tem `ml_model_id`, `ml_probability`, `model_lane` ou `final_priority_score` preenchidos.**

Isso **confirma diretamente, com dados de produção em tempo real**, o achado central desta auditoria: o código de lineage escrito nesta sessão (`shadow_trade_service.py`/`pipeline_scan.py`) **não está rodando** — a deployment ativa (commit `872d7fe`) não o contém, então os 833 shadows criados pelo pipeline real seguiram o caminho antigo, sem lineage.

```sql
SELECT COUNT(*), COUNT(*) FILTER (WHERE ml_model_id IS NOT NULL), COUNT(*) FILTER (WHERE model_lane IS NOT NULL)
FROM shadow_trades;  -- histórico completo, qualquer data
```
```
(13540, 0, 0)
```

Nem um único shadow trade, em todo o histórico (13.540 linhas), tem `ml_model_id` ou `model_lane` preenchido — confirma que mesmo o backfill assíncrono manual (`/api/ml/orchestrator/backfill`, mencionado na memória do projeto) nunca foi executado, e que a coluna `model_lane` (recém-criada, migration 106) está 100% NULL, como esperado para uma coluna nova sem deploy do código que a popularia.

**Veredito Fase 12: FALHOU o critério operacional (0% lineage em shadows novos)**, mas com a ressalva técnica correta: o motivo é "código não deployado", não "lógica quebrada" (os 39 testes de `test_shadow_ml_lineage.py`/`test_ml_lane_eligibility.py` comprovam a lógica em isolamento). Classificação correta segundo a taxonomia do prompt: **IMPLEMENTADO MAS NÃO OPERACIONAL.**

---

## FASE 13 — features_snapshot_exit

```sql
SELECT COUNT(*) FROM shadow_trades WHERE created_at >= '2026-06-24 20:00:00+00' AND status='COMPLETED' AND features_snapshot_exit IS NULL;
```
```
(44,)
```
```sql
SELECT COUNT(*) FROM shadow_trades WHERE status='COMPLETED' AND features_snapshot_exit IS NULL;  -- histórico total
```
```
(394,)
```

44 trades `COMPLETED` desde o início do lote ainda têm `features_snapshot_exit IS NULL`, sem marcador `_capture_failed` necessariamente presente (não testado neste lote — Fase 13 do plano original, item 10 do plano de 23 fases, **não foi implementada nesta sessão**; nenhum arquivo relacionado a isso (`exit_metrics.py` ou equivalente) foi tocado).

**Veredito Fase 13: NÃO COMPROVADO / PENDENTE.** Esta fase não fez parte do lote priorizado pelo usuário e não há código desta sessão relacionado a ela. Risco operacional continua presente e não-mitigado (mas também não-agravado por este lote).

---

## FASE 14 — Duplicidade de decision_id

```sql
SELECT decision_id, COUNT(*) n, COUNT(DISTINCT outcome) distinct_outcomes
FROM shadow_trades WHERE decision_id IS NOT NULL GROUP BY decision_id HAVING COUNT(*)>1 ORDER BY n DESC LIMIT 10;
```
```
(4225, 4, 2)   (4222, 4, 2)   (4252, 3, 2)   (4366, 3, 1)   (1785, 2, 2)
(2083, 2, 2)   (1023, 2, 1)  (2501, 2, 2)   (2380, 2, 2)
```
```sql
SELECT COUNT(*) FROM (SELECT decision_id FROM shadow_trades WHERE decision_id IS NOT NULL GROUP BY decision_id HAVING COUNT(*)>1) t;
```
```
(38,)
```

```sql
SELECT indexname, indexdef FROM pg_indexes WHERE tablename='shadow_trades' AND indexdef ILIKE '%UNIQUE%';
```
```
shadow_trades_pkey                       UNIQUE (id)
uq_shadow_lab_profile_symbol_bucket       UNIQUE (profile_id, symbol, source, hour_bucket) WHERE profile_id IS NOT NULL
ux_shadow_running_user_source             UNIQUE (user_id, symbol, source) WHERE status='RUNNING' AND profile_id IS NULL
uq_shadow_lab_active_profile_symbol       UNIQUE (profile_id, symbol, source) WHERE profile_id IS NOT NULL AND status IN ('RUNNING','PENDING')
```

**Achado crítico de documentação incorreta (pré-existente, não introduzido nesta sessão):** o docstring de `_create_from_decision` em `shadow_trade_service.py` afirma "idempotente via `ON CONFLICT (decision_id) DO NOTHING` (UNIQUE INDEX criado na migration 047)". **Não existe nenhum índice UNIQUE sobre `decision_id`** no banco atual — apenas um índice não-único (`ix_shadow_trades_decision_id`). O `ON CONFLICT DO NOTHING` na query de INSERT não especifica coluna de conflito, então só protege contra violações dos OUTROS índices únicos acima (lab dedup / running-symbol dedup) — **nunca protegeu `decision_id`**. Isso explica os 38 `decision_id` duplicados encontrados, vários (`distinct_outcomes=2`) com **outcomes conflitantes** (o mesmo decision contabilizado como TP em uma linha e SL em outra) — risco real de contaminação de dataset de treino.

**Veredito Fase 14: FALHOU o critério "detecta duplicidade, mas ainda não bloqueia novos casos"** — na verdade está pior: nem detecta (não há tabela de auditoria `shadow_trade_duplicate_audit`) nem bloqueia (índice não é único). Esta é a Fase 9 do plano de 23 fases original, **explicitamente não priorizada pelo usuário nesta sessão** (removida da lista de tarefas por decisão direta). Status correto: **PENDENTE, NÃO ENDEREÇADO, RISCO PRÉ-EXISTENTE CONFIRMADO E QUANTIFICADO (38 casos, ≥7 com outcomes conflitantes).**

---

## FASE 15 — Promotion Gate / Elegibilidade de modelos

```sql
SELECT id, version, status, model_lane, metrics_json->'promotion_gate'->>'status' AS gate_status,
  metrics_json->'promotion_gate'->'metrics'->>'test_roc_auc' AS test_roc_auc
FROM ml_models WHERE status='active';
```
```
(v44, active, L3_PROFILE,  gate=REJECTED, test_roc_auc=0.4260)
(v46, active, L1_SPECTRUM, gate=REJECTED, test_roc_auc=0.4546)
```

Os dois únicos modelos `active` em produção **têm `test_roc_auc < 0.5`** (pior que aleatório) e **o Promotion Gate os classifica como `REJECTED`**, gravado em `metrics_json.promotion_gate` (migrations + backfill desta sessão). **Importante:** `status` continua `active` — o Gate **anota** elegibilidade, não promove nem despromove (conforme regra absoluta do projeto: nunca editar `status` automaticamente).

**Risco real não-mitigado, confirmado com dados:** como o **código que filtra por `promotion_gate.status='APPROVED'`** (em `gcs_model_loader.py`/`prediction_service.py`) **não está deployado** (Achado Central), qualquer chamada de produção a esses dois modelos hoje **ainda os usa normalmente**, porque o código deployado (commit `872d7fe`) não tem o filtro do Gate — ele simplesmente lê "o modelo `active` mais recente" sem checar `promotion_gate`. Em outras palavras: **o Gate existe e está correto na anotação, mas não protege nada ainda, porque o enforcement (o código que lê o Gate antes de usar o modelo) não foi deployado.**

Verificação de código (não-deployado, mas correto): `gcs_model_loader._load_from_db` adiciona `_lane_clause = "AND model_lane = %s AND metrics_json->'promotion_gate'->>'status' = 'APPROVED'"` quando `model_lane` é passado; `NoEligibleModelError` é levantado quando nenhuma linha casa. `prediction_service.predict()` captura essa exceção e retorna `reason_code='NO_ELIGIBLE_MODEL_FOR_LANE'`. Isso está testado (`test_ml_lane_eligibility.py`, 11/11 passando) mas **não operacional** (Achado Central).

**Veredito Fase 15: PARCIAL — "modelo ativo ruim existe, mas filtro operacional NÃO impede uso ainda"** (porque o filtro não está deployado). Esta é exatamente a categoria "PARCIAL" do prompt, na pior das duas variantes possíveis dela.

---

## FASE 16 — Configurações

```sql
SELECT config_type, config_json->>'enable_catboost', config_json->>'enable_lightgbm'
FROM config_profiles WHERE config_type IN ('ml','profile_intelligence');
```
```
('ml',                   NULL,    NULL)     -- chaves mortas REMOVIDAS nesta sessão (Fase 3, consolidate_ml_pi_flags.sql)
('profile_intelligence', 'false', 'false')  -- única fonte de verdade, lida de fato pelo código (profile_intelligence_job.py:148-157)
```

`ml_win_fast_threshold_seconds`: confirmado em ambos os `config_type` como `14400` (já sincronizado, correção de sessão anterior, não desta).

`orchestrator_weights`: **`config_type='orchestrator_weights'` não existe** em `config_profiles` — confirmado por ausência na query da Fase 9/16. `decision_orchestrator.py` (não alterado nesta sessão) usa pesos hardcoded (`0.60`/`0.40`) conforme documentado na memória do projeto — não há fallback documentado com warning explícito verificado nesta auditoria.

`dry_run_mode`/`autopilot_full_authority`: ver Fase 10 (achado de risco pré-existente).

**Veredito Fase 16: PASS** para o que foi escopo desta sessão (divergência `enable_catboost`/`enable_lightgbm` eliminada, com evidência). **NÃO COMPROVADO** para `orchestrator_weights` (não criado, não documentado como pendente em nenhum doc formal até este relatório).

---

## FASE 17 — Endpoints

Endpoints novos (não deployados — ver Achado Central):

| Endpoint | Método | Arquivo | Auth | Filtra user_id nos dados? | Escreve no banco? | Audit log? | Teste |
|---|---|---|---|---|---|---|---|
| `/api/ml/models/eligible` | GET | `api/ml.py:441` | `Depends(get_current_user_id)` | Não — `ml_models` é recurso global da plataforma, não por usuário (consistente com todos os outros endpoints de `ml_models` já existentes) | Não | n/a (leitura) | Não há teste de endpoint dedicado (só da lógica subjacente) |
| `/api/ml/models/{id}/evaluate-promotion-gate` | POST | `api/ml.py:488` | `Depends(get_current_user_id)` | Não (mesmo motivo acima) | **Sim** — `UPDATE ml_models SET metrics_json=...` | **Sim** — `log_pi_event(..., event_type="ML_PROMOTION_GATE_EVALUATED", before_json, after_json, diff_json)` | Não há teste de endpoint dedicado |

**Veredito Fase 17: PASS quanto a auth e audit trail; NÃO COMPROVADO quanto a teste de endpoint (só a lógica interna do Gate tem os 24 testes; o endpoint HTTP em si não foi exercitado por teste de integração nesta sessão).** Não há endpoints de Label Lab/Feedback Engine — ambos só têm scripts manuais, sem rota HTTP (confirma Fase 1: IMPLEMENTADO MAS NÃO OPERACIONAL, nem como endpoint).

---

## FASE 18 — UI

Não verificado nesta auditoria por busca de código frontend dedicada; busca rápida confirma que nenhuma tela em `frontend/` foi alterada nesta sessão (arquivos modificados, Fase 1, são todos `backend/`). **Veredito: NÃO COMPROVADO / FALTANTE.** Conforme o critério do prompt ("Backend sem UI pode ser PARCIAL, não FAIL, se o fluxo operacional estiver correto") — mas como o backend também não está operacional (Achado Central), a classificação composta é **PARCIAL tendendo a NÃO OPERACIONAL**: não há UI para Label Lab, Feedback Engine, Promotion Gate ou Shadow lineage ML.

---

## FASE 19 — Documentação

- `docs/AUDITORIA_COMPLETA_POOL_L1_L3_SHADOW_ML_PI_AUTOPILOT_2026-06-24.md` — auditoria read-only original (Fase A). Existe.
- `docs/IMPLEMENTATION_BASELINE_PROFILE_INTELLIGENCE_ADAPTIVE_LOOP.md` — baseline da Fase 0. Existe.
- **Não existe** `PROFILE_INTELLIGENCE_ADAPTIVE_LOOP.md` nem `ADAPTIVE_LOOP_ACCEPTANCE_TESTS.md` dedicados — a documentação de cada fase ficou nos próprios docstrings de migration/módulo, não consolidada em um doc único de aceite, até este relatório.
- Pendências (ML Opportunity Ranking vazio, Shadow Lineage não-operacional, Candidate Shadow Validation parcial, `DISABLED` ambíguo, Promotion Gate não-enforced, regressão pytest-asyncio incompleta) **não estavam documentadas formalmente em nenhum doc antes desta auditoria** — só existiam nos resumos de chat.

**Veredito Fase 19: PARCIAL.** Os docstrings de código são precisos e não exageram o que foi feito (boa prática mantida), mas faltava um documento de aceite formal consolidando o estado real — este relatório agora cumpre esse papel.

---

## FASE 20 — Matriz Final de Aceite

| # | Item | Status | Evidência (resumo) |
|---|---|---|---|
| 1 | Migrations 105–108 aplicadas | **PASS** | `alembic_version` = `108_suggestion_feedback` |
| 2 | Head Alembic confirmado | **PASS** | idem |
| 3 | Label Lab implementado | **PASS** | `profile_intelligence_label_lab.py` + testes |
| 4 | Label Lab testado | **PASS** | 12/12 |
| 5 | Label Lab executado em produção | **PASS** | `triggered_by='run_label_lab_report.py'` em `label_lab_runs` |
| 6 | Label Lab persistiu resultados | **PASS** | 4 linhas em `label_lab_runs` |
| 7 | Os 4 combos são VIABLE | **PASS** | tabela Fase 3 |
| 8 | Conclusão "não é volume/balanceamento" comprovada | **PASS** | Fase 4 |
| 9 | Feedback Engine implementado | **PASS** | `profile_suggestion_feedback_engine.py` |
| 10 | Feedback Engine testado | **PASS** | 13/13 |
| 11 | Feedback Engine executado em produção | **PASS** | 101 linhas anotadas |
| 12 | Feedback Engine persistiu resultados | **PASS** | colunas `shadow_feedback_*` populadas |
| 13 | 99 exploratory_only = NO_PROFILE_LINKED sem inferência | **PASS** | Fase 5 |
| 14 | 2 applied avaliadas | **PASS** | Fase 5 |
| 15 | Nenhuma suggestion virou PROMOTE_CANDIDATE indevidamente | **PASS** | `COUNT=0` |
| 16 | Nenhuma promoção automática ocorreu | **PASS** | Fase 9 |
| 17 | Novos testes 25/25 passando | **PASS** | Fase 6 |
| 18 | Regressão adjacente validada/marcada | **PASS (como NÃO COMPROVADA POR AMBIENTE)** | Fase 7 |
| 19 | Shadow Validation de candidates validada/PARCIAL | **PARCIAL** | Fase 8 — código existe e já estava deployado, mas não testado nesta sessão (bloqueio pytest-asyncio) |
| 20 | DISABLED ambíguo documentado | **PASS** (documentado agora) / risco **NÃO corrigido** | Fase 8 |
| 21 | ML Opportunity Ranking validado/pendente | **PARCIAL (pendente, tabela vazia)** | Fase 11 |
| 22 | Shadow lineage validado/pendente | **FALHOU operacionalmente / IMPLEMENTADO MAS NÃO OPERACIONAL** | Fase 12 — 0/833 novos shadows com lineage |
| 23 | Promotion Gate validado/pendente | **PARCIAL** — anotado mas não enforced (não deployado) | Fase 15 |
| 24 | features_snapshot_exit validado | **NÃO COMPROVADO / fora do escopo desta sessão** | Fase 13 |
| 25 | decision_id duplicado validado | **FALHOU (pendente, quantificado: 38 casos, índice não é único)** | Fase 14 |
| 26 | Nenhum live trading ativado | **PASS** | Fase 10 |
| 27 | Nenhum profile ACTIVE alterado diretamente | **PASS** | nenhum `UPDATE profiles`/`config_profiles` de profile específico nesta sessão, exceto a remoção das chaves mortas em `config_type='ml'` (config global, não um profile específico) |
| 28 | Nenhum modelo promovido indevidamente | **PASS** | Fase 9 |
| 29 | Nenhum profile promovido indevidamente | **PASS** | Fase 9 |
| 30 | Git limpo ou alterações listadas | **PASS (listadas)** | Fase 1 — nada commitado, tudo listado |

---

## FASE 21 — Veredito Final

### **APROVADO COM RESSALVAS**

Label Lab e Feedback Engine **passaram integralmente** nos critérios que lhes cabiam (implementados, testados 25/25, executados em produção, persistidos, sem inferência, sem promoção automática). Isso é real e verificado com evidência direta de banco.

Mas a implementação como um todo carrega pendências que impedem um "APROVADO" pleno:

1. **Nenhum código de aplicação está deployado** — todo o ganho de segurança do Promotion Gate, model_lane e Shadow Lineage existe apenas localmente, não operacional em produção (confirmado com 0/833 shadows novos com lineage).
2. **Promotion Gate não tem enforcement ativo** — os dois modelos `active` (v44, v46) com AUC de teste < 0.5 continuam sendo servidos normalmente pelo código hoje em produção.
3. **decision_id duplicado** é pior do que documentado anteriormente — o índice único citado em comentários de código não existe; 38 casos confirmados, vários com outcomes conflitantes.
4. **DISABLED ambíguo** em candidates, confirmado, não corrigido.
5. **ML Opportunity Ranking** é uma tabela vazia — pendente integral.
6. **Regressão adjacente** não pôde ser comprovada por ausência de `pytest-asyncio` no ambiente — risco residual de que mudanças futuras quebrem `profile_intelligence_autopilot_service.py` sem detecção automática.
7. **Risco pré-existente de configuração** (`autopilot_guardrails.dry_run_mode=false`) identificado e registrado, embora inerte hoje (`auto_pilot_enabled=0/109`).

Nenhum desses pontos é uma falha crítica de segurança operacional **hoje** (live trading=0, promoção automática=0, profile ACTIVE não tocado), mas todos são bloqueadores para classificar o lote como "pronto para produção" sem mais trabalho.

---

### Perguntas obrigatórias

| Pergunta | Resposta |
|---|---|
| 1. A implantação está aprovada? | **Aprovada com ressalvas** (ver lista acima) |
| 2. Pode prosseguir para Shadow Validation ampliada? | **Não ainda** — primeiro corrigir Fase 7 (pytest-asyncio) para poder testar `_review_shadow`/`promotion_decision` com confiança |
| 3. Pode ligar ML Gate? | **Não** — `ML_GATE_ENABLED` está corretamente `false`/não-definido hoje; ligá-lo agora exporia os 2 modelos `active` rejeitados pelo Gate, porque o enforcement do Gate não está deployado |
| 4. Pode promover algum modelo? | **Não** — 0 modelos aprovados pelo Gate (47/47 avaliados, 0 APPROVED) |
| 5. Pode promover algum profile? | **Não** — 0 suggestions com `PROMOTE_CANDIDATE`; as 2 `applied` existentes já foram avaliadas como `POOR_PERFORMANCE`/`INSUFFICIENT_EVIDENCE` |
| 6. Pode ativar live trading? | **NÃO, salvo autorização explícita posterior do usuário.** Confirmado: 0/109 profiles com `live_trading_enabled=true` hoje. |

---

### Pode prosseguir para o próximo lote? **Sim**, com a condição de que as pendências P0 (deploy do código, decision_id duplicado, regressão pytest-asyncio) sejam tratadas antes de qualquer enforcement real do Promotion Gate em produção.

### Pode commitar 105–108? **Sim, do ponto de vista técnico** — as migrations estão aplicadas, testadas via schema real, e são aditivas/reversíveis (todas têm `downgrade()`). A decisão de commit/push continua sendo do usuário (nunca executei `git commit`/`push` nesta sessão nem nesta auditoria).

### Pode ligar `ML_GATE_ENABLED`? **Não**, pelos motivos da pergunta 3.

### Pode promover modelo? **Não.**

### Pode promover profile? **Não.**

### Pode ativar live trading? **Não, salvo autorização explícita posterior do usuário.**

---

## Pendências (ordenadas por risco)

1. **P0 — Nada está deployado.** Sem commit + push + deploy no Railway, todo o trabalho de Fase 1/2/8 (model_lane, Promotion Gate enforcement, Shadow Lineage) permanece teórico.
2. **P0 — `decision_id` duplicado** (38 casos, outcomes conflitantes) — risco direto de contaminação de dataset de ML, sem mitigação no código atual nem no deployado.
3. **P1 — `pytest-asyncio` ausente do ambiente** — impede validar a suíte de candidates/autopilot/suggestions; instalar e re-rodar antes do próximo lote.
4. **P1 — `DISABLED` ambíguo** em `profile_intelligence_autopilot_candidates` — 61 linhas hoje sem causa-raiz distinguível por estado.
5. **P2 — ML Opportunity Ranking** — tabela existe, zero produtor; pendência integral da Fase 6 do plano original.
6. **P2 — `config_type='orchestrator_weights'`** não criado — pesos do orchestrator continuam hardcoded sem fallback documentado.
7. **P2 — `features_snapshot_exit`** — 394 trades históricos (44 desde o início do lote) `COMPLETED` sem snapshot de saída; fora do escopo desta sessão.
8. **P3 — Risco de configuração pré-existente:** `autopilot_guardrails.dry_run_mode=false` + `autopilot_full_authority=true`, hoje inerte (`auto_pilot_enabled=0/109`) mas merece decisão explícita do usuário sobre se deve voltar a `true` por padrão.

## Riscos confirmados como ausentes (evidência positiva)

- Live trading: **0/109** profiles.
- Auto-Pilot ativo: **0/109** profiles.
- Promoção automática de modelo: **0** eventos na janela do lote.
- Promoção automática de profile/suggestion: **0** eventos na janela do lote.
- Inferência de `profile_id` sem evidência: **0** ocorrências (código não tem fallback heurístico).
- ML Gate ligado em produção: **não** (env var ausente, fallback `pool_config` ausente).

---

## Checklist Go/No-Go

- [x] Migrations aplicadas e revertíveis
- [x] Scripts manuais com `--dry-run` por padrão, `--commit` explícito
- [x] 25/25 testes novos passando
- [x] Nenhuma promoção/ativação automática
- [x] Nenhum live trading
- [ ] Código de aplicação deployado (**bloqueador para "operacional"**)
- [ ] `decision_id` duplicado corrigido
- [ ] `pytest-asyncio` instalado e regressão completa rodada
- [ ] `DISABLED` desambiguado
- [ ] ML Opportunity Ranking com produtor real
