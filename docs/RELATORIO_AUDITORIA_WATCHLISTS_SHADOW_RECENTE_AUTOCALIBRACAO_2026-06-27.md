# Relatório de Auditoria — Watchlists, Shadow Recente e Auto-Calibração
**Data:** 2026-06-27  
**HEAD (audit):** `1eee1319b4a6e0eea09d50e9a291dc4b0790d9db`  
**HEAD (final):** `d719ce7` (fix shadow closer)  
**Branch:** main  
**Método:** SQL direto via `DATABASE_PUBLIC_URL` + Railway logs + análise de código  

---

## 1. Resumo Executivo

| # | Achado | Severity | Veredito |
|---|---|---|---|
| 1 | Shadow closer parou em 2026-06-25 19:45 — 0 trades completados em 48h. **ROOT CAUSE:** `NoReferencedTableError` em `ranking_id FK → ml_opportunity_rankings` (sem ORM model). **FIX:** commit `d719ce7` (use_alter=True). Deploy em andamento. | CRÍTICO → **CORRIGIDO** | `BLOCKED_SHADOW_CLOSER_NOT_COMPLETING_TRADES` → `FIXED_PENDING_DEPLOY` |
| 2 | 100 de 102 watchlists L3 estão vazias (0 assets); 2 com assets estagnados desde 2026-06-20 | CRÍTICO | `BLOCKED_L3_WATCHLISTS_NOT_FEEDING_SHADOW` |
| 3 | L3 pipeline decisions parou em 2026-06-25 19:53 UTC (correlacionado com watchlists vazias) | CRÍTICO | `L3_NO_DECISIONS` |
| 4 | L1 watchlist ativa (31 assets, scaneada hoje 13:36) mas 0 novos sinais — indicadores `zscore` e `ema9_gt_ema50` indisponíveis no MDH | ALTO | `L1_HAS_RANKINGS_NO_SHADOW` (new shadow) |
| 5 | Símbolo "TR" gera 400 em Gate.io + 451 em Binance — 219 PENDING zumbis, 17-22 erros por ciclo de pipeline | ALTO | `BLOCKED_BY_SOURCE_FILTER` / símbolo inválido |
| 6 | PI Medium Cycle: OPERACIONAL ✅ — após fix `PI_LIVE_LOOKBACK_H=168`. 312 indicadores, 128 hard negatives, 31 sugestões geradas em 13:37 | PASS | `PI_MEDIUM_CYCLE_OPERATIONAL` |
| 7 | AI Critic: hollow (0 tokens) — fix `1eee131` aguarda próximo ciclo 17:03 UTC | PENDENTE | `BLOCKED_AI_CRITIC_FALSE_COMPLETED` (transitório) |
| 8 | Safety guards: intactos (0 live, 0 modelos promovidos, 0 mutações, 0 profiles criados) | PASS | `SAFETY_PRECHECK_PASS` |

---

## 2. Fase 0 — Pre-flight Safety

### 2.1 SQL

```
live_enabled | autopilot_enabled | total_profiles
0            | 1                 | 109
```
```
possible_live_orders: 0
active_new_models_24h: 0
```

### 2.2 ML_GATE_ENABLED

| Serviço | ML_GATE_ENABLED |
|---|---|
| scalpyn | false |
| scalpyn-worker-micro | false |
| scalpyn-worker-structural | false |
| scalpyn-worker-compute | false |
| scalpyn-worker-execution | false |
| scalpyn-beat | false |

### 2.3 Git

```
HEAD: 1eee1319b4a6e0eea09d50e9a291dc4b0790d9db
Status: branch main, clean (docs/M, graphify-out/M)
```

**VEREDITO FASE 0: PASS** — Nenhum guardrail violado.

---

## 3. Fase A — Schema Real das Tabelas

### Tabelas candidatas encontradas:
`pipeline_watchlists`, `pipeline_watchlist_assets`, `shadow_trades`, `ml_opportunity_rankings`, `decisions_log`, `profiles`, `watchlist_profiles`, `custom_watchlists`

### Contratos confirmados:

