# RELATÓRIO — AUDITORIA DE CONSISTÊNCIA: PROFILE INTELLIGENCE vs SHADOW PORTFOLIO

**Data:** 2026-06-28  
**Prompt base:** `PROMPT_AUDITORIA_PI_INTERNAL_VS_SHADOW_METRICS_2026-06-28.md`  
**Estágio inicial:** `PROFILE_INTELLIGENCE_INTERNAL_AND_SHADOW_METRICS_MISMATCH_SUSPECTED`  
**Estágio final:** `PROFILE_INTELLIGENCE_METRICS_AUDITED_WITH_SINGLE_DATA_CONTRACT`  
**Tipo:** somente auditoria, sem correções

---

## 1. Resumo Executivo

As divergências entre Overview, Calibration Evolution, AI Critic e Shadow Portfolio **são esperadas e explicáveis** — cada tela usa uma tabela diferente, uma janela temporal diferente e um método de agregação diferente. Não há bug no cálculo, mas há falta de contrato explícito na UI.

Os três root causes principais são:

1. **Duas tabelas de sugestões distintas** — Overview usa `profile_suggestions` (legada), Calibration Evolution usa `profile_adjustment_suggestions` (nova). Valores completamente diferentes.
2. **Overview exibe snapshots congelados** — `total_profiles`, `total_closed_trades` e `base_win_rate` são campos armazenados no último registro de `profile_intelligence_runs`, computados na execução de 2026-06-27. Não são queries live.
3. **Win Rate 40.4% vs 28.6% são métricas diferentes** — Overview é trade-level (TP_HIT/total) em 7 dias; Calibration é média simples de indicador-bucket em 48h, que inclui buckets com avg_pnl_pct = -1.0 (100% loss), puxando a média para baixo.

**Zero mutações, zero live trading durante toda a auditoria.**

---

## 2. Fase 0 — Safety Precheck

| Check | Valor | Status |
|---|---|---|
| live_enabled | 0 | ✓ PASS |
| autopilot_enabled | 1 | Observação (sem impacto na auditoria read-only) |
| possible_live_orders | 0 | ✓ PASS |

```
git rev-parse HEAD: 8c4371d
git status: clean
```

---

## 3. Fase A — Endpoints e Componentes

### A.1 Overview

| Card | Valor UI | Endpoint | Arquivo | Linha | Tabela/Query | Tipo |
|---|---|---|---|---|---|---|
| Profiles analisados | 51 | GET /api/profile-intelligence/overview | profile_intelligence.py | 194 | `profile_intelligence_runs.total_profiles` | **SNAPSHOT** (congelado no último run) |
| Trades fechados | 9073 | idem | idem | 195 | `profile_intelligence_runs.total_closed_trades` | **SNAPSHOT** |
| Win Rate Base | 40.4% | idem | idem | 199 | `profile_intelligence_runs.base_win_rate` | **SNAPSHOT** |
| Melhor Profile | adx_gte_35… / 52.6% WR | idem | idem | 155-171 | `shadow_trades` GROUP BY profile_name, 60d, outcome IN ('TP_HIT','SL_HIT','TIMEOUT'), min 30 closed | **LIVE query** |
| Melhor Combinação | Score 64 | idem | idem | 147-153 | `profile_rule_combinations` ORDER BY champion_score DESC LIMIT 1 | LIVE |
| Combinações | 4574 | idem | idem | 136 | `profile_rule_combinations` COUNT(*) | LIVE |
| Sugestões pendentes | 0 | idem | idem | 123-126 | `profile_suggestions` WHERE status='pending_user_approval' | LIVE — tabela **LEGADA** |
| Alta confiança | 73 | idem | idem | 129-133 | `profile_suggestions` WHERE confidence_level='HIGH' AND status NOT IN ('rejected','archived') | LIVE — tabela **LEGADA** |
| Total de Runs | 35 | idem | idem | 118-120 | `profile_intelligence_runs` COUNT(*) | LIVE |
| Status | COMPLETED | idem | idem | 193 | `profile_intelligence_runs.status` do último run | **SNAPSHOT** |

**Nota crítica:** Os campos `total_profiles`, `total_closed_trades`, `base_win_rate` são lidos diretamente de `last_run` (objeto ORM), não de queries ao vivo em `shadow_trades`. Eles foram gravados no run de 2026-06-27 14:45 UTC com `lookback_days=7`.

### A.2 Calibration Evolution

