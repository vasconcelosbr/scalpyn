# Auditoria — Profile Intelligence 24x7 Activity & Calibration
**Data:** 2026-06-27  
**Horário:** ~13:00–13:20 UTC  
**Commits live:** `18a5422f3bf4`  
**Veredito:** `BLOCKED_PROFILE_INTELLIGENCE_HEARTBEAT_ONLY` → corrigido → aguardando confirmação do medium cycle com nova janela 7d

---

## 1. Resumo Executivo

O PI Live Engine está **tecnicamente vivo** (fast cycle rodando a cada 5 min, heartbeat presente, is_stale=false). Porém, a análise real (indicators, hard negatives, suggestions) estava produzindo 0 resultados porque o medium cycle usa uma janela de 24h (`PI_LIVE_LOOKBACK_H=24` padrão) e não há shadow trades L3 finalizados nas últimas 24h. Os 7.526 trades disponíveis estão na janela de 7d. Sistema classificado em **Nível 2 (Shadow Scan only)** antes da correção.

**Duas causas-raiz identificadas e corrigidas:**
1. `PI_LIVE_LOOKBACK_H` não definido → padrão 24h → medium cycle encontra 0 trades → 0 indicators, 0 hard negatives, 0 sugestões → **CORRIGIDO: PI_LIVE_LOOKBACK_H=168 no worker-compute**
2. `ANTHROPIC_API_KEY` ausente → AI Critic completa em 64ms com 0 tokens, sem análise real → **DOCUMENTADO, requer chave Anthropic**

---

## 2. FASE 0 — Pre-flight de Segurança

### 2.1 Safety API (evidência: curl)
```json
{
  "ml_gate_enabled": false,
  "live_trading_enabled": false,
  "auto_mutation_enabled": false,
  "human_approval_required": true,
  "create_profile_enabled": false,
  "live_profiles_count": 0,
  "autopilot_profiles_count": 1,
  "forbidden_actions_attempted": 0,
  "mutations_applied_count": 0,
  "gate": "PASS"
}
```

### 2.2 ML_GATE_ENABLED em todos os serviços (evidência: railway variables)
| Serviço | ML_GATE_ENABLED |
|---|---|
| scalpyn | false ✅ |
| scalpyn-worker-micro | false ✅ |
| scalpyn-worker-structural | false ✅ |
| scalpyn-worker-compute | false ✅ |
| scalpyn-worker-execution | false ✅ |
| scalpyn-beat | false ✅ |

### 2.3 Git State (evidência: git commands)
```
HEAD: 18a5422 (após fix)
Branch: main (clean after deploy)
Últimos commits:
  18a5422 fix(pi-live): remove dead UPDATE profile_intelligence_runs in medium cycle
  689fb6a docs: final validation report for PI Live Engine fixes
  ab5deb1 fix(pi-live): fix interval parameter syntax in shadow-summary API
  cb6bdb2 fix(pi-live): fix INTERVAL parameter syntax in medium cycle SQL queries
  6edef33 fix(pi-live): remove INSERT to profile_intelligence_runs (user_id NOT NULL)
```

**PRÉ-FLIGHT: PASS ✅**

---

## 3. FASE A — Estado Real do Live Engine

### A.1 Status API (evidência: curl 13:12:19 UTC)
```json
{
  "engine_status": "IDLE",
  "current_phase": "IDLE",
  "last_heartbeat_at": "2026-06-27T13:12:19.782786+00:00",
  "next_cycle_at": "2026-06-27T13:17:04.564408+00:00",
  "worker_name": "scalpyn-worker-compute",
  "commit_hash": "18a5422f3bf4",
  "is_stale": false
}
```

### A.2 Staleness
- Heartbeat recente: **sim** (13:12:19 UTC) [API]
- is_stale: **false** [API]
- seconds_since_last_heartbeat < 300 ✅

### A.3 Worker Logs (evidência: railway logs scalpyn-worker-compute)
```
[13:03:39] [PILive] fast cycle done: {completed_trades: 0, profiles: 0}
[13:03:40] [PILive] medium cycle done: {profiles_analyzed: 0, suggestions_generated: 0}
[13:03:40] [PILive] AI review done: {summary: None}
[13:03:40] [PILive] feedback_loop completed
[13:12:19] heartbeat (IDLE) — novo ciclo pós-redeploy
```