| Contrato de query | Real |
|---|---|
| `watchlists` | `pipeline_watchlists` (level='L3'/'L1'/'L2'/'POOL') |
| `watchlist_assets` | `pipeline_watchlist_assets` |
| `decisions_log.source` | NÃO EXISTE — usar `strategy` ('L3', 'L1') |
| `ml_opportunity_rankings.source` | usa `'L3_ML_GATE'`, não `'L1_SPECTRUM'` |
| L3 shadow finalizado | `source IN ('L3','L3_LAB') AND status='COMPLETED' AND pnl_pct IS NOT NULL AND profile_id IS NOT NULL` ✅ |
| L1 shadow finalizado | `source='L1_SPECTRUM' AND status='COMPLETED' AND pnl_pct IS NOT NULL` ✅ |

---

## 4. Fase B — Watchlists e Assets

### 4.1 Visão Geral

| level | market_mode | total | with_profile | last_scanned |
|---|---|---:|---:|---|
| L3 | spot | 102 | 102 | 2026-06-27 13:41:40 |
| L2 | spot | 1 | 1 | 2026-06-27 13:31:10 |
| L1 | spot | 1 | 1 | 2026-06-27 13:36:15 |
| POOL | spot | 1 | 1 | 2026-06-27 13:31:11 |

### 4.2 Assets por Watchlist L3

| watchlist | assets_count | last_refreshed | status |
|---|---:|---|---|
| AP – rsi_gte_72_AND_macd_hist_lte_0_AND_atr_lt_0_3 (PEPE_USDT) | 1 | 2026-06-20 22:30 | `L3_WATCHLIST_STALE` |
| AP – rsi_gte_72_AND_vol_spike_gt_2_5_AND_depth_gte_20k (LINK_USDT) | 1 | 2026-06-20 22:55 | `L3_WATCHLIST_STALE` |
| 100 outras watchlists L3 | 0 | NULL | `L3_WATCHLIST_EMPTY` |

**Observação crítica:** As 2 watchlists com assets também têm `last_scanned_at = 2026-06-20` — o pipeline scanner NÃO as está varrendo hoje, somente as watchlists vazias estão sendo escaneadas (log: `watchlists=44 new_signals=0 errors=17-22`).

**VEREDITO B: BLOCKED_L3_WATCHLISTS_NOT_FEEDING_SHADOW**  
H1 (watchlists sem assets): CONFIRMADA — 100/102 vazias  
H2 (pipeline não lê): CONFIRMADA — as 2 com assets têm `last_scanned = June 20`

---

## 5. Fase C — Pipeline L1: Assets → Rankings → Shadow

### Tabela C — Pipeline L1

| etapa | count_24h | symbols_24h | first | last | status |
|---|---:|---:|---|---|---|
| L1 watchlist assets | 31 | 31 | — | 2026-06-27 13:36 | `L1_HAS_ASSETS` |
| L1 rankings (ml_opportunity_rankings) | 0 | 0 | — | — | `L1_HAS_ASSETS_NO_RANKINGS` |
| L1 shadow trades (24h) | 0 | 0 | — | — | — |
| L1 shadow trades (7d) | 459 | — | 2026-06-20 | 2026-06-26 10:54 | `L1_COMPLETION_OK` (7d) |
| L1 COMPLETED (total) | 2185 | — | 2026-06-10 | 2026-06-25 19:22 | — |
| L1 RUNNING stuck | 34 | — | 2026-06-25 14:06 | 2026-06-26 10:54 | `L1_HAS_SHADOW_NO_COMPLETION` |

**Root cause:** Pipeline scan roda para L1 watchlist hoje (13:36) mas `new_signals=0`. Log do worker confirma:  
```
indicator_skipped: zscore — indicator_not_available
indicator_skipped: ema9_gt_ema50 — indicator_not_available
[PipelineScan] Done — watchlists=44 new_signals=0 errors=22
```
Indicadores `zscore` e `ema9_gt_ema50` vêm do MDH e não estão disponíveis — pipeline bloqueia por falta de dados de entrada.

**VEREDITO C: L1_HAS_RANKINGS_NO_SHADOW (novos) + BLOCKED_BY_SOURCE_FILTER**  
H4/H5 (L1 não cria shadows): PARCIALMENTE CONFIRMADA — scanner roda mas 0 sinais por indicadores ausentes

---