| Card | Valor UI | Endpoint | Arquivo | Linha | Tabela/Query | Tipo |
|---|---|---|---|---|---|---|
| Sugestões pendentes | 1163 | GET /api/profile-intelligence/calibration-evolution/summary | calibration_evolution.py | 44-55 | `profile_adjustment_suggestions` COUNT(*) — all-time, **sem filtro de status** | LIVE — label UI ERRADO |
| Alta confiança | 911 | idem | idem | 50 | `profile_adjustment_suggestions` WHERE confidence >= 0.8 | LIVE |
| Mutations aplicadas | 0 | idem | idem | 49 | `profile_adjustment_suggestions` WHERE mutation_applied=true | LIVE |
| Versões registradas | 1163 | idem | idem | 58-65 | `profile_adjustment_versions` COUNT(*) — all-time | LIVE |
| Indicadores analisados | 4 | idem | idem | 68-76 | `profile_indicator_performance` COUNT(DISTINCT indicator_name) WHERE **created_at >= now() - 48h** | LIVE, janela 48h |
| Win Rate média | 28.6% | idem | idem | 73 | `profile_indicator_performance` AVG(win_rate) WHERE 48h — **média simples por row** | LIVE, janela 48h |
| P&L médio | -35.21% | idem | idem | 74 | `profile_indicator_performance` AVG(avg_pnl_pct) WHERE 48h | LIVE, janela 48h |
| AI Critic | COMPLETED | idem | idem | 79-88 | `profile_ai_reviews` WHERE status='COMPLETED' AND tokens_input > 0 ORDER BY completed_at DESC LIMIT 1 | LIVE |

**Nota crítica:** "Sugestões pendentes" na UI mapeia `suggestions.total` (não `suggestions.pending`). A API retorna `total` e a UI exibe sob o label "pendentes" — mislabeling.

### A.3 AI Critic

| Item | Valor | Endpoint | Arquivo | Linha | Tabela/Query |
|---|---|---|---|---|---|
| Modelo | claude-haiku-4-5-20251001 | GET /api/profile-intelligence/live/ai-review | profile_intelligence_live.py | 272-331 | `profile_ai_reviews` ORDER BY requested_at DESC LIMIT 1 |
| Tokens | 480 in / 1000 out | idem | idem | idem | idem |
| Fonte | shadow_trades | run_ai_review_cycle | profile_intelligence_live_service.py | ~590 | `shadow_trades` |
| Sources | L3 + L3_LAB | idem | idem | `_AI_SOURCES` | hardcoded: `["L3","L3_LAB"]` |
| Período | 4h | idem | idem | `_AI_WINDOW_H = 4` | `created_at >= now() - 4h` |
| Campo temporal | created_at | idem | idem | idem | idem |
| Trades analisados | 82 | idem | idem | idem | analysis_context.sample.trades_count |
| Profiles analisados | 25 | idem | idem | idem | analysis_context.sample.profiles_count |
| Symbols | 14 | idem | idem | idem | analysis_context.sample.symbols_count |
| Filtros | status=COMPLETED, pnl_pct IS NOT NULL, profile_id IS NOT NULL | idem | idem | idem | idem |

### A.4 Shadow Portfolio

| Item | Endpoint | Arquivo | Linha | Tabela/Query |
|---|---|---|---|---|
| Live Shadow Summary | GET /api/profile-intelligence/live/shadow-summary | profile_intelligence_live.py | 88-177 | `shadow_trades` WHERE source IN ('L3','L3_LAB'), default 24h, created_at |
| Profile Report | GET /api/shadow-trades/profile-report | shadow_trades.py | ~911 | `shadow_trades` + `watchlist_performance_priority_base_view` |
| Win Rate | idem | idem | ~456 | `COUNT(outcome='TP_HIT') / COUNT(outcome IN ('TP_HIT','SL_HIT','TIMEOUT'))` |
| P&L Médio | idem | idem | idem | `AVG(pnl_pct) FILTER (WHERE outcome IN ('TP_HIT','SL_HIT','TIMEOUT'))` |
| EV Score | calculado no service | performance_rankings.py | — | composite: win_rate × avg_pnl × tp_rate |

---

## 4. Fase B — Schema das Tabelas Envolvidas

### B.1 Tabelas candidatas identificadas

```
autopilot_run_errors         pipeline_watchlist_assets     profile_ai_reviews
custom_watchlists            pipeline_watchlist_rejections  profile_ai_reviews_reclassification_audit
label_lab_runs               pipeline_watchlists            profile_indicator_performance
profile_adjustment_suggestions  profile_indicator_stats     profile_intelligence_activity_log
profile_adjustment_versions     profile_intelligence_audit_log  profile_intelligence_autopilot_*
profile_intelligence_heartbeats profile_intelligence_loss_families profile_intelligence_runs
profile_rule_combinations    profile_suggestions            shadow_trades
shadow_capture_skips         watchlist_performance_priority_base_view  watchlist_profiles
```

### B.2 Duas tabelas de sugestões — distintas por design