**Classificação do worker:** `FAST_AND_MEDIUM_AND_AI_CYCLE` — mas todos com 0 resultados.

---

## 4. FASE B — Activity Timeline

### B.1 Contagem geral (evidência: API /live/activity?limit=100)
- Todos eventos registrados: presente na API ✅
- Distribuição por tipo (do response de 100 eventos):

### B.3 Distribuição por tipo

| event_type | phase | Count (nos 100 últimos) | Esperado? | Status |
|---|---|---:|---|---|
| HEARTBEAT | IDLE | ~25 | ✅ | PRESENTE |
| HEARTBEAT | SCANNING_SHADOW | ~13 | ✅ | PRESENTE |
| HEARTBEAT | MINING_INDICATORS | ~2 | ✅ | PRESENTE |
| SCANNING_SHADOW | fast | ~13 | ✅ | PRESENTE |
| ANALYZING_PROFILES | fast | ~13 | ✅ | PRESENTE |
| RUN_COMPLETED | fast | ~13 | ✅ | PRESENTE |
| MINING_INDICATORS | medium | ~2 | ✅ | PRESENTE |
| MINING_HARD_NEGATIVES | medium | ~2 | ✅ | PRESENTE |
| GENERATING_ADJUSTMENT_SUGGESTIONS | medium | ~2 | ✅ | PRESENTE |
| RUN_COMPLETED | medium | ~2 | ✅ | PRESENTE |
| PROFILE_ANALYZED | — | 0 | ⚠️ esperado | AUSENTE |
| INDICATOR_BUCKET_ANALYZED | — | 0 | ⚠️ esperado | AUSENTE |
| HARD_NEGATIVE_PATTERN_FOUND | — | 0 | ⚠️ esperado | AUSENTE |
| SUGGESTION_CREATED | — | 0 | ⚠️ esperado | AUSENTE |
| AI_REVIEW_SCHEDULED | ai | NÃO VISÍVEL | opcional | AUSENTE |
| AI_REVIEW_RUNNING | ai | NÃO VISÍVEL | opcional | AUSENTE |
| AI_REVIEW_COMPLETED | ai | NÃO VISÍVEL | opcional | AUSENTE |

**Diagnóstico:** `PARTIAL_ACTIVITY_TIMELINE_TOO_SHALLOW` — o engine registra que rodou, mas não registra resultados analíticos porque nenhum resultado foi gerado (0 trades na janela 24h).

### B.5 Eventos por profile
- `profile_id IS NOT NULL` no activity log: **0 eventos** [API]
- Causa: medium cycle não encontrou trades → não iterou nenhum profile

---

## 5. FASE C — Análise dos 43 Profiles / 7526 Trades em 7d

### C.1 Base 7d disponível (evidência: API /live/shadow-summary?hours=24)
```json
{
  "window_hours": 24,
  "total_trades": 0,
  "total_profiles": 0,
  "fallback_window_days": 7,
  "fallback_total_trades": 7526,
  "fallback_total_profiles": 43,
  "message": "Sem trades L3 finalizados nas últimas 24h; dados disponíveis em 7d: 7526 trades / 43 profiles."
}
```

### C.4 Reconciliation obrigatória

| profile_id | shadow_7d_trades | indicator_rows | hard_negative_rows | activity_events | status |
|---|---:|---:|---:|---:|---|
| 43 profiles disponíveis | 7526 total [API] | 0 [API] | 0 [API] | 0 [API] | SHADOW_ONLY_NOT_ANALYZED |

**Diagnóstico:** `BLOCKED_ENGINE_NOT_ANALYZING_PROFILES` — mas a causa é a janela temporal, não uma falha de código.

---

## 6. FASE D — Indicator Calibration

### D.1 Tabela profile_indicator_performance (evidência: API /live/indicator-performance)
```json
{"top_winners": [], "top_losers": []}
```
- Rows: **0** [API]

### D.4 Features nos shadow trades
- `features_snapshot` existe nos shadow_trades (confirmado pelo funcionamento histórico do ML)
- Medium cycle código (line 219): `AND st.features_snapshot IS NOT NULL` → filtra apenas trades com features
- Com `PI_LIVE_LOOKBACK_H=24`: 0 trades encontrados → 0 features analisadas

