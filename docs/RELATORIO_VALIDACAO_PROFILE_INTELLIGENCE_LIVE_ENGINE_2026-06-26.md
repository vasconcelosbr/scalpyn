# RELATÓRIO DE VALIDAÇÃO — PROFILE INTELLIGENCE LIVE ENGINE
**Data:** 2026-06-26 / 2026-06-27  
**Escopo:** Validação ponta-a-ponta do Live Engine  
**Veredito final:** `BLOCKED_PROFILE_INTELLIGENCE_WORKER_NOT_RUNNING`

---

## 1. RESUMO EXECUTIVO

O Live Engine foi implementado de forma completa em backend, banco e frontend. A migration foi aplicada (`113_pi_live_engine`), as 9 tabelas existem, o código do worker existe, o beat agenda e despacha a task a cada 5 minutos, e o worker a recebe. Porém **toda execução falha com `RuntimeError('Event loop is closed')`** antes de gravar qualquer dado no banco.

Causa-raiz: `feedback_loop` usa `AsyncSessionLocal` (baseado no `_celery_engine` module-level), cujas conexões asyncpg ficam vinculadas ao event loop do *task anterior* (`compute_structural_5m`). O `_run_async` cria um novo loop para cada task, mas as conexões do pool detectam a incompatibilidade e levantam `RuntimeError`. Fix: usar `NullPool` em `_run_feedback_loop`, igual ao padrão de `_run_ml_challengers_only`.

Bug secundário (independente, bloqueante após o fix principal): `run_fast_cycle` tenta INSERT com coluna `started_at` que não existe na DB (a coluna real é `run_at`).

Resultado visível na UI: `Engine Status: STALE / IDLE`, todos os campos vazios — exatamente porque zero heartbeats foram gravados.

---

## 2. TELA OBSERVADA

```text
Safety Guard — PASS (hardcoded via endpoint /live/safety que lê DB real)
Engine Status: STALE / IDLE
Último heartbeat: —
Próximo ciclo: —
Worker: —
Shadow Analyzer: Sem dados de shadow disponíveis (24h sem trades completos)
AI Critic: NOT_STARTED
Auto-Pilot Calibration: 0 sugestões / 0 mutações
Indicator Calibration: vazio
Activity Timeline: vazia
```

---

## 3. FASE 0 — PRE-FLIGHT DE SEGURANÇA

**GATE: PASS**

| Métrica | Valor | Fonte |
|---|---|---|
| live_trading_enabled=true | 0 | [query] `SELECT COUNT(*) FILTER (WHERE live_trading_enabled=true) FROM profiles` |
| auto_pilot_enabled=true | 1 | [query] `SELECT COUNT(*) FILTER (WHERE auto_pilot_enabled=true) FROM profiles` — PI autopilot existente, não live trading |
| total_profiles | 109 | [query] mesmo SELECT |
| possible_live_orders | 0 | [query] `SELECT COUNT(*) FROM orders WHERE status NOT IN ('cancelled','rejected','simulation','shadow')` |
| active_new_models_24h | 0 | [query] `SELECT COUNT(*) FROM ml_models WHERE created_at >= now()-interval '24h' AND (status='active' OR activated_at IS NOT NULL)` |
| ML_GATE_ENABLED | false (todos os 6 serviços) | [railway variables] |

Serviços verificados com `ML_GATE_ENABLED=false`:
- `scalpyn` ✓
- `scalpyn-worker-micro` ✓
- `scalpyn-worker-structural` ✓
- `scalpyn-worker-compute` ✓
- `scalpyn-worker-execution` ✓
- `scalpyn-beat` ✓

---

## 4. FASE A — COMMIT E DEPLOY

| Item | Valor | Fonte |
|---|---|---|
| git HEAD | `464d37cc78b18e3e38c9ef41754862ddf329c432` | [git rev-parse HEAD] |
| Último commit | `464d37c fix(tasks): route feedback_loop to structural_compute queue` | [git log -5 --oneline] |
| scalpyn deploy | SUCCESS `0fe58a22` @ 2026-06-26 19:01:25 -03:00 | [railway deployment list] |
| scalpyn-beat deploy | SUCCESS `feace9ea` @ 2026-06-26 19:01:26 -03:00 | [railway deployment list] |
| scalpyn-worker-compute deploy | SUCCESS `28fae94d` @ 2026-06-26 19:01:25 -03:00 | [railway deployment list] |