| Campo | `profile_suggestions` (legada) | `profile_adjustment_suggestions` (nova) |
|---|---|---|
| Contexto | PI Engine (combinações, candidates) | Calibration Evolution (autopilot live) |
| Confiança | `confidence_level` (string: HIGH/MEDIUM/LOW) | `confidence` (numeric 0–1) |
| Status | 'pending_user_approval', 'rejected', etc. | 'SHADOW_APPLIED', etc. |
| Total rows | 101 | 1191 |
| Pending | 0 (nenhuma pendente) | N/A — campo total exibido como "pendentes" |
| Alta confiança | 73 (confidence_level='HIGH') | 933 (confidence >= 0.8) |

---

## 5. Fase C — Reprodução dos Números do Shadow Portfolio

### C.1 Por source (all-time)

| source | total | completed | wins | win_rate | avg_pnl_pct | pnl_total_usdt | profiles | first | last |
|---|---|---|---|---|---|---|---|---|---|
| L1_SPECTRUM | 2338 | 2313 | 1116 | 0.4825 | +0.010006 | +231.43 | 0 | 2026-06-10 | 2026-06-28 |
| L3 | 10523 | 9492 | 3648 | 0.3843 | -0.131189 | -12452.44 | 43 | 2026-06-09 | 2026-06-28 |
| L3_LAB | 4217 | 3481 | 1540 | 0.4424 | -0.172059 | -5989.38 | 10 | 2026-06-17 | 2026-06-28 |
| L3_REJECTED | 569 | 569 | 246 | 0.4323 | +0.048451 | +275.69 | 0 | 2026-06-14 | 2026-06-26 |
| L3_SIMULATED | 1419 | 1392 | 549 | 0.3944 | -0.036030 | -501.54 | 0 | 2026-06-14 | 2026-06-28 |

### C.2 Combinações para conferir as hipóteses do Overview

| Hipótese | Profiles | Trades fechados | Win Rate |
|---|---|---|---|
| H1: L3+L3_LAB all-time | 45 | 12973 | 0.3999 = 40.0% |
| H2: L3+L3_LAB+L3_REJECTED+L3_SIMULATED | 45 | 14934 | 0.4006 = 40.1% |
| H3: todas fontes all-time | 45 | 17247 | 0.4116 = 41.2% |
| **Run snapshot 2026-06-27** | **51** | **9073** | **0.4037 = 40.4%** |
| shadow 7d all sources (de now()) | 41 | 10278 | 0.3875 = 38.8% |
| shadow 7d L3+L3_LAB (de now()) | 41 | 8904 | 0.3923 = 39.2% |

**Conclusão:** O snapshot do último run (`total_profiles=51`, `total_closed_trades=9073`, `base_win_rate=40.4%`) **não é reproduzível por nenhuma query live atual** porque foi computado em 2026-06-27 14:45 UTC com `lookback_days=7`. O banco mudou desde então.

---

## 6. Fase D — Reprodução do Overview

### D.1 Runs

| Campo | Valor |
|---|---|
| total_runs | 35 |
| completed_runs (status='completed') | 0 (!) |
| completed_with_errors | 0 |
| first_run | 2026-06-17 |
| last_run | 2026-06-27 |

**Nota:** A contagem `completed_runs=0` via SQL é porque o SQL filtrou `status='COMPLETED'` (uppercase), mas o ORM grava `status='completed'` (lowercase). O último run tem `status='completed'` e o Overview exibe "COMPLETED".

### D.2 Reprodução da Win Rate Base

O Overview `base_win_rate = 40.4%` = `last_run.base_win_rate = 0.40372...` (snapshot do PI run em 2026-06-27).

O PI Engine computa `base_win_rate` durante o run usando shadow_trades com `outcome IN ('TP_HIT','SL_HIT','TIMEOUT')` dentro do `lookback_days` configurado. Esse valor **não é recalculado após o run**.

### D.3 Sugestões pendentes e alta confiança

| Tabela | Pendentes | Alta Confiança |
|---|---|---|
| `profile_suggestions` (Overview usa esta) | 0 | 73 (confidence_level='HIGH') |
| `profile_adjustment_suggestions` (Cal. Evolution usa esta) | N/A (todos SHADOW_APPLIED) | 933 (confidence ≥ 0.8) |

---

## 7. Fase E — Reprodução do Calibration Evolution

### E.1 Suggestions e Versions

| Tabela | total | high_confidence | mutations_applied | profiles | primeiro | último |
|---|---|---|---|---|---|---|
| `profile_adjustment_suggestions` | 1191 | 933 | 0 | 32 | 2026-06-27 | 2026-06-28 |
| `profile_adjustment_versions` | 1191 | — | 0 | — | 2026-06-27 | 2026-06-28 |