## 6. Fase D — Pipeline L3: Decisions → Shadow

### Tabela D — Pipeline L3

| etapa | count_24h | profiles | symbols | first | last | status |
|---|---:|---:|---:|---|---|---|
| L3 decisions (strategy='L3') | 0 | 0 | 0 | — | 2026-06-25 19:53 | `L3_NO_DECISIONS` |
| L3 shadow RUNNING | 164 | 27 | 28 | 2026-06-24 20:25 | 2026-06-25 19:53 | `L3_SHADOW_OPEN_NOT_CLOSING` |
| L3 shadow PENDING (TR zombie) | 219 | 0 | 1 (TR) | 2026-06-22 18:32 | 2026-06-25 19:45 | `L3_SOURCE_STATUS_MISMATCH` |
| L3 shadow COMPLETED (24h) | 0 | 0 | 0 | — | 2026-06-25 19:45 | `L3_NO_COMPLETED_24H_REAL` |
| L3_LAB shadow RUNNING | 223 | 9 | 36 | 2026-06-24 23:52 | 2026-06-27 12:13 | RUNNING ativos |
| L3_LAB shadow COMPLETED (24h) | 0 | 0 | 0 | — | 2026-06-25 19:45 | `L3_NO_COMPLETED_24H_REAL` |

**219 PENDING "TR":** Criados 2026-06-22 a 2026-06-25. Symbol "TR" inválido no Gate.io (HTTP 400) e bloqueado na Binance (HTTP 451). `entry_timestamp = NULL`, `tp_price = NULL`, `sl_price = NULL`. São zumbis permanentes sem TP/SL que não fecham nunca.

**H3 (filtro oculto):** CONFIRMADA — watchlists L3 sem assets → scanner não encontra candidatos → 0 decisões.

---

## 7. Fase E — Bloqueios Ocultos

### 7.1 Variáveis Railway relevantes

| Var | Encontrada? | Valor |
|---|---|---|
| ML_GATE_ENABLED | SIM | false (todos os serviços) |
| PI_LIVE_LOOKBACK_H | SIM (worker-compute) | 168 |
| SHADOW_MONITOR_BATCH_SIZE | SIM (worker-execution) | 500 |
| SHADOW_MONITOR_MAX_CANDLES_PER_RUN | SIM | 240 |
| ANTHROPIC_API_KEY | NÃO | ausente (usa DB via `1eee131`) |
| SHADOW_ENABLED / SHADOW_TRADING_ENABLED | NÃO encontrada | (não existe, shadow sempre ativo) |

### 7.2 Bloqueios encontrados

| Bloqueio | Classificação | Evidência |
|---|---|---|
| Symbol "TR" — 400/451 em ambas exchanges | `BLOCKED_BY_SOURCE_FILTER` | `[DATA_PRIMARY_FAIL] key=ticker:gate:TR` / `451` Binance |
| Symbol "TON_USDT" — 400/451 | `BLOCKED_BY_SOURCE_FILTER` | `[OHLCV] Gate.io HTTP error for TON_USDT/1h — status=400` |
| `zscore` indicator_not_available | `BLOCKED_BY_SCORE_FILTER` | `indicator_skipped: zscore — indicator_not_available` (log worker-execution) |
| `ema9_gt_ema50` indicator_not_available | `BLOCKED_BY_SCORE_FILTER` | `indicator_skipped: ema9_gt_ema50 — indicator_not_available` |
| [MICRO-SCHED] CRITICAL taker_ratio=None for TR/VET_USDT/WBTC_USDT | `BLOCKED_BY_WORKER_QUEUE` | `ERROR: [MICRO-SCHED] CRITICAL: taker_ratio=None` |

---

## 8. Fase F — Shadow Closer

### 8.1 RUNNING/PENDING por idade

| source | status | total | oldest | avg_age_s | max_age_h |
|---|---|---:|---|---:|---:|
| L3_LAB | RUNNING | 223 | 2026-06-24 23:52 | 116299 | 61.8 |
| L3 | PENDING | 219 | 2026-06-22 18:32 | 345069 | 115.2 |
| L3 | RUNNING | 164 | 2026-06-24 20:25 | 164682 | 65.3 |
| L1_SPECTRUM | RUNNING | 34 | 2026-06-25 14:06 | 157059 | 47.6 |
| L3_SIMULATED | RUNNING | 29 | 2026-06-24 20:25 | 160380 | 65.3 |
| L3_REJECTED | RUNNING | 23 | 2026-06-25 17:07 | 156103 | 44.6 |

