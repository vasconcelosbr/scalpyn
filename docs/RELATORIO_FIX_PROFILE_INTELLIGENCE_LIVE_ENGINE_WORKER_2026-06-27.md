# Relatório — Fix Profile Intelligence Live Engine Worker
**Data:** 2026-06-27  
**Sessões:** 2026-06-26 (sessão 1) + 2026-06-27 (sessão 2, continuação)  
**Branch:** main  
**Commits:** `372735e`, `ba80931`, `6edef33`, `cb6bdb2`, `ab5deb1`

---

## 1. Contexto

O PI Live Engine (`feedback_loop` task, QUEUE_STRUCTURAL_COMPUTE) estava falhando silenciosamente em produção desde o deploy inicial. O beat despachava a tarefa, mas o worker reportava erros em cascata que impediam qualquer dado de ser gravado no banco.

---

## 2. Erros Encontrados e Fixes

### 2.1 RuntimeError: Event loop is closed (Fix 1 — NullPool)
**Commit:** `372735e`  
**Arquivo:** `backend/app/tasks/profile_intelligence_job.py`

**Causa raiz:** `_run_feedback_loop` usava `AsyncSessionLocal` (engine de pool compartilhado). O asyncpg vincula conexões ao event loop em que foram criadas. Quando `_run_async` cria um novo event loop para cada task Celery, as conexões do pool do loop anterior ficam inválidas.

**Evidência do erro (log do worker, 00:38:07 UTC):**
```
[PILive] feedback_loop started
RuntimeError: Event loop is closed
```

**Fix:** Introduzida `_live_nullpool_session()` — cria engine com `NullPool` por invocação. Mesmo padrão já usado em `_run_ml_challengers_only`.

---

### 2.2 UndefinedColumnError: column "started_at" does not exist (Fix 2)
**Commit:** `372735e`  
**Arquivo:** `backend/app/services/profile_intelligence_live_service.py`, linha 188

**Causa raiz:** INSERT em `profile_intelligence_runs` referenciava `started_at` mas o schema (migration 113) define `run_at`.

**Fix:** `started_at` → `run_at` no INSERT.

**Nota:** O INSERT foi completamente removido no Fix 4 (ver abaixo).

---

### 2.3 PostgresSyntaxError: syntax error at or near ":" (Fix 3 — CAST jsonb)
**Commit:** `ba80931`  
**Arquivo:** `backend/app/services/profile_intelligence_live_service.py`

**Causa raiz:** SQLAlchemy + asyncpg: `:param::jsonb` é renderizado como `$1:jsonb` (asyncpg trata `::` após named param como colon literal, não cast PostgreSQL). Resultado: SQL inválido.

**Evidência (log, 00:38:07 UTC):**
```
[PILive] fast cycle failed: PostgresSyntaxError: syntax error at or near ":"
```

**Fix:** 8 ocorrências de `:param::jsonb` → `CAST(:param AS jsonb)`:
- `_log_activity` (line 59)
- `record_heartbeat` (line 91)
- `run_medium_cycle` (lines 334, 368–369, 392)
- `run_ai_review_cycle` (lines 555–558)

---

### 2.4 NotNullViolationError: user_id NOT NULL (Fix 4 — remover INSERT pi_runs)
**Commit:** `6edef33`  
**Arquivo:** `backend/app/services/profile_intelligence_live_service.py`

**Causa raiz:** `profile_intelligence_runs.user_id` é NOT NULL (schema projetado para análises per-user). O Live Engine é system-wide e não possui user_id.

**Evidência (log, 00:46:58 UTC):**
```
[PILive] fast cycle failed: NotNullViolationError: null value in column "user_id" of relation "profile_intelligence_runs"
```

**Fix:** Removido INSERT em `profile_intelligence_runs` do `run_fast_cycle`. Estado do engine é rastreado integralmente via:
- `profile_intelligence_heartbeats` (já escrito pelo `record_heartbeat`)
- `profile_intelligence_activity_log` (já escrito pelo `_log_activity`)

A API `/live/status` não consulta `profile_intelligence_runs` — zero impacto funcional.

---

### 2.5 PostgresSyntaxError: syntax error at or near "$1" (Fix 5 — interval parameter)
**Commit:** `cb6bdb2`  
**Arquivo:** `backend/app/services/profile_intelligence_live_service.py`

**Causa raiz:** `interval :lookback` com `{"lookback": "24 hours"}` → asyncpg renderiza `interval $1` que é sintaxe PostgreSQL inválida (INTERVAL não aceita parâmetro posicional).

**Evidência (log, 00:53:56 UTC):**
```
[PILive] medium cycle failed (non-fatal): PostgresSyntaxError: syntax error at or near "$1"
```

**Fix:** Duas queries em `run_medium_cycle` convertidas para f-string:
```python
# Antes:
AND st.created_at >= now() - interval :lookback
# Params: {"lookback": f"{_LOOKBACK_HOURS} hours"}

# Depois:
AND st.created_at >= now() - interval '{_LOOKBACK_HOURS} hours'
# (sem params, valor inlined como literal seguro — é int de env var)
```