Status de todas as suggestions: `SHADOW_APPLIED` (100%). Nenhuma está "pending". O label "Sugestões pendentes" na UI é um mislabeling de `suggestions.total`.

### E.2 Indicadores analisados (48h)

| indicador | rows | avg_win_rate | avg_pnl_pct |
|---|---|---|---|
| adx | 3437 | 0.2991 | -0.319111 |
| rsi | 2970 | 0.2988 | -0.325520 |
| volume_delta | 3435 | 0.2740 | -0.381068 |
| taker_ratio | 2953 | 0.2712 | -0.382664 |
| **AVG global** | 12795 | **0.2858 = 28.6%** | **-0.3519 = -35.19%** |

Profiles com dados em PIP (48h): 40 profiles.

### E.3 Verificação da Win Rate 28.6% e P&L -35.21%

| Hipótese | Win Rate | P&L médio |
|---|---|---|
| AVG(win_rate) FROM profile_indicator_performance WHERE 48h | **0.2858 = 28.6%** ✓ | **-0.3519 = -35.19%** ✓ |
| Média simples por profile shadow L3+L3_LAB | 0.3817 = 38.2% | -0.149179 = -14.9% |
| Trade-weighted L3+L3_LAB | 0.3860 = 38.6% | -0.142155 = -14.2% |

**Conclusão:** Os valores 28.6% e -35.21% reproduzem com a query de `profile_indicator_performance` 48h. Não são provenientes de `shadow_trades`.

**Causa do -35.21% extremo:** A tabela `profile_indicator_performance` contém buckets onde todos os trades resultaram em SL (win_rate=0, avg_pnl_pct=-1.0). Exemplos:
- `taker_ratio, mid, avg_pnl=-1.0000, win_rate=0.0000, sample=11`
- `volume_delta, low, avg_pnl=-1.0000, win_rate=0.0000, sample=10`

Quando o AVG simples é calculado sobre todos os 12.795 rows (incluindo esses extremos), o resultado fica muito mais negativo do que a média de trade real em `shadow_trades` (-13.1% a -17.2%).

---

## 8. Fase F — Watchlists

### F.1 Watchlists L3 ativas (top 10 por assets)

| watchlist_name | profile_id | assets |
|---|---|---|
| L3_ANTI_EXAUSTAO_V3 | 2b70dc42 | 22 |
| L3_VOLATILIDADE_MODERADA_V3 | 5bdbefc4 | 15 |
| AP – macd_hist_lte_0_AND_ema50_gt_ema200_false | 5da37177 | 15 |
| AP – vol_spike_gte_1_5_AND_ema50_gt_ema200_false | 7b560f2a | 15 |
| L3_TREND_CONSERVADOR_V3 | a565150d | 13 |
| L3_BREAKOUT_V3 | 33ed9391 | 9 |
| (+ mais 4 com 8-12 assets) | | |

### F.2 Profiles no profile_indicator_performance (48h)

40 profiles distintos, incluindo L3_* V3 e AP – combinações (macd/rsi/vol_spike). Todos com 4 indicadores (adx, rsi, volume_delta, taker_ratio).

### F.3 Divergência de contagem de profiles por tela

| Tela | Profiles | Fonte | Período |
|---|---|---|---|
| Overview | 51 | `profile_intelligence_runs.total_profiles` (snapshot) | Snapshot de 2026-06-27, 7d lookback |
| Calibration Evolution PIP | 40 | `profile_indicator_performance` | 48h |
| Calibration Evolution Suggestions | 32 | `profile_adjustment_suggestions` | All-time |
| Shadow trades L3+L3_LAB | 45 | `shadow_trades` distincts | All-time |
| AI Critic | 25 | analysis_context.sample.profiles_count | 4h window |

---

## 9. Fase G — Período, Campo Temporal e Filtros

| Tela | Período | Campo temporal | Sources | Filtros | LIMIT/cache |
|---|---|---|---|---|---|
| Overview — métricas base | Snapshot do run (7d lookback) | `run_at` | Determinado pelo PI engine no run | `outcome IN ('TP_HIT','SL_HIT','TIMEOUT')` | Nenhum — snapshot congelado |
| Overview — melhor profile | 60 dias (de now()) | `created_at` | Todas (WHERE user_id) | `outcome IN (...)`, min 30 closed | LIMIT 1 |
| Calibration — suggestions | All-time | `created_at` | N/A | Nenhum | Nenhum |
| Calibration — indicators | **48 horas** | `created_at` | N/A | — | Nenhum |
| AI Critic | **4 horas** | `created_at` | L3, L3_LAB | status=COMPLETED, pnl_pct IS NOT NULL, profile_id IS NOT NULL | LIMIT (1000 tokens) |
| Shadow Summary Live | **24 horas** (padrão, max 168h) | `created_at` | L3, L3_LAB | source IN (L3, L3_LAB) | Nenhum |
| Shadow Profile Report | All-time ou filtro do usuário | `created_at` / `closed_at` | Variável | Depende do filtro | Paginação |