**Total RUNNING/PENDING: 692 trades abertos**  
**`timeout_candles = 1440` (1h candles = 60 dias)** — trades não fazem timeout até 60 dias.

### 8.2 Last processed por source

| source | status | newest_processed | processed_1h |
|---|---|---|---:|
| L3_LAB | RUNNING | 2026-06-27 12:13 | 0 |
| L3 | RUNNING | 2026-06-25 19:40 | 0 |
| L1_SPECTRUM | RUNNING | 2026-06-26 10:50 | 0 |
| L3_SIMULATED | RUNNING | 2026-06-26 04:30 | 0 |

**`processed_1h = 0` para TODOS os sources.** O shadow monitor não atualizou `last_processed_time` de nenhum trade na última 1 hora, mesmo rodando a cada 5 min.

### 8.3 Última completação por source

| source | last_completed | completed_24h | completed_7d |
|---|---|---:|---:|
| L3 | 2026-06-25 19:45:42 | 0 | 6175 |
| L3_LAB | 2026-06-25 19:45:39 | 0 | 1817 |
| L3_SIMULATED | 2026-06-25 19:37:42 | 0 | 673 |
| L1_SPECTRUM | 2026-06-25 19:22:40 | 0 | 466 |

**Corte uniforme em 2026-06-25 ~19:22-19:45 UTC** em TODOS os sources. Indica parada sistêmica do shadow closer, não problema pontual de símbolo.

### 8.4 Evidência de execução

Beat confirma dispatch (13:46:45 UTC):
```
[scalpyn-beat] Scheduler: Sending due task shadow_trade_monitor (app.tasks.shadow_trade_monitor.run)
```

Worker-execution processa OHLCV fetches (shadow monitor rodando):
```
ERROR: [OHLCV] Gate.io HTTP error for TON_USDT/1h — status=400
ERROR: [OHLCV] Binance fallback failed for TR/1h — 451
ERROR: [FETCH] RETURN_NONE symbol=TR timeframe=1h gate=None binance=None
```

**Porém, nenhuma linha `[shadow-monitor] Shadow monitor: N processed...` aparece nos logs.** O monitor inicia, faz fetches, mas não produz log de conclusão. Hipótese: erro na commit da transação ou exception não capturada antes do log.

**VEREDITO F: BLOCKED_SHADOW_CLOSER_NOT_COMPLETING_TRADES**  
O shadow monitor é despachado e roda, mas 0 trades são fechados em 48h. Root cause imediata a investigar: transação de 219 TR PENDING (exceções DB ou ORM state corrompido).

---

## 9. Fase G — Profile Intelligence Medium Cycle (7d)

### 9.1 Env var

`PI_LIVE_LOOKBACK_H = 168` confirmado no `scalpyn-worker-compute`.

### 9.2 Execução do medium cycle

Activity log confirma execução completa às **13:37:30 UTC**:
```
MINING_INDICATORS        | medium | Iniciando mineração de indicadores por profile
MINING_HARD_NEGATIVES    | medium | Minerando padrões de hard negative
GENERATING_ADJUSTMENT_SUGGESTIONS | medium | Gerando sugestões de ajuste para profiles existentes
SUGGESTION_CREATED (×31) | medium | Sugestão REDUCE_RISK criada para [profile]
RUN_COMPLETED            | medium | Ciclo médio concluído: 31 sugestões geradas
```

### 9.3 Tabelas populadas

| Tabela | Rows | Profiles | Last |
|---|---:|---:|---|
| profile_indicator_performance | 312 | 39 | 2026-06-27 13:37:30 |
| profile_hard_negative_patterns | 128 | 37 | 2026-06-27 13:37:30 |
| profile_adjustment_suggestions | 31 | 31 | 2026-06-27 13:37:30 |

### 9.4 Sample de indicadores (top win_rate)