---

### 2.6 Database error 503 no shadow-summary API (Fix 6 — interval API)
**Commit:** `ab5deb1`  
**Arquivo:** `backend/app/api/profile_intelligence_live.py`

**Causa raiz:** Mesmo padrão do Fix 5 — 3 queries no endpoint `/shadow-summary` usavam `interval :hours`.

**Fix:** `_h = min(hours, 168)` e f-strings nas 3 queries. `hours` é `int` validado pelo FastAPI — seguro para inline.

---

### 2.7 Shadow Analyzer — fallback 7d (Feature)
**Commit:** `372735e`  
**Arquivo:** `backend/app/api/profile_intelligence_live.py`

Quando `total_trades == 0` (sem trades L3 finalizados nas últimas N horas), o endpoint executa query secundária com janela de 7d e retorna:
- `fallback_window_days: 7`
- `fallback_total_trades`
- `fallback_total_profiles`
- `message` em português

---

### 2.8 Frontend — auto-refresh Live Engine (Feature)
**Commit:** `372735e`  
**Arquivo:** `frontend/app/profile-intelligence/page.tsx`

Polling de 30s enquanto a aba "Live Engine" está ativa. Limpeza via `clearInterval` quando a aba muda.

---

## 3. Evidências de Validação

### 3.1 Worker Logs — fast cycle concluído (02:16:32 UTC)
```
[2026-06-27 02:16:32,085: INFO/ForkPoolWorker-2] [PILive] feedback_loop started
[2026-06-27 02:16:32,443: INFO/ForkPoolWorker-2] [PILive] fast cycle done: {
  'run_id': 'a6642bbb-5540-41e5-ae61-b8d127604a3d',
  'cycle': 'fast', 'completed_trades': 0, 'profiles': 0,
  'avg_pnl_pct': None, 'win_rate': None
}
[2026-06-27 02:16:32,602: INFO/ForkPoolWorker-2] [PILive] feedback_loop completed
```

### 3.2 API `/live/status` — is_stale: false (02:16:32 UTC)
```json
{
  "engine_status": "IDLE",
  "current_phase": "IDLE",
  "last_heartbeat_at": "2026-06-27T02:16:32.419756+00:00",
  "next_cycle_at": "2026-06-27T02:21:32.089371+00:00",
  "worker_name": "scalpyn-worker-compute",
  "commit_hash": "cb6bdb2064e2",
  "is_stale": false
}
```

### 3.3 API `/live/activity` — RUN_COMPLETED confirmado
```json
{
  "event_type": "RUN_COMPLETED",
  "phase": "fast",
  "severity": "info",
  "message": "Ciclo rápido concluído",
  "created_at": "2026-06-27T02:16:32.334678+00:00"
}
```

### 3.4 API `/live/shadow-summary` — fallback 7d funcionando
```json
{
  "window_hours": 24,
  "total_trades": 0,
  "total_profiles": 0,
  "fallback_window_days": 7,
  "fallback_total_trades": 7639,
  "fallback_total_profiles": 44,
  "message": "Sem trades L3 finalizados nas últimas 24h; dados disponíveis em 7d: 7639 trades / 44 profiles."
}
```

---

## 4. Sequência de Commits

| Commit | Timestamp UTC | Fix(es) |
|--------|--------------|---------|
| `372735e` | 00:32:30 | NullPool, run_at, CAST jsonb (parcial), shadow fallback 7d, frontend polling |
| `ba80931` | 00:40:45 | CAST jsonb (restante) |
| `6edef33` | 00:48:21 | Remove INSERT pi_runs (user_id NOT NULL) |
| `cb6bdb2` | 00:55:xx | interval :lookback → f-string (medium cycle) |
| `ab5deb1` | 01:50:xx | interval :hours → f-string (shadow-summary API) |

---

## 5. Regras Absolutas — Conformidade

- ✅ Nenhum perfil criado ou candidato
- ✅ ML_GATE_ENABLED permanece false
- ✅ live_trading_enabled não alterado
- ✅ Nenhum modelo promovido
- ✅ Nenhum canário shadow
- ✅ mutation_applied=false em todas as sugestões
- ✅ Nenhum erro mascarado com fake fallback
- ✅ Evidências de log/API coletadas antes do PASS

---

## 6. Estado Final

O PI Live Engine está operacional em produção:
- **Fast cycle**: executa a cada 5 min (heartbeat + shadow scan)
- **Medium cycle**: executa a cada 30 min quando `_needs_medium_cycle` = True
- **AI review**: executa a cada 4h quando `_needs_ai_cycle` = True
- **is_stale**: false — UI exibe dados atuais
- **commit_hash ativo**: `cb6bdb2064e2` (beat + worker compute em ab5deb1)