### 5 commits recentes:
```
464d37c fix(tasks): route feedback_loop to structural_compute queue
d8177b6 fix(migration): move 113_pi_live_engine to versions/ (alembic root scan path)
b95f17c fix(migration): 113_pi_live_engine points to 000_baseline_prod_schema
aca5dd0 fix(models): rename metadata column attr to meta_json (SQLAlchemy reserved)
b3011a0 feat(profile-intelligence): add Live Engine + calibration-only autopilot
```

**Backend, beat e worker-compute implantados no commit que inclui o Live Engine.** ✓

---

## 5. FASE B — MIGRATIONS / TABELAS

**Alembic version no banco:** `113_pi_live_engine` [query `SELECT version_num FROM alembic_version`]

### Tabelas verificadas (9/9 existem):

| Tabela | Status | Fonte |
|---|---|---|
| profile_intelligence_heartbeats | EXISTS | [query information_schema.tables] |
| profile_intelligence_runs | EXISTS | [query information_schema.tables] |
| profile_intelligence_activity_log | EXISTS | [query information_schema.tables] |
| profile_indicator_performance | EXISTS | [query information_schema.tables] |
| profile_hard_negative_patterns | EXISTS | [query information_schema.tables] |
| profile_adjustment_suggestions | EXISTS | [query information_schema.tables] |
| profile_adjustment_versions | EXISTS | [query information_schema.tables] |
| profile_ai_reviews | EXISTS | [query information_schema.tables] |
| autopilot_pending_actions | EXISTS | [query information_schema.tables] |

### Colunas de profile_intelligence_runs (relevantes):
```
run_at, run_type, finished_at, suggestions_generated, ai_review_requested, ai_review_id
```
Nota: coluna existente é `run_at`, NÃO `started_at`. O service usa `started_at` — bug secundário.

**DB_CONTRACT: PASS (9/9 tabelas, migration aplicada)**

---

## 6. FASE C — WORKER / JOB / SCHEDULER

### C.1 Task definida:
- Arquivo: `backend/app/tasks/profile_intelligence_job.py:350-358`
- Nome: `app.tasks.profile_intelligence_job.feedback_loop`

### C.2 Beat schedule (celery_app.py:627-633):
```python
"profile_intelligence_live": {
    "task": "app.tasks.profile_intelligence_job.feedback_loop",
    "schedule": float(os.environ.get("PI_LIVE_FAST_INTERVAL_S", 300)),  # 5 min
    "options": {"queue": QUEUE_STRUCTURAL_COMPUTE},
}
```

### C.3 TASK_ROUTES (celery_app.py:199):
```python
"app.tasks.profile_intelligence_job.feedback_loop": {"queue": QUEUE_STRUCTURAL_COMPUTE},
```

### C.4 Beat despachando (log scalpyn-beat):
```
[2026-06-27 00:02:16,434] Scheduler: Sending due task profile_intelligence_live (app.tasks.profile_intelligence_job.feedback_loop)
[2026-06-27 00:07:16,437] Scheduler: Sending due task profile_intelligence_live (app.tasks.profile_intelligence_job.feedback_loop)
```

### C.5 Worker recebendo (log scalpyn-worker-compute):
```
[2026-06-27 00:12:16,442: INFO/MainProcess] Task app.tasks.profile_intelligence_job.feedback_loop[5d4159d2-2d52-4a88-80f0-1edb6d6c2679] received
```