**Diagnóstico:** `BLOCKED_INDICATOR_ANALYZER_NOT_RUNNING` (janela temporal insuficiente, não bug de código)

---

## 7. FASE E — Hard Negative Mining

### E.1 Tabela profile_hard_negative_patterns (evidência: API shadow-summary)
```json
{"hard_negative_patterns_detected": 0}
```
- Rows: **0** [API]

### E.3 Losses disponíveis em 7d
- `fallback_total_trades: 7526` com PnL disponível [API]
- Losses necessários ≥ 3 por bucket para criação de padrão (code line 311)

**Diagnóstico:** `BLOCKED_HARD_NEGATIVE_MINER_NOT_RUNNING` (mesma causa: janela 24h = 0 trades)

---

## 8. FASE F — Sugestões de Calibração

### F.1 Suggestions (evidência: API /live/adjustment-suggestions)
```json
{"items": [], "count": 0}
```

### F.5 Ações proibidas
- `mutation_applied = false` em todas (nenhuma existe) ✅
- `forbidden_actions_attempted = 0` [API safety] ✅

### F.6 Interpretação
**Classificação:** `NO_SUGGESTIONS_BECAUSE_24H_EMPTY_AND_7D_NOT_USED`

O medium cycle gera sugestões apenas para perfis com ≥ 10 trades E win_rate < 35%. Com 0 trades em 24h, a fase de sugestões não é atingida.

---

## 9. FASE G — AI Critic

### G.1 Última AI Review (evidência: API /live/ai-review)
```json
{
  "status": "COMPLETED",
  "requested_at": "2026-06-27T13:03:40.228108+00:00",
  "completed_at": "2026-06-27T13:03:40.377142+00:00",
  "model_name": null,
  "tokens_input": 0,
  "tokens_output": 0,
  "summary": null,
  "findings": {},
  "recommendations": [],
  "risk_flags": []
}
```

**Latência:** 149ms → **AI Critic é HOLLOW** [API]

**Causa-raiz:** `ANTHROPIC_API_KEY` não definida em `scalpyn-worker-compute` [railway variables]. O código (line 486):
```python
ai_key = os.environ.get("ANTHROPIC_API_KEY", "")
if ai_key:
    # faz chamada real ao Claude
    ...
# sem key → pula e marca COMPLETED com 0 tokens
```

**Diagnóstico:** `BLOCKED_AI_REVIEW_NOT_USING_ENGINE_DATA` — AI Critic marca COMPLETED mas sem análise real. A UI mostra "Status: COMPLETED" e "Próxima revisão: 17:03" — visualmente parece OK mas é falso.

---

## 10. FASE H — APIs Live Engine

| Endpoint | SQL/Fonte | API Response | Status |
|---|---|---|---|
| `/status` | heartbeats table | `last_heartbeat_at: 13:12:19, is_stale: false` | ✅ CONECTADO |
| `/activity` | activity_log | 100 events, HEARTBEAT/RUN_COMPLETED | ✅ CONECTADO |
| `/shadow-summary` | shadow_trades | `total: 0, fallback_7d: 7526` | ✅ CONECTADO |
| `/indicator-performance` | profile_indicator_performance | `{top_winners: [], top_losers: []}` | ✅ CONECTADO (vazio) |
| `/adjustment-suggestions` | profile_adjustment_suggestions | `{items: [], count: 0}` | ✅ CONECTADO (vazio) |
| `/ai-review` | profile_ai_reviews | `tokens_in: 0, summary: null` | ✅ CONECTADO (hollow) |
| `/safety` | env vars + DB | `gate: PASS, live_profiles: 0` | ✅ CONECTADO |

Nenhum endpoint desconectado. Tabelas corretas, mas dados vazios.

---

## 11. FASE I — Frontend

**Evidência:** código analisado em `frontend/app/profile-intelligence/page.tsx`