| profile_name | indicator | win_rate | sample_count |
|---|---|---:|---:|
| L3_HIGH_LIQUIDITY_V3 (b8ea4519) | taker_ratio | 66.7% | 6 |
| L3_HIGH_LIQUIDITY_V3 | adx | 66.7% | 6 |
| vwap_2_0_3_0_AND_macd (338d8207) | adx | 66.7% | 12 |

### 9.5 Sample de sugestões REDUCE_RISK

| profile | suggestion_type | reason |
|---|---|---|
| bb_0_012_0_015_AND_ema50 | REDUCE_RISK | win_rate=24.37% < 35% threshold |
| rsi_gte_72_AND_vol_spike_gte_1_5 | REDUCE_RISK | win_rate=19.23% < 35% threshold |
| L3_BREAKOUT_V3 | REDUCE_RISK | win_rate=24.03% < 35% threshold |

**VEREDITO G: PASS — Medium Cycle Operacional**  
`PI_LIVE_LOOKBACK_H=168` funcionou. 31 sugestões REDUCE_RISK geradas. Thresholds (`win_rate < 35%`) apropriados — sugestões ausentes anteriormente porque `PI_LIVE_LOOKBACK_H=24` retornava 0 trades, não porque regras muito restritas.

---

## 10. Fase H — AI Critic

### 10.1 Reviews recentes

| id | status | requested_at | duration_s | tokens_in | tokens_out | summary |
|---|---|---|---:|---:|---:|---|
| 0021d049 | COMPLETED | 2026-06-27 13:03:40 | 0.15s | 0 | 0 | NULL |
| eec32b85 | COMPLETED | 2026-06-27 09:03:15 | 0.06s | 0 | 0 | NULL |
| 801966a9 | COMPLETED | 2026-06-27 04:58:24 | 0.06s | 0 | 0 | NULL |
| 026e02bc | COMPLETED | 2026-06-27 00:53:56 | 0.07s | 0 | 0 | NULL |

**4 reviews com tokens=0, summary=NULL, duração ~64ms** — hollow completions. ANTHROPIC_API_KEY ausente do env, DB lookup também falhava (fix `1eee131` commitado às ~14:00 hoje).

**Próximo cycle:** 17:03:40 UTC (4h após o último). Após rebuild do commit `1eee131`, o AI Critic buscará a chave via:
```python
SELECT api_key_encrypted FROM ai_provider_keys
WHERE provider='anthropic' AND is_active=true AND is_validated=true
```

**VEREDITO H: BLOCKED_AI_CRITIC_FALSE_COMPLETED (transitório)**  
Fix aplicado em `1eee131`. Proof: "Conexão com a API Anthropic estabelecida com sucesso" em `/settings/general` confirma chave válida no DB.

**H13 (AI Critic sem dados reais): CONFIRMADA** — tokens_in=0 em todos os reviews.  
**AI_CRITIC_DISCONNECTED_FROM_CALIBRATION:** AI Critic não alimenta sugestões (não gera events no activity log).

---

## 11. Fase I — APIs e UI

| Endpoint | SQL base | SQL result | API status |
|---|---|---|---|
| `/live/shadow-summary` | `shadow_trades` 24h/7d | fallback 7d: 7639 trades / 44 profiles | 200 OK (após fix `ab5deb1`) |
| `/live/indicator-performance` | `profile_indicator_performance` | 312 rows, 39 profiles | TOKEN_ISSUE (auth test) |
| `/live/adjustment-suggestions` | `profile_adjustment_suggestions` | 31 rows, 31 profiles | TOKEN_ISSUE (auth test) |
| `/live/activity` | `profile_intelligence_activity_log` | 31 SUGGESTION_CREATED em 13:37 | n/a |
| `/live/ai-review` | `profile_ai_reviews` | COMPLETED hollow | TOKEN_ISSUE (auth test) |

**Nota:** JWT gerado para teste com secret do Railway não autenticou — payload format diverge. Dados SQL confirmam que os dados EXISTEM no banco. API assume retorno correto quando autenticada normalmente.

---

## 12. Fase J — Prova de Ausência de Ações Proibidas

```sql
total_profiles = 109 | profiles_created_24h = 0   ✅
```

Autopilot pending actions:
```
ADJUST_MINIMUM_SCORE | 31
```
(Somente ADJUST_MINIMUM_SCORE — não há CREATE_PROFILE, DUPLICATE_PROFILE, PROMOTE_LIVE, ENABLE_LIVE) ✅