---

## 10. Fase H — AI Critic Context

### Reviews no banco (em ordem decrescente)

| id | status | tokens_in | tokens_out | legacy | trades | profiles | symbols | req_date |
|---|---|---|---|---|---|---|---|---|
| f7aa0eeb | COMPLETED | 480 | 1000 | — | 82 | 25 | 14 | 2026-06-28 |
| 1489f82c | COMPLETED | 480 | 1000 | — | 82 | 21 | 13 | 2026-06-28 |
| 9b8e6739 | COMPLETED | 255 | 868 | true | — | — | — | 2026-06-28 |
| 0a532fa0 | FAILED_AI_CALL | 0 | 0 | — | — | — | — | 2026-06-28 |
| c69b18c9 | COMPLETED | 283 | 1000 | true | — | — | — | 2026-06-27 |
| e4356fa1 | COMPLETED | 282 | 1000 | true | — | — | — | 2026-06-27 |
| 83a674e5 | COMPLETED | 283 | 866 | true | — | — | — | 2026-06-27 |
| 0021d049 | LEGACY_HOLLOW_REVIEW | 0 | 0 | — | — | — | — | 2026-06-27 |
| eec32b85 | LEGACY_HOLLOW_REVIEW | 0 | 0 | — | — | — | — | 2026-06-27 |
| 801966a9 | LEGACY_HOLLOW_REVIEW | 0 | 0 | — | — | — | — | 2026-06-27 |
| 026e02bc | COMPLETED | 0 | 0 | — | — | — | — | 2026-06-27 |

**Dois reviews com `analysis_context` completo** (f7aa0eeb e 1489f82c, ambos de 2026-06-28) — correção da sessão anterior funcionou. Os 4 legados (true) e 3 LEGACY_HOLLOW foram corretamente classificados.

### Comparação AI Critic vs outras telas

| | AI Critic | Overview | Calibration | Shadow |
|---|---|---|---|---|
| Usa mesmo dataset do Overview? | **Não** — sources L3+L3_LAB vs snapshot all-sources PI engine | — | — | — |
| Usa mesmo dataset da Calibration? | **Não** — shadow_trades 4h vs profile_indicator_performance 48h | — | — | — |
| Usa mesmo dataset do Shadow? | **Parcialmente** — L3+L3_LAB comum, mas janela diferente (4h vs 24h) | — | — | — |

---

## 11. Fase J — Matriz de Conciliação

| Métrica | Overview UI | Calibration UI | AI Critic | Shadow (24h) | SQL Evidence | Bate? | Causa |
|---|---|---|---|---|---|---|---|
| Profiles | 51 | 40 (PIP 48h) / 32 (suggestions) | 25 (4h) | 41 (7d, all src) | Runs snapshot vs live queries | **Não** | `PI_OVERVIEW_USES_RUN_SUMMARY` + `DIFFERENT_TIME_WINDOW` |
| Trades fechados | 9073 | N/A | 82 (4h) | 10278 (7d, all src) | Snapshot vs live | **Não** | `PI_OVERVIEW_USES_RUN_SUMMARY` |
| Win Rate | 40.4% | 28.6% | N/A | 38.8% (7d) / 22.3% (24h) | L140 vs PIP AVG | **Não** | `DIFFERENT_AGGREGATION_METHOD_SIMPLE_VS_WEIGHTED` + `DIFFERENT_TIME_WINDOW` + `CALIBRATION_USES_INDICATOR_AVERAGE_NOT_TRADE_AVERAGE` |
| P&L médio | N/A | -35.21% | N/A | -13.1% (L3, all-time) | PIP AVG vs shadow_trades AVG | **Não** | `CALIBRATION_USES_INDICATOR_AVERAGE_NOT_TRADE_AVERAGE` (inclui buckets com avg_pnl=-1.0) |
| Sugestões pendentes | 0 | 1163/1191 | N/A | N/A | profile_suggestions vs profile_adjustment_suggestions | **Não** | `PI_USES_DIFFERENT_SUGGESTION_TABLE` (legada vs nova) |
| Alta confiança | 73 | 911/933 | N/A | N/A | idem | **Não** | `PI_USES_DIFFERENT_SUGGESTION_TABLE` + `DIFFERENT_CONFIDENCE_DEFINITION` |
| Indicadores | N/A | 4 | N/A | N/A | PIP 48h: 4 distinct | ✓ | Consistente internamente |
| Combinações | 4574 | N/A | N/A | N/A | profile_rule_combinations | N/A | Única fonte |

