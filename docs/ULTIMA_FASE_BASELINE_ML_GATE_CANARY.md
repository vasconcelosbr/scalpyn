# Última Fase — Baseline Pré-Teste (ML Gate Canary)

**Data/hora:** 2026-06-25 ~01:00 UTC
**Modo:** read-only (todas as queries em `BEGIN; SET TRANSACTION READ ONLY;` + `ROLLBACK`)

## 1. Git

```
HEAD            = 4a4c999713ae51389bd22918ac1d60f957390217
branch          = main
status          = limpo (apenas ruído pré-existente não relacionado: .codex/, AGENTS.md, docs/* não desta sessão)
```

Log recente (10):
```
4a4c999 feat(ml): wire ML Opportunity Ranking producer into the L3 ML gate
00c6279 docs: add post-deploy addendum to VALIDACAO_GERAL report
faedd62 fix(shadow): resolve decision_id duplicates via audit + non-destructive marking
9f71865 test: declare pytest/pytest-asyncio/fakeredis as explicit dev dependencies
68dc216 feat(ml,profile-intelligence): Promotion Gate, model_lane, Shadow lineage, Label Lab, Suggestion Feedback Engine
872d7fe fix(shadow-portfolio): ProfileReportTable overflow e enquadramento de colunas
```

## 2. Deploy (Railway)

- Projeto `scalpyn` (`a3af94be-bbb5-413b-a1bd-c1f0a5db0ee5`), environment **único**: `production` (`8e7bba37-1dc2-4f78-b549-248bbb3ec29d`). **Não existe ambiente de staging neste projeto Railway.**
- Todos os 6 serviços de aplicação confirmados `RUNNING` no commit `4a4c999...` (verificado na fase anterior, reconfirmado aqui via `railway status`).

## 3. Variáveis de ambiente relevantes (serviço `scalpyn`, produção)

| Variável | Valor |
|---|---|
| `ML_GATE_ENABLED` | **não definida** → default do código = `"false"` |
| `LIVE_TRADING_ENABLED` | não definida (controle real é por linha em `profiles.live_trading_enabled`, não por env var global) |
| `AUTO_PROMOTION_ENABLED` | não definida — **não existe no código**; não há mecanismo de auto-promoção de modelo de qualquer forma (Promotion Gate só anota `metrics_json`, nunca `status`) |
| `PROFILE_MUTATION_ENABLED` | não definida — **não existe no código** |
| `ML_RANKING_SHADOW_ONLY` | não definida — **não existe no código**; o ranking já É shadow-only por desenho (Fase 7 do lote anterior: a tabela só recebe INSERT, nunca é usada para decisão real) |
| `CANARY_SYMBOL_LIMIT` | não definida — **não existe no código**. Não há hoje nenhum mecanismo para limitar o ML Gate a um subconjunto de símbolos. Ligar `ML_GATE_ENABLED=true` afeta **todos** os watchlists L3 simultaneamente. |

**Achado importante:** as variáveis `AUTO_PROMOTION_ENABLED`, `PROFILE_MUTATION_ENABLED`, `ML_RANKING_SHADOW_ONLY`, `CANARY_SYMBOL_LIMIT` listadas na especificação desta fase **não existem no código atual**. Implementá-las seria trabalho novo, não validação do que já existe. Isso é tratado explicitamente na próxima decisão (ver mensagem de acompanhamento).

## 4. Migrations

```sql
SELECT * FROM alembic_version;
-- ('110_shadow_decision_unique',)
```
Head atual = `110_shadow_decision_unique`. **Critério "head 110 ou superior" satisfeito.** Nenhuma migration pendente.

## 5. Live trading / Auto-Pilot

```sql
SELECT COUNT(*) FILTER (WHERE live_trading_enabled=true), COUNT(*) FILTER (WHERE auto_pilot_enabled=true), COUNT(*) FROM profiles;
-- (0, 0, 109)
```
**0/109 profiles com live trading ou auto-pilot ativos.** Critério satisfeito.

## 6. Modelos ML

```sql
SELECT version, status, model_lane, metrics_json->'promotion_gate'->>'status', metrics_json->'promotion_gate'->'metrics'->>'test_roc_auc'
FROM ml_models WHERE status='active';
-- ('44', 'active', 'L3_PROFILE',  'REJECTED', '0.42603030303030304')
-- ('46', 'active', 'L1_SPECTRUM', 'REJECTED', '0.4545600858369099')
```
**v44 e v46 confirmados `REJECTED` pelo Promotion Gate.** Critério satisfeito.

```sql
SELECT COUNT(*) FROM ml_models WHERE metrics_json->'promotion_gate'->>'status'='APPROVED';
-- (0,)
```
**Nenhum modelo `APPROVED` existe hoje.**

## 7. ml_opportunity_rankings

```sql
SELECT COUNT(*) FROM ml_opportunity_rankings;
-- (0,)
```
Tabela existe (migration 105) e tem produtor real (commit `4a4c999`), mas está vazia — esperado, pois o produtor só executa dentro do bloco `if _ml_gate_enabled:` e `ML_GATE_ENABLED=false`.

## 8. Shadow lineage

```sql
SELECT COUNT(*), COUNT(*) FILTER (WHERE ranking_id IS NOT NULL), COUNT(*) FILTER (WHERE ml_model_id IS NOT NULL), COUNT(*) FILTER (WHERE model_lane IS NOT NULL)
FROM shadow_trades;
-- (13617, 0, 0, 0)
```
13.617 shadow trades no histórico, **0 com lineage ML** — esperado pelo mesmo motivo do item 7.

## 9. Critério para prosseguir — checklist

| Critério | Status |
|---|---|
| live_enabled = 0 | ✅ PASS (0/109) |
| v44/v46 = REJECTED | ✅ PASS |
| ML_GATE_ENABLED=false em produção | ✅ PASS (não definida → default false) |
| Deploy no commit esperado | ✅ PASS (`4a4c999`, todos os 6 serviços RUNNING) |
| Banco em migration head ≥ 110 | ✅ PASS (`110_shadow_decision_unique`) |
| Sem migration pendente | ✅ PASS |

**Baseline aprovado para prosseguir à Fase 1.**

## 10. Restrição estrutural identificada (decisão necessária antes da Fase 1)

Não existe staging neste projeto Railway, e não existe nenhum mecanismo de "canary por símbolo" no código hoje. Ligar `ML_GATE_ENABLED=true` em produção, mesmo que brevemente, é uma mudança **global e imediata**: como v44/v46 estão `REJECTED`, todo o caminho `prediction_service.predict(model_lane=...)` passaria a retornar `NoEligibleModelError` → `model_approved=False` para **toda** decisão `ALLOW` em **todo** watchlist L3 simultaneamente, que então seria convertida em `BLOCK` pelo gate (`pipeline_scan.py` linha ~2969). Nenhum capital real está em risco (live trading = 0/109), mas a coleta de novos shadows L3 para todos os usuários ficaria pausada durante a janela do teste. Decisão sobre como proceder registrada na mensagem de acompanhamento desta sessão.