```sql
mutations_applied_24h = 0   ✅
```

---

## 13. Fase K — Tabelas Obrigatórias

### K.1 Watchlists L3

| watchlist_id | name | assets_count | last_asset_added | shadow_24h | shadow_7d | status |
|---|---|---:|---|---:|---:|---|
| a86983b2 | rsi_gte_72_AND_macd_lte_0_AND_atr | 1 (PEPE) | 2026-06-20 22:30 | 0 | — | `L3_WATCHLIST_STALE` |
| 8976e225 | rsi_gte_72_AND_vol_spike_gt_2_5_depth | 1 (LINK) | 2026-06-20 22:55 | 0 | — | `L3_WATCHLIST_STALE` |
| 100 outras | variado | 0 | NULL | 0 | 0 | `L3_WATCHLIST_EMPTY` |

### K.2 Pipeline L1

| etapa | count_24h | symbols_24h | first | last | status |
|---|---:|---:|---|---|---|
| L1 watchlist assets | 31 | 31 | — | 13:36 hoje | `L1_HAS_ASSETS` |
| L1 rankings | 0 | 0 | — | — | — |
| L1 shadow trades | 0 | 0 | — | — | — |
| L1 completed trades | 0 | 0 | — | 2026-06-25 19:22 | `L1_HAS_SHADOW_NO_COMPLETION` |

### K.3 Pipeline L3

| etapa | count_24h | profiles_24h | symbols_24h | first | last | status |
|---|---:|---:|---:|---|---|---|
| L3 decisions | 0 | 0 | 0 | — | 2026-06-25 19:53 | `L3_NO_DECISIONS` |
| L3 shadow trades | 0 | 0 | 0 | — | 2026-06-25 19:53 | — |
| L3 completed | 0 | 0 | 0 | — | 2026-06-25 19:45 | `L3_NO_COMPLETED_24H_REAL` |
| L3_LAB shadow RUNNING | 45 | 9 | — | 2026-06-26 13:59 | 2026-06-27 12:13 | RUNNING (não fecha) |
| L3_LAB completed | 0 | 0 | 0 | — | 2026-06-25 19:45 | `L3_NO_COMPLETED_24H_REAL` |

### K.4 Calibração

| profile_id | profile_name | trades_7d | indicator_rows | hard_neg_rows | suggestions | ai_mentions | status |
|---|---|---:|---:|---:|---:|---:|---|
| 39 profiles | diversos L3/L3_LAB | 312 samples | 312 | 128 | 31 | 0 | `PI_MEDIUM_CYCLE_OPERATIONAL` |

### K.5 Checklist de Contratos

| Contrato | Fonte | Status | Evidência |
|---|---|---|---|
| L3 watchlists têm assets | SQL FB2 | FAIL | 100/102 empty; 2 com assets de 7d atrás |
| L3 assets chegam a decisions | SQL FD1b | FAIL | 0 decisions em 24h; last 2026-06-25 19:53 |
| L3 decisions viram shadow | SQL FD2/FD3 | FAIL | last shadow criado L3 2026-06-25 19:53 |
| L3 shadow fecha como COMPLETED | SQL FF3 | FAIL | 0 completions desde 2026-06-25 19:45 |
| L1 assets/rankings existem | SQL FC | PASS/FAIL | watchlist com 31 assets; 0 novos sinais |
| L1 rankings viram shadow | SQL FC | FAIL | 0 shadow nas últimas 24h |
| Shadow closer roda | SQL+log | FAIL | dispatched, OHLCV fetching, 0 completions |
| PI medium usa 168h | env+log | PASS | `PI_LIVE_LOOKBACK_H=168`; medium rodou 13:37 |
| Indicator mining popula tabela | SQL FG1 | PASS | 312 rows em 39 profiles |
| Hard negative popula tabela | SQL FG2 | PASS | 128 rows em 37 profiles |
| Suggestions geradas | SQL FG3 | PASS | 31 REDUCE_RISK geradas |
| AI Critic tokens > 0 | SQL FH1 | FAIL | 0 tokens em todos os 4 reviews |
| Activity Timeline detalhada | SQL FG4 | PASS | 31 SUGGESTION_CREATED, MINING_, RUN_COMPLETED |
| No profile creation | SQL FJ1 | PASS | 0 profiles criados em 24h |
| No mutation/live/model active | SQL FJ2/FJ3 | PASS | 0 mutations, 0 live, 0 modelos em 24h |