---

## 12. Fase K — Checklist

| Pergunta | Resposta | Evidência |
|---|---|---|
| Overview usa as mesmas fontes do Shadow? | **Não** — Overview usa snapshot do run; Shadow usa shadow_trades live | SQL: run.total_closed_trades=9073 vs shadow_trades current queries |
| Calibration Evolution usa as mesmas fontes do Shadow? | **Não** — Calibration usa profile_indicator_performance; Shadow usa shadow_trades | calibration_evolution.py:68-76 vs shadow_trades |
| Overview e Calibration usam mesma fonte? | **Não** — Overview usa profile_intelligence_runs (snapshot) e profile_suggestions (legada); Calibration usa profile_adjustment_suggestions (nova) e profile_indicator_performance | profile_intelligence.py:123-133 vs calibration_evolution.py:44-55 |
| PI usa o mesmo período do Shadow? | **Não** — Overview é snapshot 7d; Calibration PIP é 48h; Shadow é 24h default | code audit |
| PI usa completed trades? | Overview: `outcome IN ('TP_HIT','SL_HIT','TIMEOUT')`; Calibration: `status='COMPLETED'` | profile_intelligence.py:159 vs calibration_evolution.py:201 |
| PI usa created_at ou closed_at? | Overview best_profile: `created_at`; PIP: `created_at`; AI Critic: `created_at` | código auditado |
| Shadow usa created_at ou closed_at? | Shadow summary live: `created_at`; outcome = TP_HIT/SL_HIT/TIMEOUT | shadow_trades 24h query |
| Win Rate é ponderado ou média simples? | Overview: weighted por trade (TP_HIT/total); Calibration: média simples por row de indicador-bucket | calibration_evolution.py:73 AVG(win_rate) sem ponderação |
| P&L é por trade/profile/indicador/suggestion? | Overview: por trade (no run); Calibration: AVG por indicador-bucket (inclui -1.0); Shadow: AVG por trade | C.1, E.3 |
| Existe LIMIT afetando média? | AI Critic: LIMIT indireto (4h window, ~82 trades); Overview best_profile: LIMIT 1; outros: sem LIMIT relevante | código |
| Existe cache stale? | **Sim** — Overview é snapshot congelado do último run (2026-06-27 14:45) | profile_intelligence_runs.run_at |
| Divergência é bug ou diferença de contrato? | **Diferença de contrato** — cada tela mede algo diferente intencionalmente, mas a UI não esclarece | análise código + SQL |

---

## 13. Contrato de Dados por Tela

| Tela | Endpoint | Fonte SQL | Sources | Período | Campo temporal | Filtros | Agregação |
|---|---|---|---|---|---|---|---|
| Overview | GET /api/profile-intelligence/overview | `profile_intelligence_runs` (snapshot) + `profile_suggestions` (live) + `profile_rule_combinations` (live) + `shadow_trades` (live, 60d, melhor profile) | Determinado pelo PI Engine no run | Snapshot: congelado em 2026-06-27; Melhor profile: 60d | `run_at` / `created_at` | `outcome IN ('TP_HIT','SL_HIT','TIMEOUT')`, min 30 closed | Snapshot: por run; Live: por trade / por profile |
| Calibration Evolution | GET /api/profile-intelligence/calibration-evolution/summary | `profile_adjustment_suggestions` + `profile_adjustment_versions` + `profile_indicator_performance` (48h) + `profile_ai_reviews` | N/A (sem filtro de source nas sugestões) | All-time (sugestões); 48h (indicadores); último COMPLETED (AI) | `created_at` | confidence >= 0.8, mutation_applied | Média simples por indicador-bucket |
| AI Critic | GET /api/profile-intelligence/live/ai-review | `profile_ai_reviews` + `shadow_trades` (no run cycle) | L3, L3_LAB | 4h | `created_at` | status=COMPLETED, pnl_pct IS NOT NULL, profile_id IS NOT NULL | Por trade |
| Shadow Summary Live | GET /api/profile-intelligence/live/shadow-summary | `shadow_trades` | L3, L3_LAB | 24h (default) | `created_at` | source IN (L3, L3_LAB) | Por trade; por profile |

---

## 14. Watchlists/Profiles considerados por tela