### C.6 Worker falhando (log scalpyn-worker-compute):
```
[2026-06-27 00:12:16,447: ERROR/ForkPoolWorker-2] Task app.tasks.profile_intelligence_job.feedback_loop[5d4159d2-2d52-4a88-80f0-1edb6d6c2679] raised unexpected: RuntimeError('Event loop is closed')
  File "/app/app/tasks/profile_intelligence_job.py", line 358, in feedback_loop
    return _run_async(_run_feedback_loop())
  File "/app/app/tasks/profile_intelligence_job.py", line 56, in _run_async
  File "/app/app/tasks/profile_intelligence_job.py", line 322, in _run_feedback_loop
```

**WORKER_STATUS: BLOCKED_WORKER_RUNTIME_ERROR**

---

## 7. FASE D — HEARTBEATS

| Métrica | Valor | Fonte |
|---|---|---|
| heartbeat_rows | 0 | [query] `SELECT COUNT(*) FROM profile_intelligence_heartbeats` |
| last_heartbeat | NULL | [query] `SELECT MAX(heartbeat_at) FROM profile_intelligence_heartbeats` |
| seconds_since_last | NULL | [query] |

**Tabela existe e está vazia.** O worker falha antes de gravar o heartbeat.

**STATUS: BLOCKED_NO_HEARTBEAT_ROWS**

---

## 8. FASE E — RUNS E ACTIVITY LOG

### Runs (profile_intelligence_runs):
- Total rows: 10 [query]
- Todas com `trigger_source='manual'`, `run_type=NULL`, `lookback_days=7`
- São runs do PI Engine antigo (não do Live Engine)
- Zero runs com `run_type='fast'` (Live Engine)

### Activity log:
- ACTIVITY_COUNT: 0 [query `SELECT COUNT(*) FROM profile_intelligence_activity_log`]

**STATUS: BLOCKED_ACTIVITY_LOG_EMPTY + BLOCKED_RUNS_EMPTY (para Live Engine)**

---

## 9. FASE F — CONTRATOS DE SHADOW

| Janela | total_trades | profiles | wins | losses | Fonte |
|---|---|---|---|---|---|
| L3+L3_LAB 24h | 0 | 0 | 0 | 0 | [query shadow_trades] |
| L3+L3_LAB 7d | 7658 | 44 | 3041 | 4617 | [query shadow_trades] |
| L1_SPECTRUM 24h | 0 | 0 | 0 | 0 | [query shadow_trades] |

A UI exibe "Sem dados de shadow disponíveis" — **correto** para janela 24h (zero trades completados nas últimas 24h). Dados de 7 dias existem (7658 trades). O problema não é ausência de dados — é que o engine nunca rodou para processá-los.

**STATUS: NO_24H_DATA_BUT_7D_AVAILABLE — mensagem de UI correta para janela atual**

---

## 10. FASE G — APIs

### Rotas registradas:
- `backend/app/api/profile_intelligence_live.py:15` — `APIRouter(prefix="/api/profile-intelligence/live")`
- `backend/app/main.py:468-469` — `app.include_router(pi_live_api.router)`

### Endpoints existentes (código):
| Endpoint | Path:Line |
|---|---|
| GET /status | profile_intelligence_live.py:17 |
| GET /activity | profile_intelligence_live.py:62 |
| GET /shadow-summary | profile_intelligence_live.py:88 |
| GET /indicator-performance | profile_intelligence_live.py:148 |
| GET /adjustment-suggestions | profile_intelligence_live.py:190 |
| GET /ai-review | profile_intelligence_live.py:240 |
| GET /safety | profile_intelligence_live.py:281 |

### Teste sem autenticação:
```
curl https://scalpyn-production.up.railway.app/api/profile-intelligence/live/status
→ {"detail":"Not authenticated"}
```
Endpoints requerem cookie de sessão — esperado. Backend lê DB real (sem mock/default hardcoded).

**API: CONNECTED_TO_DB — lê tabelas reais, retorna vazio pois worker não populou dados**

---

## 11. FASE H — FRONTEND

### Componentes:
- `frontend/app/profile-intelligence/page.tsx:582-588` — 7 chamadas ao vivo