- Live Engine tab: carrega 6 endpoints em paralelo (line 582–587) ✅
- Auto-refresh: `setInterval(30000)` ativo na aba Live Engine (line 642–643) ✅
- Shadow Analyzer: mostra `message` de fallback 7d quando `total_trades === 0` (line 2076–2079) ✅
- Indicator Calibration: só renderiza quando `liveIndicators && (top_winners ou top_losers)` (line 2165) — mostra nada quando vazio ✅ (UI correta: não há dados para mostrar)
- Activity Timeline: renderiza todos os eventos com timestamp + event_type + message (line 2243–2255) ✅
- AI Critic: mostra `tokens_input` e `summary` — quando null, oculta bloco ✅
- Auto-Pilot Calibration: conta `liveAdjustments.filter(...)` — 0 quando array vazio ✅

**Diagnóstico:** `FRONTEND_FULLY_CONNECTED` — UI reflete exatamente os dados da API. O que aparece vazio na tela é vazio na API, que é vazio no banco.

---

## 12. FASE J — Scheduler Medium Cycle e AI Cycle

### J.1 Código de decisão (evidência: código fonte)
```python
# _MEDIUM_INTERVAL_M = 30  (padrão)
# _AI_REVIEW_INTERVAL_H = 4 (padrão)

async def _needs_medium_cycle(db) -> bool:
    # busca último RUN_COMPLETED phase='medium' na activity_log
    # retorna True se nunca rodou OU se rodou > 30 min atrás
    ...

async def _needs_ai_cycle(db) -> bool:
    # busca último COMPLETED em profile_ai_reviews
    # retorna True se nunca rodou OU se rodou > 4h atrás
    ...
```

### J.2 Worker logs (evidência: railway logs)
```
[13:03:40] medium cycle done: {profiles_analyzed: 0, suggestions_generated: 0}
[13:03:40] AI review done: {summary: None}
```

Medium cycle roda a cada 30 min. Próxima janela: **13:33 UTC**.

### J.3 Critério
Medium e AI cycle são observáveis nos logs. **Não é `BLOCKED_MEDIUM_AI_CYCLE_NOT_OBSERVABLE`.**

---

## 13. FASE K — Nível de Operacionalidade

| Nível | Descrição | Status atual | Após fix |
|---|---|---|---|
| 1 | Heartbeat | ✅ ATIVO | ✅ |
| 2 | Shadow Scan | ✅ ATIVO (0 em 24h, fallback 7d) | ✅ |
| 3 | Profile Analysis | ❌ 0 profiles | ✅ Esperado após 13:33 |
| 4 | Indicator/Hard Negative Mining | ❌ 0 rows | ✅ Esperado após 13:33 |
| 5 | Calibration Suggestions | ❌ 0 suggestions | ✅ Se win_rate < 35% |
| 6 | AI Critic Integrated | ❌ HOLLOW (sem API key) | ❌ Requer ANTHROPIC_API_KEY |
| 7 | UI Live Fully Connected | ✅ Frontend conectado (dados vazios) | ✅ |

**Estado atual:** Nível 2  
**Estado esperado após 13:33 UTC:** Nível 4–5

---

## 14. Causa-Raiz Principal

### Root Cause 1 — PI_LIVE_LOOKBACK_H = 24 (padrão)

O medium cycle (`run_medium_cycle`) usa `_LOOKBACK_HOURS = int(os.environ.get("PI_LIVE_LOOKBACK_H", "24"))`. Sem esta variável definida, o engine minerava apenas as últimas 24h.

O shadow pipeline **parou de gerar novos trades completados nas últimas 24h** (os 7.526 trades disponíveis estão na janela de 7d, não 24h).

**Fix aplicado:** `PI_LIVE_LOOKBACK_H=168` definido em `scalpyn-worker-compute` via Railway (13:10 UTC).

### Root Cause 2 — ANTHROPIC_API_KEY ausente

`run_ai_review_cycle` verifica `ai_key = os.environ.get("ANTHROPIC_API_KEY", "")` — se vazio, pula o call à API da Anthropic e marca status='COMPLETED' com 0 tokens.

**Status:** Não corrigido. Requer provisionamento de API key da Anthropic.

### Root Cause 3 (minor) — UPDATE dead code em run_medium_cycle

O `run_medium_cycle` tentava `UPDATE profile_intelligence_runs SET suggestions_generated = :n WHERE id = :run_id` mas esse `run_id` nunca foi inserido na tabela (o INSERT foi removido no Fix 4 da sessão anterior). A UPDATE sempre afetava 0 rows.

