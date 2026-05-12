# Scalpyn

Scalpyn is an institutional-grade cryptocurrency trading platform that provides advanced analytics, robust scoring, and automated trading capabilities to users.

## Run & Operate

**To start the application:**

- **Frontend:** `cd frontend && npm run dev` (runs on port 5000)
- **Backend API:** `cd backend && uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload` (runs on port 8000)

**Required Environment Variables:**

- `DATABASE_URL`: PostgreSQL connection string (e.g., `postgresql://user:pass@host:port/db`)
- `JWT_SECRET`: Secret key for JWT signing (generate with `openssl rand -hex 32`)
- `ENCRYPTION_KEY`: AES key for encrypting API credentials (generate with `openssl rand -hex 16`)
- `REDIS_URL`: Redis connection string (optional, defaults to `redis://localhost:6379/0`)
- `BACKEND_URL`: Backend URL for frontend proxy (defaults to `http://localhost:8000`)
- `PROMETHEUS_BEARER_TOKEN`: Bearer token for scraping `/metrics` endpoint.
- `ENABLE_GATE_WS`: Set to `1` to enable Gate.io WebSocket for real-time order flow (default `0`).

## Stack

- **Frontend:** Next.js 16 (App Router), TypeScript, TailwindCSS, shadcn/ui
- **Backend:** FastAPI (Python 3.12), SQLAlchemy 2.0, Alembic
- **Database:** PostgreSQL (Replit managed), TimescaleDB extension is gracefully handled (not available)
- **Task Queue:** Celery, Redis
- **Exchange Integration:** Gate.io API v4

## Where things live

- `frontend/`: Next.js application code.
- `backend/`: FastAPI application code.
  - `app/main.py`: FastAPI app entry point.
  - `app/config.py`: Application settings and environment variable handling.
  - `app/api/`: API route handlers.
  - `app/models/`: SQLAlchemy ORM database models.
  - `app/schemas/`: Pydantic schemas for data validation and serialization.
  - `app/services/`: Business logic and service implementations.
  - `app/engines/`: Trading engine implementations.
  - `app/tasks/`: Celery task definitions.
- `docs/`: Architecture documentation, Grafana configurations, and runbooks.
  - `docs/grafana/`: Grafana dashboard definitions and alert rules.
  - `backend/alembic/versions/`: Database migration scripts (source of truth for schema).
  - `backend/app/_critical_schema.py`: Lists critical database columns for schema health checks.

## Architecture decisions