```typescript
apiGet("/profile-intelligence/live/status").catch(() => null),
apiGet("/profile-intelligence/live/activity?limit=50").catch(() => null),
apiGet("/profile-intelligence/live/shadow-summary?hours=24").catch(() => null),
apiGet("/profile-intelligence/live/indicator-performance?limit=20").catch(() => null),
apiGet("/profile-intelligence/live/adjustment-suggestions?limit=30").catch(() => null),
apiGet("/profile-intelligence/live/ai-review").catch(() => null),
apiGet("/profile-intelligence/live/safety").catch(() => null),
```

### Polling:
- Tab switch dispara `loadTab("Live Engine")` (useEffect em linha 638)
- **Não há setInterval para auto-refresh da aba Live Engine**
- Polling automático existe apenas para Auto-Pilot (while-loop com 3s delay em linha 678-690)

**PARTIAL_FRONTEND_NO_LIVE_POLLING** — UI consome endpoints corretos ao abrir a aba, mas não refresca automaticamente. Para uma UI de "Live Engine 24x7", auto-refresh a cada 15-30s seria o esperado.

---

## 12. FASE I — AI CRITIC

| Métrica | Valor | Fonte |
|---|---|---|
| profile_ai_reviews rows | 0 | [query] |
| next_review_at via API | calculado em tempo real | [código] `datetime.now() + timedelta(hours=4)` |

Código existe em `profile_intelligence_live_service.py:443-574`. Agendamento: a cada execução do fast_cycle, `_check_ai_needed()` avalia se 4h se passaram desde o último review. Nunca executou porque o fast_cycle nunca completou.

**STATUS: BLOCKED_AI_CRITIC_NOT_IMPLEMENTED — worker quebrado antes de chegar ao AI cycle**

---

## 13. FASE J — SUGGESTIONS E AUTO-PILOT CALIBRATION

| Tabela | Rows | Fonte |
|---|---|---|
| profile_adjustment_suggestions | 0 | [query] |
| autopilot_pending_actions | 0 | [query] |

Ações proibidas verificadas:
- CREATE_PROFILE: 0 ✓
- DUPLICATE_PROFILE: 0 ✓
- PROMOTE_LIVE: 0 ✓
- ENABLE_LIVE: 0 ✓

**STATUS: CALIBRATION_EMPTY_EXPECTED_NO_EVIDENCE — medium cycle nunca rodou (fast cycle falha antes)**

---

## 14. FASE K — INDICATOR PERFORMANCE E HARD NEGATIVES

| Tabela | Rows | Fonte |
|---|---|---|
| profile_indicator_performance | 0 | [query] |
| profile_hard_negative_patterns | 0 | [query] |

Medium cycle nunca executou (fast cycle falha antes de completar).

**STATUS: BLOCKED_ANALYZER_NOT_POPULATING_TABLES** (causa: worker runtime error)

---

## 15. FASE L — NENHUM PROFILE CRIADO

| Métrica | Valor | Fonte |
|---|---|---|
| total_profiles | 109 | [query profiles] |
| profiles_created_24h | 0 | [query WHERE created_at >= now()-24h] |
| CREATE events no activity_log | 0 | [query event_type ILIKE '%CREATE%'] |
| Constraints no DB | chk_adj_sugg_type_not_create, chk_apa_action_type_not_create | [migration 113] |

**PASS: Nenhum profile criado. Constraints de banco impedem ações proibidas.**

---

## 16. FASE M — SAFETY GUARD REAL

O endpoint `/live/safety` lê dados reais do banco + env var (código em `profile_intelligence_live.py:281-328`):

| Guarda | Valor real | Origem |
|---|---|---|
| ml_gate_enabled | false | [env var ML_GATE_ENABLED] |
| live_profiles_count | 0 | [query COUNT(*) FILTER (WHERE live_trading_enabled=true)] |
| mutations_applied_count | 0 | [query profile_adjustment_suggestions WHERE mutation_applied=true] |
| forbidden_actions_attempted | 0 | [query autopilot_pending_actions WHERE action_type IN ('CREATE_PROFILE',...)] |
| gate | "PASS" | [calc: todos os 4 checks passam] |

**Safety Guard: REAL (não hardcoded) — lê DB + env var em cada request. PASS.**