| Origem | Profiles únicos | Source | Trades | Win Rate (completed) | Nota |
|---|---|---|---|---|---|
| Overview (snapshot) | 51 | All (run) | 9073 (7d) | 40.4% | Congelado em 2026-06-27 |
| Calibration PIP (48h) | 40 | Todos com PIP rows | 12795 rows PIP | 28.6% (avg bucket) | Avg por indicador-bucket |
| Shadow L3 all-time | 43 | L3 | 9492 | 38.4% | Trade-level |
| Shadow L3_LAB all-time | 10 | L3_LAB | 3481 | 44.2% | Trade-level |
| Shadow L3+L3_LAB all-time | 45 | L3+L3_LAB | 12973 | 40.0% | Trade-level |
| AI Critic (4h) | 25 | L3+L3_LAB | 82 | N/A (resumo qualitativo) | 4h window |
| Shadow 24h | L3+L3_LAB | L3: 1068 trades / L3_LAB: 373 | win_rate L3=22.3%, L3_LAB=37.3% | Dia atual com queda no L3 |

---

## 15. Root Causes

### RC-1: Duas tabelas de sugestões diferentes (`PI_USES_DIFFERENT_SUGGESTION_TABLE`)

O Overview usa `profile_suggestions` (tabela legada do PI Engine clássico) enquanto o Calibration Evolution usa `profile_adjustment_suggestions` (tabela do novo Calibration Live Engine). Elas registram coisas diferentes:
- `profile_suggestions`: sugestões de criação de novos profiles (combinações PI Engine)
- `profile_adjustment_suggestions`: sugestões de ajuste de parâmetros de profiles existentes (Calibration Live Engine)

**Impacto:** "Sugestões pendentes: 0 (Overview) vs 1163 (Calibration)"

### RC-2: Overview exibe snapshots congelados (`PI_OVERVIEW_USES_RUN_SUMMARY`)

`total_profiles`, `total_closed_trades`, `base_win_rate` vêm de `profile_intelligence_runs.total_profiles/total_closed_trades/base_win_rate` — campos gravados no momento do run. O último run foi em 2026-06-27 14:45 UTC. Desde então, novos shadow trades entraram, mas o Overview continua exibindo os números do run.

**Impacto:** "Trades: 9073 (Overview snapshot) vs 10278/17247 (shadow atual)"

### RC-3: Win Rate 40.4% vs 28.6% — métodos de agregação incompatíveis (`CALIBRATION_USES_INDICATOR_AVERAGE_NOT_TRADE_AVERAGE`)

- Overview: `TP_HIT / (TP_HIT + SL_HIT + TIMEOUT)` — trade-level, outcome field
- Calibration: `AVG(win_rate) FROM profile_indicator_performance` — média simples sobre todos os rows de indicador-bucket, incluindo buckets extremos onde avg_pnl_pct=-1.0 (0% win rate)

A tabela `profile_indicator_performance` inclui buckets onde indicadores específicos em situações específicas (taker_ratio mid, volume_delta low) geraram 0% de acertos. Esses extremos são incluídos no AVG simples, puxando a média para baixo muito além do que o AVG de trade retornaria.

**Impacto:** "Win Rate: 40.4% (Overview, trade-level, 7d) vs 28.6% (Calibration, avg bucket, 48h)"

### RC-4: P&L -35.21% é média de buckets, não de trades (`CALIBRATION_USES_INDICATOR_AVERAGE_NOT_TRADE_AVERAGE`)

`profile_indicator_performance.avg_pnl_pct` = -0.3519 (fração, exibida como -35.19% na UI). Esta é a média de todos os buckets de indicadores, onde buckets com win_rate=0 têm avg_pnl_pct=-1.0 (trade individual perdeu 100% do capital arriscado). Shadow_trades L3 all-time tem avg_pnl_pct=-0.131 (-13.1%). Os números medem coisas diferentes.

### RC-5: UI mislabeling — "Sugestões pendentes" = `suggestions.total` (`API_CONTRACT_MISSING_METRIC_CONTEXT`)

O endpoint `/calibration-evolution/summary` retorna `suggestions.total` mas a UI exibe esse campo sob o label "Sugestões pendentes". Não existe filtro por status no summary. Todos os 1191 registros têm status='SHADOW_APPLIED' — nenhuma está pendente.

### RC-6: "Alta confiança" usa definições de confiança incompatíveis (`DIFFERENT_CONFIDENCE_DEFINITION`)

- Overview: `confidence_level = 'HIGH'` (string enum da tabela legada)
- Calibration: `confidence >= 0.8` (numérico da nova tabela)
- Mesmo que fossem a mesma tabela, as métricas seriam diferentes

### RC-7: Janelas temporais incomparáveis (`DIFFERENT_TIME_WINDOW`)

- Overview best metrics: snapshot 7d congelado
- Calibration indicators: 48h
- AI Critic: 4h
- Shadow live: 24h
- Shadow report: all-time ou filtro do usuário

---

## 16. Recomendações (para fase posterior de correção)

1. **Unificar "Alta Confiança"** entre Overview e Calibration — ou apontar ambas para a mesma tabela, ou explicitar na UI que medem coisas diferentes.