- **Single-source-of-truth for Indicators**: All indicator reads for decision-making must go through `app/services/indicators_provider.get_merged_indicators` to ensure a complete and merged view of structural and microstructure indicators. Direct DB queries are forbidden.
- **Deterministic Scoring**: The scoring formula `score = (sum_matched_points / total_possible_points) × 100` is purely deterministic. Confidence influences `can_trade` gating but does not multiply into the score numerator or denominator.
- **Real-time Order Flow via WebSocket**: `taker_ratio` and `volume_delta` are primarily sourced from a Gate.io Spot WebSocket, buffered in Redis, with a fallback to REST polling. This prioritizes real-time data for indicators.
- **Schema Bootstrap Robustness**: Database migrations are handled by Alembic, executed at Cloud Run startup with aggressive retry and timeout mechanisms. A critical schema check ensures essential columns exist, preventing silent drift and enabling automatic rollback on failure.
- **Structural 30m pipeline split (Task #262)**: o pipeline estrutural saiu de `collect_all` (60s) e virou `collect_structural_30m` @ `crontab(minute="0,30")` UTC, alinhado ao fechamento da candle 30m do Gate.io. Cadeia: `collect_structural_30m → compute_30m → score → evaluate`. `collect_all` ficou ticker/metadata-only (≈5 700 fetch_ohlcv/h removidos). `compute_indicators.compute` (1h) virou stub `DEPRECATED` (mantido só para preservar a route — invariant #4 do lint test — até a próxima limpeza). Opção A escolhida: `scheduler_group="structural"` reutilizado (zero mudanças em `indicator_merge`/`indicators_provider`). Runbook: `backend/docs/runbooks/structural-30m-refactor.md`.
- **Recompute Rejected Tab Trace on Read**: The `Rejected` tab dynamically recomputes `evaluation_trace` on every read to immediately reflect backend semantic changes (e.g., new `SKIPPED` reasons, plausibility bounds) without waiting for scheduled snapshots.
- **Ingestion vs Execution gates split** (Task #232, migration 043): `pool_coins.is_active` is the **ingestion** gate (collector, indicators, scoring, `pipeline_scan` funnel entry, WS subscription resolver). `pool_coins.is_tradable` is the **execution** gate read only by `evaluate_signals` and `execute_buy`. Defaults: `is_active=true`, `is_tradable=false` (trading is opt-in per symbol). A BEFORE-UPDATE trigger mirrors `is_approved → is_tradable` so legacy SQL paths keep working through one rolling deploy. Lint test `test_pool_queries_filter_*` enforces the right column per file (see `backend/tests/test_celery_routing_invariants.py`). Operator runbook: `backend/docs/runbooks/pool-execution-gate.md`.

## Product

- **Comprehensive Crypto Trading**: Provides tools for institutional-grade crypto trading.
- **Advanced Indicator Analysis**: Consolidates structural and microstructure indicators for informed decision-making.
- **Robust Scoring Engine**: Evaluates asset scores based on a sophisticated rule engine, providing confidence metrics and trade signals.
- **Watchlist Management**: Allows users to create and manage watchlists with detailed asset evaluation traces.
- **Real-time Data Feeds**: Integrates real-time order flow data from exchanges via WebSockets.
- **Trade History & P&L Tracking**: Imports and analyzes closed spot orders, calculating profit and loss.
- **Operational Monitoring**: Integrates with Grafana for real-time monitoring of system health, data quality, and alerts.
- **Native Performance Dashboard** (`/dashboard/performance`, Task #224): seven panels (health, system status, ingest rate, decisions, trades, sim-vs-real, ML dataset) backed by `GET /api/dashboard/*` read-only aggregations. Native alternative to the Grafana iframe — the legacy MonitoringTab is retained for backward compatibility.
- **Persistence queue** (`backend/app/services/persistence/`, Task #226): bounded asyncio queue + 4 long-running workers consume idempotent UPSERT messages and execute one short transaction per message via `run_uow`. Foundation only — opt-in via `USE_PERSISTENCE_QUEUE=1`. Three schedulers (combined / structural / microstructure) already enqueue when the flag is on; they keep the legacy `run_db_task` path when off (zero-risk rollout). Healthcheck at `GET /api/system/persistence`. Prometheus metrics under `scalpyn_persistence_*`. Producers MUST never call `enqueue` from inside a DB transaction. Heavier consumers (`collect_market_data`, `compute_indicators_*`, `pipeline_scan`, `trade_reconciliation_service`) are still on the legacy path — see follow-up tasks.
- **Centro Operacional** (`/dashboard/performance`, Task #225): rewrite of the perf page powered by the eventually-consistent `OperationalSnapshotService` (`backend/app/services/operational_snapshot.py`). Six background refreshers (ingestion 10s, celery 15s, redis 15s, db 30s, score 60s, latency 60s) feed `GET /api/dashboard/overview` (single O(1) aggregation: snapshots + alerts). Per-family endpoints kept for debugging (`/celery`, `/redis`, `/db-health`, `/score-engine`, `/pipeline-latency`, `/ingestion`, `/alerts`, `/events`). Health thresholds raised 6/10 → 10/20 min after observing legitimate catch-ups stretching to 12-14 min.

## User preferences

- **Sempre publicar (deploy) ao final de cada tarefa.** Após `mark_task_complete`, chamar `suggest_deploy` para que a versão em produção (ex.: `scalpyn.vercel.app` no front + backend hospedado) reflita o código mais recente. Sem isso, o usuário continua vendo 404 em rotas novas.
- **Antes do checkpoint final, rodar `cd frontend && npx tsc --noEmit -p .`** para validar o build de produção. A Vercel falha com qualquer type error que o `next dev` não pega (ex.: `Tooltip` do recharts tipa `formatter`'s `value` como `ValueType | undefined`, não aceita anotação `(v: number) => …`). Corrigir antes do push evita ciclo de deploys vermelhos.
- Comunicação em PT-BR.

## Gotchas

### Onboarding (dev)
- **TimescaleDB warnings**: extension não disponível no Replit, fallback automático para PostgreSQL standard.
- **`DATABASE_URL` format**: validator converte `postgresql://` → `postgresql+asyncpg://`.
- **Alembic migrations**: sempre escrever migration. Hot tables: pre-aplicar `ADD COLUMN IF NOT EXISTS` em Cloud SQL antes do deploy.
- **`CRITICAL_COLUMNS`**: deploy migration (N), verificar coluna, depois adicionar em `_critical_schema.py` em deploy (N+1).
- **WS Leader Election**: requer Redis. Se `ENABLE_GATE_WS=1` sem Redis, retry infinito.
- **Dev pipeline boot (Replit)**: precisa `Redis` (port 6379) + `Celery Worker` (`--queues=microstructure,structural,execution`) + `Celery Beat`. Pool sem `is_approved=true` é tratado como ciclo vazio (Task #231): `collect_all`/`collect_5m` logam `WARNING [COLLECT] no approved symbols — skipping cycle` e retornam 0 sem retry. Em prod isso é output do pipeline pool→watchlist→profile→L3, não ação manual.

### Vivos / regressões recentes (manter no radar)

- **Vocabulário canônico de `decisions_log.direction` (Task #292, 2026-05-12)**: o campo usa **uppercase** `'LONG' | 'SHORT' | 'NEUTRAL' | 'SPOT'`. Spot é long-only por natureza → ALLOW spot vira `'SPOT'`. Antes desta task: (a) `pipeline_scan._apply_robust_authoritative_scoring` só populava `futures_direction` no branch `if is_futures:` — watchlists SPOT gravavam NULL (109 ALLOW/24h NULL em prod); (b) `shadow_trade_service` filtrava `direction='up'` (vocabulário lowercase inexistente no resto do código). Resultado: gate Shadow Portfolio nunca disparou, painel ML vazio mesmo com pipeline saudável. Fix: setter SPOT em `pipeline_scan.py` (`else` do `if is_futures`), `asset["is_futures"]` propagado no início do loop para o fallback identificar o market mode corretamente, fallback defensivo em `_evaluate_l3_decisions` (NULL→`'SPOT'` se spot, `'NEUTRAL'` se futures), gate Shadow filtra **APENAS `direction == 'SPOT'`** em `shadow_trade_service._resolve_decision` (ORM) e `_promote_pending_decisions` (SQL raw). NÃO aceitar `'LONG'` aqui — Shadow é spot-only (sem leverage); habilitar futures requer helper separado com guard de market_mode. Migration `049_backfill_decisions_log_direction.py` faz UPDATE histórico (7d, ALLOW, NULL→'SPOT'). Lint test `backend/tests/test_direction_vocabulary_invariants.py` (4 invariantes: produtor canônico, consumidores sem lowercase, ML map keys, gate Shadow com filtro EFETIVO ORM+SQL). **NOT NULL constraint adiada** (rule N/N+1, esperar 1 semana de observação). Qualquer novo produtor de `decisions_log.direction` DEVE usar valor da allowlist `{LONG, SHORT, NEUTRAL, SPOT}` — adicionar valor novo exige atualizar `CANONICAL_DIRECTION_VALUES` no test E `DatasetBuilder.direction_map`.

- **Contrato `trade_simulations.features_snapshot` é flat (Task #290, 2026-05-12)**: `decisions_log.metrics["indicators_snapshot"]` é gravado por `indicators_provider.build_indicators_snapshot` no formato **aninhado** `{key: {"value": …, "source_group": …, "ts": …, "stale": …}}` (otimizado pra debugging "decision vs DB"). Mas `DatasetBuilder.extract_features` (`backend/app/ml/dataset_builder.py:152`) lê `features_snap.get(feat)` e chama `float(value)` esperando escalar. **TODOS os produtores** de `trade_simulations.features_snapshot` (e `shadow_trades.features_snapshot`) DEVEM achatar para `{key: scalar}`: (a) `ShadowTradeService._build_features_snapshot` (path Shadow Portfolio); (b) `simulation_service._flatten_indicators_for_ml` (path SimulationService.simulate_decision — antes gravava `decision.metrics` cru, corrigido nesta task). Sem o flatten, o ML quebra com `TypeError: float() argument must be a string or a real number, not 'dict'` ou (pior) silenciosamente coerce dicts pra NaN/0, contaminando o dataset histórico. **Qualquer produtor novo** de `trade_simulations` (ex.: shadow para futures, paper trading, backtest replay) precisa usar UM dos dois helpers — não copiar `indicators_snapshot` cru. Migration de campo `source` em `trade_simulations` já existia (`String(30)`, default `'SIMULATION'`); shadow grava `'SHADOW'` — sem migration nova nesta fase.


- **`ASYNC_MIGRATIONS=1` no `scalpyn` API (maio/2026, deploy fail recovery)**: depois de seis revisões consecutivas (`scalpyn-00450+`) caírem com "Startup probe timed out after 4m" — alembic + validate_critical_schema + uvicorn cold-import estourando os 240s do probe TCP — a API agora roda o gate de schema (`alembic upgrade head` + `validate_critical_schema`) em subshell de background quando `ASYNC_MIGRATIONS=1` (setado APENAS no service `scalpyn` em `cloudbuild.yaml`; workers/beat continuam síncronos). `start.sh` cria `/tmp/.migrations_done` ou `/tmp/.migrations_failed`; um watchdog secundário polla a cada 5s por até 15min e mata o container no `_failed` (Cloud Run rolla de volta — contrato de segurança preservado). `/api/health/schema` é o sinal público enquanto o gate roda. Mudanças complementares no mesmo deploy: `WEB_CONCURRENCY` 2→1 no `Dockerfile`, `ALEMBIC_TIMEOUT_PER_ATTEMPT` default 50→90s (override via env). **NÃO setar `ASYNC_MIGRATIONS=1` em workers/beat** — paraleliza só multiplica contenção de lock no Cloud SQL.

- **`acks_late=False` em tasks idempotentes beat-driven (Task #245)**: o global `task_acks_late=True` + `task_reject_on_worker_lost=True` (`celery_app.py:246-247`) faz o broker re-entregar QUALQUER task que estoure `time_limit` (SIGKILL) — fora do contador de `max_retries=3` (que só conta `task.retry()` explícito). Em maio/2026 isso gerou loop infinito: UPSERT em `market_metadata` contendido estourava `command_timeout=60s` → outer tx poisoned → `_inner` raise → reject_on_worker_lost requeue → repete forever. Backlog `structural=473`/`execution=1206`. Fix em `celery_app.py:179-209`: `_NO_REQUEUE_ON_WORKER_LOSS = {"acks_late": False}` aplicado a `collect_5m`, `collect_all`, `compute_5m`, `compute`, `compute_30m`, `score`, `pipeline_scan.scan`, `health_checks.check_structural_coverage` (todas idempotentes — beat re-roda em ≤60s/5min/30min). Tasks de execução (`evaluate_signals`, `execute_buy_cycle`) MANTÊM o global `acks_late=True` — perder uma decisão de compra sem retry é inaceitável. **Não desligar acks_late nessas duas.**

- **Dois budgets distintos no probe Celery (Task #246)**: `OperationalSnapshotService._refresh_celery` usa **dois** budgets independentes que NÃO devem ser unificados. `CELERY_INSPECT_TIMEOUT_S` (default 2s) é passado a cada `inspect(timeout=...)`; `CELERY_INSPECT_BUDGET_S` (default 8s) envolve o probe inteiro via `asyncio.wait_for`. Em maio/2026 os dois eram a MESMA constante (2s) — com Redis Labs externo em us-central1 + workers ocupados, as 4 chamadas sequenciais passavam de 2s no agregado → snapshot reportava `Workers: 0` mesmo com 5 workers vivos. Fix complementar: snapshot path chama APENAS `active()` + `registered()` (2 broadcasts em vez de 4); `reserved`/`scheduled` continuam no shape mas sempre 0 (backlog real coberto pelo `LLEN` em `_refresh_redis`). Não restaurar essas duas chamadas ao snapshot.

- **`command_timeout=180s` para sessions Celery (Task #245)**: API engine usa 60s (HTTP precisa falhar rápido); engine Celery (`database.py:240`) usa 180s porque `collect_market_data.collect_all/_5m` envolve o loop do universo inteiro em UMA outer transaction. SAVEPOINTs internos NÃO liberam row-locks — só o COMMIT do outer libera. Com 5 workers Cloud Run UPSERTando concorrentemente em `market_metadata`, contenção transiente regularmente passava de 60s. 180s dá folga sem ultrapassar `soft_time_limit=540s` do structural. Override via env `CELERY_DB_COMMAND_TIMEOUT`.

- **Pipeline recovery 2026-05-08/09 (4 incidentes correlacionados)**: `_MICRO_GUARDS` time_limit 180→480s + `idle_in_transaction_session_timeout=300s` no Celery engine + `scalpyn` API ingress `internal-and-cloud-load-balancing`→`all` + **`lock_timeout=30s` REVERTIDO em 2026-05-09** (regressão em <12h: hot symbols ETH_USDT/GT_USDT/FLOKI_USDT/NEXO_USDT falhando com `LockNotAvailableError` cronicamente, backlog execution=2446). **NÃO re-adicionar `lock_timeout` < 120s sem medir p95 lock-wait por símbolo em janela de 24h**. `command_timeout=180s` é o teto correto. Recovery manual (TX órfã): `SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='scalpyn' AND state LIKE 'idle in transaction%' AND NOW()-state_change > INTERVAL '1 minute';` (Cloud Shell, com `--database=scalpyn` explícito). Rolling restart dos workers usa `--max-instances=1` depois `=2` (Cloud Run rejeita `=0`). Detalhes em `backend/docs/runbooks/2026-05-08-pipeline-recovery.md`.

- **`MICRO_IDLE_IN_TX_TIMEOUT_S` — mitigação de `Lock: transactionid` no worker-micro (2026-05-11)**: vetor NOVO de deadlock 40P01 não coberto por #251/#273 (que cobrem row-level via `sorted()`). Evidência: `_collect_5m_async` faz `await fetch_ohlcv` + `await fetch_orderbook_metrics` por símbolo DENTRO do `run_db_task` → `session.begin()`. Para ~95 símbolos × ~600ms = ~60s com a outer tx aberta segurando XID_micro. Worker-structural enfileira em `Lock: transactionid`; dois structural concorrentes = ciclo determinístico. **Mitigação aplicada** em `backend/app/database.py:295-313`: detecção `K_SERVICE.startswith("scalpyn-worker-micro")` aplica `idle_in_transaction_session_timeout=90s` (override `MICRO_IDLE_IN_TX_TIMEOUT_S`, default 90). Outros services mantêm 300s (Task #256, override `CELERY_IDLE_IN_TX_TIMEOUT_S`). **Defesa, NÃO fix estrutural**: o fix correto é mover I/O da exchange para FORA da outer tx. Plan-file em `.local/tasks/refactor-collect-5m-io-out-of-tx.md`. **NÃO remover** a detecção micro-only sem antes mergear o refactor.

- **Ordenação determinística de símbolos antes de UPSERT — Tasks #251 + #273 (consolidado)**: deadlock 40P01 determinístico quando dois workers iteram o mesmo set de símbolos em ordens diferentes (SAVEPOINT por símbolo NÃO libera row-locks — só COMMIT do outer libera). Em 2026-05-09 isso gerou **140 deadlocks em 9min**. Em 2026-05-11 voltou nos paths não cobertos pela Task #251. Fix consolidado: `sorted()` em **todos** os callsites de iteração symbol-wise antes de UPSERT em `market_metadata`/`indicators`/`alpha_scores`/`pipeline_watchlist_assets`/`ohlcv` — `collect_market_data.py` (5 sites, ticker loops via `_bulk_upsert_market_metadata`), 3 schedulers, `compute_indicators` (1h/30m/5m), `compute_scores`, `pipeline_scan._upsert_assets`/`_replace_rejection_snapshot`, `ohlcv_backfill_service`. Lint test `backend/tests/test_pipeline_symbol_ordering_invariants.py` (substring-based, marca cada sort) impede regressão. **NÃO afrouxar os markers do lint** sem provar que a classe de contenção mudou. Bisect quando 40P01 retornar: `backend/docs/runbooks/postgres-deadlock-bisect.md`. Timeline 11/05: Apêndice C de `2026-05-08-pipeline-recovery.md`.

- **Cloud Build trigger / YAML escape / secrets / GitHub sync (2026-05-08)**: 5 lições do recovery em `backend/docs/runbooks/cloudbuild-trigger-history.md`. Resumo: SA do trigger é `330575088921-compute@`; shell vars em scripts inline precisam de `$$VAR`; `--update-secrets` é incremental (use `--remove-secrets`); `gcloud run services describe` NÃO aceita `--filter`; origin do Replit é gitsafe-backup.

### Estabilizadas (arquivadas em `backend/docs/runbooks/replit-md-archive.md`)

Ler o arquivo antes de mexer no subsistema correspondente. Cada item ainda é autoritativo.

- Celery sentinel queue `__no_default__` — Task #216
- `compute_indicators_robust` window=300s
- Nested-savepoint rollback rule — Task #222
- Scheduler concurrency ceiling (`BACKGROUND_SCHEDULER_CONCURRENCY` default 3)
- `pool_starved` ≠ `ingestion_stale` — Task #232
- No inline Celery/Redis probes em HTTP handlers — Task #225
- Cloud Run topology = 5 serviços — Task #239
- Cloud Run recovery script (`promote-cloud-run-topology.sh`) — Task #244
- Celery `--hostname` obrigatório no Cloud Run — Task #244
- `procps` obrigatório no backend Dockerfile

## Pointers

- **Robust Indicators Design**: `backend/docs/robust_indicators.md`
- **Grafana Monitoring Setup**: `docs/grafana/README.md`
- **Cloud SQL Database Pool Budget**: `docs/db-pool-budget.md`
- **Runbooks for Critical Schema Drift**: `backend/docs/runbooks/critical-schema-drift.md`
- **Gotchas Estabilizadas (arquivo histórico)**: `backend/docs/runbooks/replit-md-archive.md`
- **Cloud Build Trigger History (5 lessons, 2026-05-08)**: `backend/docs/runbooks/cloudbuild-trigger-history.md`
- **Pipeline Recovery 2026-05-08 (3 incidentes + Apêndices A/B/C)**: `backend/docs/runbooks/2026-05-08-pipeline-recovery.md` (Apêndice C = deadlocks `compute_*`/`pipeline_scan` 11/05, Task #273)
- **Postgres Deadlock Bisect (Task #273)**: `backend/docs/runbooks/postgres-deadlock-bisect.md`
- **Alembic Migration Guardrails**: Skill #7 (two-deploy rollout for `CRITICAL_COLUMNS`) and Skill #9 (pre-push schema audit).
- **JWT Secret Generation**: `openssl rand -hex 32`
- **AES Key Generation**: `openssl rand -hex 16`