---

## 17. FASE N — DIAGNÓSTICO DE CAUSA-RAIZ

**Causa-raiz primária:** `WORKER_RUNTIME_ERROR`

```
RuntimeError('Event loop is closed')
```

### Análise técnica:

`feedback_loop` (Celery task) usa `_run_async()` que cria um novo asyncio event loop. O `_run_feedback_loop` usa `AsyncSessionLocal` que depende do `_celery_engine` — um `AsyncEngine` criado uma vez no módulo `database.py`.

asyncpg vincula suas conexões ao event loop em que foram criadas. Quando `compute_structural_5m` roda no mesmo `ForkPoolWorker-2` e depois encerra seu loop (em `_run_async.finally`), as conexões asyncpg do `_celery_engine` ficam vinculadas ao loop fechado. Quando `feedback_loop` roda no mesmo processo e cria um **novo** loop, `AsyncSessionLocal()` tenta usar essas conexões stale — asyncpg detecta a incompatibilidade e levanta `RuntimeError('Event loop is closed')`.

**Padrão de fix já aplicado em `_run_ml_challengers_only` (profile_intelligence_job.py:206-218):** usa `NullPool` para criar um engine fresco a cada execução, eliminando o conflito de loop.

### Bug secundário (independente):

`run_fast_cycle` (profile_intelligence_live_service.py:~190) insere em `profile_intelligence_runs` com coluna `started_at`:
```sql
INSERT INTO profile_intelligence_runs
    (id, run_type, trigger_source, status, started_at, finished_at, ...)
```
A coluna real no banco é `run_at` (não `started_at`). Esse INSERT falharia com `UndefinedColumnError` mesmo se o bug de event loop fosse corrigido. **O heartbeat é commitado ANTES desse INSERT** — portanto, depois do fix do event loop, heartbeats seriam gravados e a tela não ficaria mais vazia, mas os runs teriam erro separado.

---

## 18. CHECKLIST PONTA-A-PONTA

| Contrato | Fonte | Status | Evidência |
|---|---|---|---|
| DB tables existem (9/9) | [query information_schema] | **PASS** | 9 tabelas existem |
| Migration aplicada | [query alembic_version] | **PASS** | `113_pi_live_engine` |
| Worker existe no código | `profile_intelligence_job.py:350` | **PASS** | task `feedback_loop` |
| Scheduler registrado no beat | `celery_app.py:627` | **PASS** | `profile_intelligence_live` a cada 300s |
| Beat despachando a task | [log scalpyn-beat] | **PASS** | log 00:02:16, 00:07:16 |
| Worker recebendo a task | [log scalpyn-worker-compute] | **PASS** | log 00:12:16 received |
| Worker executando sem erro | [log scalpyn-worker-compute] | **FAIL** | RuntimeError('Event loop is closed') |
| Heartbeat gravado | [query heartbeats] | **FAIL** | 0 rows |
| API lê heartbeat | [código] | PASS (código correto, mas tabela vazia) | profile_intelligence_live.py:27 |
| UI consome API | `page.tsx:582-588` | **PASS** | 7 endpoints chamados |
| UI auto-polling | `page.tsx` | **PARTIAL** | apenas on tab-switch, sem setInterval |
| Shadow query tem dados (24h) | [query shadow_trades] | **FAIL** | 0 trades (24h) — mas 7658 em 7d |
| Shadow query tem dados (7d) | [query shadow_trades] | **PASS** | 7658 trades |
| Analyzer grava metrics | [query] | **FAIL** | 0 rows indicator_performance, hard_negatives |
| Suggestions referenciam profiles | [query join] | N/A | 0 suggestions geradas |
| AI Critic executado | [query ai_reviews] | **FAIL** | 0 rows |
| Safety real (não hardcoded) | [código + DB] | **PASS** | lê DB + env var |
| Nenhum profile criado 24h | [query profiles] | **PASS** | 0 profiles_created_24h |

---

## 19. LEDGER DE EVIDÊNCIAS