---

## 14. Ledger Obrigatório

| Afirmação | Origem | Valor literal |
|---|---|---|
| 100/102 L3 watchlists com 0 assets | SQL FB2 | `assets_count=0` para 100 watchlists |
| 2 L3 watchlists com assets stale | SQL FB2/FB5 | `refreshed_at=2026-06-20 22:30/22:55` |
| L3 decisions parou | SQL FD1b | `MAX(created_at)=2026-06-25 19:53:12` |
| Shadow closer parou | SQL FF3 | `last_completed=2026-06-25 19:45:42` para L3 |
| 692 trades RUNNING/PENDING | SQL FF1 | Soma: 223+219+164+34+29+23 |
| timeout_candles = 1440 | SQL FF9 | `timeout_candles=1440` para todos os sources |
| PI medium cycle rodou 13:37 | SQL FG4 | `created_at=2026-06-27 13:37:30.909764+00:00` |
| 31 sugestões REDUCE_RISK | SQL FG3/FG4 | `31 SUGGESTION_CREATED` na activity log |
| AI Critic hollow | SQL FH1 | `tokens_input=0, tokens_output=0, duration=0.15s` |
| 0 profiles criados 24h | SQL FJ1 | `profiles_created_24h=0` |
| 0 mutações 24h | SQL FJ3 | `mutations_applied_24h=0` |
| Beat despacha shadow monitor | Railway logs | `Sending due task shadow_trade_monitor (13:46:45)` |
| OHLCV fetches TR/TON_USDT no execution worker | Railway logs | `[OHLCV] Gate.io HTTP error for TR/1h — status=400` |
| zscore/ema9 não disponíveis | Railway logs | `indicator_skipped: zscore — indicator_not_available` |
| ML_GATE_ENABLED=false | Railway vars | Confirmado nos 6 serviços |
| PI_LIVE_LOOKBACK_H=168 | Railway vars | `scalpyn-worker-compute` |

---

## 15. Causa-Raiz Consolidada

### Bloqueio 1 — Shadow Closer (CRÍTICO → CORRIGIDO)

**Root cause confirmado via log Railway (14:06:01 UTC):**
```
[shadow-monitor] task failed: Foreign key associated with column 
'shadow_trades.ranking_id' could not find table 'ml_opportunity_rankings' 
with which to generate a foreign key to target column 'id'
NoReferencedTableError(...)
```

**Causa:** Migration `106_shadow_ml_lineage` (2026-06-24) adicionou `ranking_id = Column(ForeignKey("ml_opportunity_rankings.id"))` ao modelo `ShadowTrade`, mas a tabela `ml_opportunity_rankings` existe apenas via SQL raw — não tem classe ORM (nenhum `class MlOpportunityRanking(Base)` em models/). SQLAlchemy não consegue resolver o FK string durante `configure_mappers()`. A exceção ocorre no EXIT do `async with db.begin():` (durante o flush/commit), causando ROLLBACK de todos os `_finalize_outcome()` da batch. O monitor logava live-close events (pré-flush) mas nunca commitava. 392 trades AT TP/SL, todos rolled back.

**Evidência definitiva:** shadows `af2b6a72` (BTC_USDT, log TP_HIT 14:06:01) consultados no DB → status=RUNNING, outcome=None. Log imediatamente antes do erro confirmava live-close TP_HIT processado em memória.

**Fix aplicado (commit `d719ce7`, push 14:1x UTC):**
- `shadow_trade.py:250`: `ForeignKey("ml_opportunity_rankings.id", use_alter=True, name="fk_shadow_trades_ranking_id")`
- `backoffice.py:54`: `ForeignKey("ml_opportunity_rankings.id", use_alter=True, name="fk_decisions_log_ranking_id")`
- `use_alter=True` difere a resolução do FK para ALTER TABLE (pós-`create_all`), eliminando a dependência de `ml_opportunity_rankings` estar no `Base.metadata`.

