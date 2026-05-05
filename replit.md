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
- **Recompute Rejected Tab Trace on Read**: The `Rejected` tab dynamically recomputes `evaluation_trace` on every read to immediately reflect backend semantic changes (e.g., new `SKIPPED` reasons, plausibility bounds) without waiting for scheduled snapshots.

## Product

- **Comprehensive Crypto Trading**: Provides tools for institutional-grade crypto trading.
- **Advanced Indicator Analysis**: Consolidates structural and microstructure indicators for informed decision-making.
- **Robust Scoring Engine**: Evaluates asset scores based on a sophisticated rule engine, providing confidence metrics and trade signals.
- **Watchlist Management**: Allows users to create and manage watchlists with detailed asset evaluation traces.
- **Real-time Data Feeds**: Integrates real-time order flow data from exchanges via WebSockets.
- **Trade History & P&L Tracking**: Imports and analyzes closed spot orders, calculating profit and loss.
- **Operational Monitoring**: Integrates with Grafana for real-time monitoring of system health, data quality, and alerts.
- **Native Performance Dashboard** (`/dashboard/performance`, Task #224): seven panels (health, system status, ingest rate, decisions, trades, sim-vs-real, ML dataset) backed by `GET /api/dashboard/*` read-only aggregations. Native alternative to the Grafana iframe — the legacy MonitoringTab is retained for backward compatibility.

## User preferences

- **Sempre publicar (deploy) ao final de cada tarefa.** Após `mark_task_complete`, chamar `suggest_deploy` para que a versão em produção (ex.: `scalpyn.vercel.app` no front + backend hospedado) reflita o código mais recente. Sem isso, o usuário continua vendo 404 em rotas novas.
- **Antes do checkpoint final, rodar `cd frontend && npx tsc --noEmit -p .`** para validar o build de produção. A Vercel falha com qualquer type error que o `next dev` não pega (ex.: `Tooltip` do recharts tipa `formatter`'s `value` como `ValueType | undefined`, não aceita anotação `(v: number) => …`). Corrigir antes do push evita ciclo de deploys vermelhos.
- Comunicação em PT-BR.

## Gotchas

- **TimescaleDB warnings**: Expected on Replit as the extension is not available. The application falls back to standard PostgreSQL tables.
- **`DATABASE_URL` format**: The validator automatically converts `postgresql://` to `postgresql+asyncpg://`.
- **Alembic migrations**: Always write a migration for schema changes. For hot tables, pre-apply DDL manually (e.g., `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`) in Cloud SQL before deployment to avoid lock contention and startup failures.
- **`CRITICAL_COLUMNS`**: When adding new columns, deploy the migration first (N), verify column existence, then add the column to `_critical_schema.py` in a separate deploy (N+1).
- **WS Leader Election**: Ensure Redis is accessible for the Gate.io WebSocket leader election to function correctly. If Redis is down and `ENABLE_GATE_WS=1`, the system will retry indefinitely to connect.
- **Celery sentinel queue (`__no_default__`)**: Declared in `task_queues` so kombu's `_create_task_sender` can resolve `task_default_queue` (Celery ≥ 5.6 raises `KeyError` on every send_task / beat tick otherwise). No worker consumes it, so any task escaping `TASK_ROUTES` still piles up visibly there — preserving the loud-failure intent of invariant #4 from Task #216. Do **not** remove it.
- **Dev pipeline boot (Replit)**: Celery requires three workflows — `Redis` (port 6379), `Celery Worker` (`--queues=microstructure,structural,execution`), and `Celery Beat`. After fresh DB setup, manually approve representative symbols in dev (`UPDATE pool_coins SET is_approved=true WHERE symbol IN (...)`) — `collect_market_data.collect_all` raises `[FATAL] No approved symbols` otherwise (invariant #5 from Task #216).
- **`compute_indicators_robust` window**: The `window_seconds` for order flow data is standardized at 300s. Inconsistencies can lead to `VALID` vs `NO_DATA` flapping.
- **Nested-savepoint rollback rule**: Never call `await db.rollback()` inside a loop that uses `async with db.begin_nested()` — the SAVEPOINT is already rolled back by the context manager on exception. The extra `db.rollback()` closes the OUTER transaction opened by `run_db_task` (`async with session.begin()`) and poisons every subsequent iteration with `Can't operate on closed transaction inside context manager` (Task #222).

## Pointers

- **Robust Indicators Design**: `backend/docs/robust_indicators.md`
- **Grafana Monitoring Setup**: `docs/grafana/README.md`
- **Cloud SQL Database Pool Budget**: `docs/db-pool-budget.md`
- **Runbooks for Critical Schema Drift**: `backend/docs/runbooks/critical-schema-drift.md`
- **Alembic Migration Guardrails**: Skill #7 (two-deploy rollout for `CRITICAL_COLUMNS`) and Skill #9 (pre-push schema audit).
- **JWT Secret Generation**: `openssl rand -hex 32`
- **AES Key Generation**: `openssl rand -hex 16`