| Afirmação | Origem | Valor literal |
|---|---|---|
| live_trading_enabled count | [query] | `{'live_enabled': 0}` |
| autopilot_enabled count | [query] | `{'autopilot_enabled': 1}` |
| total_profiles | [query] | `{'total_profiles': 109}` |
| live_orders | [query] | `0` |
| new_models_24h | [query] | `0` |
| ML_GATE_ENABLED (todos serviços) | [railway variables] | `false` |
| git HEAD | [git rev-parse HEAD] | `464d37cc78b18e3e38c9ef41754862ddf329c432` |
| scalpyn deploy | [railway deployment list] | `SUCCESS 0fe58a22 @ 2026-06-26 19:01:25` |
| alembic version | [query alembic_version] | `113_pi_live_engine` |
| tabelas existentes | [query information_schema] | `9 of 9` |
| heartbeat_rows | [query] | `0` |
| activity_log_rows | [query] | `0` |
| runs com run_type=fast | [query] | `0` (10 runs total, todos run_type=NULL/manual) |
| L3 24h completed | [query shadow_trades] | `{'l3_completed_24h': 0, 'l3_profiles_24h': 0}` |
| L3 7d completed | [query shadow_trades] | `{'l3_completed_7d': 7658, 'l3_profiles_7d': 44}` |
| L1 24h completed | [query shadow_trades] | `{'l1_completed_24h': 0}` |
| indicator_performance rows | [query] | `0` |
| hard_negative_patterns rows | [query] | `0` |
| ai_reviews rows | [query] | `0` |
| suggestions rows | [query] | `0` |
| autopilot_pending_actions rows | [query] | `0` |
| forbidden_actions | [query] | `0` |
| profiles_created_24h | [query] | `0` |
| mutations_applied | [query] | `0` |
| feedback_loop error | [log scalpyn-worker-compute] | `RuntimeError('Event loop is closed') @ 00:12:16` |
| task received | [log] | `[5d4159d2-...] received @ 00:12:16,442` |
| beat schedule interval | [celery_app.py:629] | `PI_LIVE_FAST_INTERVAL_S=300` (default) |
| worker queue | [railway variables scalpyn-worker-compute] | `WORKER_QUEUES=structural_compute` |

---

## 20. VEREDITO

```
BLOCKED_PROFILE_INTELLIGENCE_WORKER_NOT_RUNNING
```

**Com causa secundária secundária:**
```
WORKER_RUNTIME_ERROR — RuntimeError('Event loop is closed') em todo ciclo do feedback_loop
```

---

## 21. PRÓXIMAS AÇÕES NECESSÁRIAS (não executadas neste relatório)

### Ação 1 — BLOQUEANTE (fix principal)
**Arquivo:** `backend/app/tasks/profile_intelligence_job.py`  
**Problema:** `_run_feedback_loop` usa `AsyncSessionLocal` que reutiliza `_celery_engine` com conexões asyncpg do loop anterior  
**Fix:** replicar padrão de `_run_ml_challengers_only` — criar engine com `NullPool` localmente em `_run_feedback_loop`

```python
# Antes:
async def _run_feedback_loop():
    from ..database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        ...

# Depois (padrão de _run_ml_challengers_only):
async def _run_feedback_loop():
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import NullPool
    from ..config import settings
    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as db:
            result = await run_fast_cycle(db)
            ...
    finally:
        await engine.dispose()
```

### Ação 2 — Bloqueante após Ação 1
**Arquivo:** `backend/app/services/profile_intelligence_live_service.py`  
**Problema:** `run_fast_cycle` usa coluna `started_at` que não existe no banco (coluna real: `run_at`)  
**Fix:** trocar `started_at` por `run_at` no INSERT de `profile_intelligence_runs`

### Ação 3 — Melhoria de UX (não bloqueante para funcionamento)
**Arquivo:** `frontend/app/profile-intelligence/page.tsx`  
**Problema:** Live Engine não tem auto-refresh — dados ficam stale após tab-switch  
**Fix:** adicionar `setInterval` de 30s na aba Live Engine para recarregar `/live/status` e `/live/activity`