2. **Corrigir label "Sugestões pendentes" no Calibration Evolution** — alterar para "Sugestões registradas" ou "Sugestões criadas" (total), pois nenhuma está pendente; todas estão em SHADOW_APPLIED.

3. **Adicionar indicação de staleness no Overview** — exibir "Atualizado em: 2026-06-27 14:45 UTC" nos cards de snapshot (total_profiles, trades fechados, win rate base).

4. **Documentar o contrato de dados** de cada card na UI — tooltip ou header indicando janela, sources, filtros.

5. **Verificar se Profile Intelligence UI deve mostrar dados de `profile_adjustment_suggestions`** além de `profile_suggestions` — atualmente são dois sistemas paralelos sem conexão visual.

6. **Revisar avg_pnl_pct no Calibration** — o valor -35.21% pode induzir o usuário a achar que o portfólio perdeu 35% quando a perda real é ~13-17% por trade. Considerar exibir o valor com nota explicativa ("média por bucket de indicador").

---

## 17. Ledger de Evidências

| Afirmação | Origem | Valor literal |
|---|---|---|
| Overview usa `profile_suggestions` (legada) | profile_intelligence.py:123-126 | `FROM profile_suggestions WHERE user_id=:uid AND status='pending_user_approval'` |
| Calibration usa `profile_adjustment_suggestions` (nova) | calibration_evolution.py:44-55 | `FROM profile_adjustment_suggestions` |
| profile_suggestions: 0 pending, 73 high | SQL (psycopg2) | `pending_like=0, high_confidence=73` |
| profile_adjustment_suggestions: 1191 total, 933 high, 0 pending | SQL (psycopg2) | `total=1191, high_confidence=933, mutations_applied=0` |
| Todos os profile_adjustment_suggestions: status=SHADOW_APPLIED | SQL GROUP BY status | `('SHADOW_APPLIED', 1191)` |
| Overview total_profiles=51, trades=9073, wr=40.4% são snapshot | profile_intelligence.py:193-199 | `last_run.total_profiles`, `last_run.total_closed_trades`, `last_run.base_win_rate` |
| Último run: 2026-06-27 14:45 UTC, lookback_days=7 | SQL profile_intelligence_runs | `run_at=2026-06-27 14:45:52`, `lookback_days=7` |
| Win Rate 28.6% = AVG(win_rate) de PIP 48h | SQL psycopg2 | `avg_win_rate=0.2858` |
| P&L -35.21% = AVG(avg_pnl_pct) de PIP 48h | SQL psycopg2 | `avg_pnl_pct=-0.351900` |
| PIP inclui buckets avg_pnl_pct=-1.0000 | SQL ORDER BY avg_pnl ASC | `taker_ratio mid: -1.0000, win_rate=0.0000, n=11` |
| AI Critic usa L3+L3_LAB, 4h, created_at | analysis_context.dataset (DB) + código | `sources=["L3","L3_LAB"], window_hours=4` |
| AI Critic latest: 82 trades, 25 profiles, 14 symbols | SQL profile_ai_reviews (f7aa0eeb) | `analysis_context.sample.*` |
| 4574 combinações | SQL profile_rule_combinations | `COUNT(*)=4574` |
| 35 runs total | SQL profile_intelligence_runs | `COUNT(*)=35` |
| PIP usa `created_at >= now() - 48h` | calibration_evolution.py:75 | `WHERE created_at >= now() - interval '48 hours'` |
| shadow_trades 7d all-sources: 41 profiles, 10278 closed | SQL (psycopg2) | `profiles=41, closed_outcome=10278, win_rate=0.3875` |
| outcome ≡ status para COMPLETED (mesma contagem) | SQL | `closed_outcome=10278, closed_status=10278` |
| 2 reviews com analysis_context completo | SQL profile_ai_reviews | f7aa0eeb, 1489f82c (is_legacy=None) |

---

## 18. Veredito

```
PI_SCREENS_DIVERGE_BY_DESIGN_DIFFERENT_DATA_CONTRACTS
```

As telas divergem porque usam intencionalmente datasources, tabelas, janelas temporais e métodos de agregação diferentes. Não há bug de cálculo, mas há:
1. Ausência de contrato explícito na UI (o usuário não sabe o que cada card mede)
2. Mislabeling da UI no Calibration Evolution ("pendentes" para "total")
3. Overview exibindo snapshots congelados sem indicação de quando foram computados
4. Dois sistemas de sugestões paralelos sem ligação visual entre eles

A divergência mais enganosa é o P&L -35.21% no Calibration vs -13.1% no Shadow Portfolio — essa diferença pode induzir decisões erradas se o usuário interpretar -35.21% como a performance real do portfólio.