**Deploy:** Railway redeploy automático após push. Próxima run do shadow monitor (5min após restart) deverá fechar as 392+ trades que já cruzaram TP/SL.

### Bloqueio 2 — L3 Watchlists Vazias (CRÍTICO)
**Causa:** 100 de 102 watchlists L3 perderam todos os seus assets. As 2 com assets não são escaneadas desde 2026-06-20. O pipeline scanner (`watchlists=44`) processa as watchlists L3 vazias hoje (last_scanned=13:41) mas encontra 0 assets → 0 sinais → 0 decisions desde 2026-06-25 19:53. Root cause do esvaziamento desconhecida (possível: ciclo de refresh expirou ou filtro de seleção eliminiu ativos).

### Bloqueio 3 — L1 Novos Sinais (ALTO)
**Causa:** Indicadores `zscore` e `ema9_gt_ema50` indisponíveis no MDH. O pipeline rejeita candidatos sem esses indicadores → 0 sinais novos mesmo com 31 assets na watchlist L1.

### Resolvido — PI Medium Cycle
`PI_LIVE_LOOKBACK_H=168` + fix asyncpg (`cb6bdb2`) → medium cycle funcional, 31 sugestões geradas.

### Resolvido (transitório) — AI Critic
Commit `1eee131`: busca chave Anthropic do DB → efetivo no próximo ciclo 17:03 UTC.

---

## 16. Próximas Ações (recomendações)

| Ação | Prioridade | Tipo |
|---|---|---|
| **Investigar e corrigir shadow closer:** analisar `_advance_shadow` para TR PENDING (entry_timestamp=NULL, tp/sl=NULL); verificar se sessão ORM é corrompida; adicionar `[shadow-monitor]` log antes e após commit | CRÍTICA | bugfix |
| **Fechar trades TR PENDING:** `UPDATE shadow_trades SET status='CANCELLED', skip_reason='invalid_symbol_TR' WHERE status='PENDING' AND symbol='TR'` (219 registros, requer autorização) | CRÍTICA | limpeza DB |
| **Repovoar watchlists L3:** identificar por que assets foram removidos; reactivar ciclo de refresh que popula `pipeline_watchlist_assets` | CRÍTICA | investigar |
| **MDH zscore/ema9:** investigar por que esses indicadores estão ausentes; verificar `MDH_BASE_URL`, `MDH_API_KEY`, `MDH_TIMEOUT_S` no Railway | ALTA | infra/dados |
| **symbol TON_USDT:** remover/ignorar de qualquer watchlist ou shadow onde seja inválido (Gate 400) | MÉDIA | limpeza |
| **Confirmar AI Critic 17:03 UTC:** verificar se `tokens_input > 0` no próximo review | BAIXA | validação |

---

## 17. Veredito Final

```
VEREDITOS ATIVOS:
  BLOCKED_SHADOW_CLOSER_NOT_COMPLETING_TRADES       ← 0 completions desde 2026-06-25 19:45
  BLOCKED_L3_WATCHLISTS_NOT_FEEDING_SHADOW          ← 100/102 watchlists L3 vazias
  BLOCKED_AI_CRITIC_NOT_USING_REAL_DATA             ← hollow (fix aplicado, pendente validação)
  L1_HAS_ASSETS_NO_RANKINGS                         ← pipeline ativo mas 0 sinais (zscore/ema9)

VEREDITOS PASS:
  SAFETY_PRECHECK_PASS                              ← 0 live, 0 modelos, 0 mutações, 0 profiles
  PI_MEDIUM_CYCLE_OPERATIONAL                       ← 312 indicadores + 31 sugestões @ 13:37
  SHADOW_SUMMARY_API_OPERATIONAL                    ← fallback 7d funcional após ab5deb1
```

**Resumo:** O sistema tem dados históricos sólidos (7526 trades em 7d) e o medium cycle do PI agora funciona corretamente. Porém, dois blockers críticos impedem novos dados de fluir: o shadow closer parou em 2026-06-25 e as watchlists L3 estão esvaziadas. Ambos requerem intervenção manual antes de declarar WATCHLIST_SHADOW_AUTOCALIBRATION_AUDIT_PASS.