**Fix aplicado:** código removido (commit `18a5422`).

---

## 15. FASE L — Tabelas Obrigatórias

### L.1 Profiles analisados

| profile_id | shadow_7d_trades | activity_events | indicator_rows | hard_negative_rows | suggestions | status |
|---|---:|---:|---:|---:|---:|---|
| 43 profiles (IDs não disponíveis via API) | 7526 total [API] | 0 [API] | 0 [API] | 0 [API] | 0 [API] | SHADOW_ONLY_NOT_ANALYZED |

### L.2 Indicadores analisados

| indicator_name | profiles | samples | status |
|---|---:|---:|---|
| (nenhum) | 0 | 0 | BLOCKED_24H_EMPTY |

### L.3 Activity Timeline Coverage

| Event type | Count (100 recentes) | Last seen | Expected? | Status |
|---|---:|---|---|---|
| HEARTBEAT | ~40 | 13:12:19 UTC | ✅ | PRESENTE |
| SCANNING_SHADOW | ~13 | 13:12:19 UTC | ✅ | PRESENTE |
| ANALYZING_PROFILES | ~13 | 13:12:19 UTC | ✅ | PRESENTE |
| RUN_COMPLETED(fast) | ~13 | 13:12:19 UTC | ✅ | PRESENTE |
| MINING_INDICATORS | ~2 | 13:03:39 UTC | ✅ | PRESENTE |
| MINING_HARD_NEGATIVES | ~2 | 13:03:39 UTC | ✅ | PRESENTE |
| GENERATING_ADJUSTMENT_SUGGESTIONS | ~2 | 13:03:39 UTC | ✅ | PRESENTE |
| RUN_COMPLETED(medium) | ~2 | 13:03:39 UTC | ✅ | PRESENTE |
| PROFILE_ANALYZED | 0 | — | ⚠️ | AUSENTE (sem dados) |
| SUGGESTION_CREATED | 0 | — | ⚠️ | AUSENTE (sem dados) |
| AI_REVIEW_* | 0 | — | ⚠️ | AUSENTE (não emitido pelo código atual) |

### L.4 Checklist ponta-a-ponta

| Contrato | Fonte | Status | Evidência |
|---|---|---|---|
| Fast cycle heartbeat | API /status | **PASS** | `is_stale: false, heartbeat 13:12:19` |
| Shadow scan 7d | API /shadow-summary | **PASS** | `fallback_total_trades: 7526` |
| Profile-level analysis | API /activity | **FAIL** | `0 eventos com profile_id` |
| Indicator mining | API /indicator-performance | **FAIL** | `{top_winners:[], top_losers:[]}` |
| Hard negative mining | API /shadow-summary | **FAIL** | `hard_negative_patterns_detected: 0` |
| Suggestions | API /adjustment-suggestions | **FAIL** | `{count: 0}` |
| Auto-Pilot calibration queue | API /adjustment-suggestions | **FAIL** | `{count: 0}` |
| AI Critic integrated | API /ai-review | **FAIL** | `tokens_in: 0, summary: null` |
| Activity Timeline vivo | API /activity | **PASS parcial** | Existe mas shallow |
| UI cards connected | frontend + API | **PASS** | Frontend reflete API fielmente |
| No profile creation | API /safety | **PASS** | `create_profile_enabled: false, live_profiles_count: 0` |
| No mutation/live/model active | API /safety | **PASS** | `mutations_applied_count: 0, live_trading_enabled: false, ml_gate_enabled: false` |

### L.5 Ledger de Evidências

| Afirmação | Origem | Valor literal |
|---|---|---|
| shadow_7d_trades = 7526 | [API] /live/shadow-summary | `"fallback_total_trades": 7526` |
| shadow_24h_trades = 0 | [API] /live/shadow-summary | `"total_trades": 0` |
| shadow_7d_profiles = 43 | [API] /live/shadow-summary | `"fallback_total_profiles": 43` |
| indicator_rows = 0 | [API] /live/indicator-performance | `{"top_winners": [], "top_losers": []}` |
| hard_negatives = 0 | [API] /live/shadow-summary | `"hard_negative_patterns_detected": 0` |
| suggestions = 0 | [API] /live/adjustment-suggestions | `{"items": [], "count": 0}` |
| mutations = 0 | [API] /live/safety | `"mutations_applied_count": 0` |
| live_trading = false | [API] /live/safety | `"live_trading_enabled": false` |
| ml_gate = false | [API] /live/safety | `"ml_gate_enabled": false` |
| live_profiles = 0 | [API] /live/safety | `"live_profiles_count": 0` |
| ai_tokens = 0 | [API] /live/ai-review | `"tokens_input": 0, "tokens_output": 0` |
| ai_summary = null | [API] /live/ai-review | `"summary": null` |
| PI_LIVE_LOOKBACK_H = 168 | [railway variables] scalpyn-worker-compute | `PI_LIVE_LOOKBACK_H │ 168` |
| ANTHROPIC_API_KEY = AUSENTE | [railway variables] scalpyn-worker-compute | não aparece na lista de vars |
| commit_hash = 18a5422f3bf4 | [API] /live/status | `"commit_hash": "18a5422f3bf4"` |
| last_heartbeat = 13:12:19 | [API] /live/status | `"last_heartbeat_at": "2026-06-27T13:12:19.782786+00:00"` |
| ML_GATE todos false | [railway variables] 6 serviços | coluna PASS ✅ em cada |
| next_medium_cycle ≈ 13:33 | [calc: 13:03:39 + 30min] | last medium=13:03:39 + 30min = 13:33:39 |

---

## 16. Ações Tomadas Nesta Sessão

### 16.1 PI_LIVE_LOOKBACK_H=168 (Railway env var)
```
railway variables --service scalpyn-worker-compute --set "PI_LIVE_LOOKBACK_H=168"
```
Efeito: medium cycle passa a minerar janela de 168h (7 dias) em vez de 24h. Ativo desde ~13:10 UTC.

### 16.2 Remoção de dead code (commit 18a5422)
Arquivo: `backend/app/services/profile_intelligence_live_service.py`  
Removidas linhas 396–400 (UPDATE profile_intelligence_runs em run_medium_cycle que sempre afetava 0 rows).

---

## 17. Próximas Ações

### Imediatas (sem código)
1. **Aguardar 13:33 UTC** — próximo medium cycle com PI_LIVE_LOOKBACK_H=168 → confirmar que `profile_indicator_performance` e `profile_hard_negative_patterns` recebem dados
2. **ANTHROPIC_API_KEY** — provisionar e setar em `scalpyn-worker-compute` para AI Critic operar (valor a ser fornecido pelo Ricardo)

### Se medium cycle às 13:33 ainda der 0 trades
- Verificar se `features_snapshot IS NOT NULL` filtra tudo — validar com SQL direto
- Confirmar se os 7526 trades em 7d têm `source IN ('L3','L3_LAB')` e `status = 'COMPLETED'`

### Longo prazo
- Adicionar evento `PROFILE_ANALYZED` no loop do medium cycle (atualmente o código não loga por profile)
- Adicionar `AI_REVIEW_SCHEDULED/RUNNING/COMPLETED` events na activity_log
- Considerar se a janela do fast cycle também deve usar `_LOOKBACK_HOURS` (atualmente é hardcoded 24h)

---

## 18. Veredito Final

```
BLOCKED_PROFILE_INTELLIGENCE_HEARTBEAT_ONLY
→ CAUSA IDENTIFICADA: PI_LIVE_LOOKBACK_H=24 padrão + shadow trades não chegando em 24h
→ FIX APLICADO: PI_LIVE_LOOKBACK_H=168 + dead code removed
→ AGUARDANDO: confirmação do próximo medium cycle (≈ 13:33 UTC) com dados reais
→ VEREDITO ESPERADO APÓS 13:33: PROFILE_INTELLIGENCE_24X7_LIVE_BUT_NO_CALIBRATION_YET
   (se sugestões ainda = 0 por insuficiência de evidência objetiva)
   ou PROFILE_INTELLIGENCE_24X7_ACTIVITY_CALIBRATION_VALIDATED
   (se sugestões forem geradas para profiles com win_rate < 35%)
```
