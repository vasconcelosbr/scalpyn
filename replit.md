# Scalpyn — Institutional-Grade Crypto Trading Platform

## Architecture
- **Frontend**: Next.js 16 (App Router) + TypeScript + TailwindCSS + shadcn/ui — runs on port 5000
- **Backend**: FastAPI (Python 3.12) + SQLAlchemy 2.0 + Alembic — runs on port 8000
- **DB**: PostgreSQL (Replit managed) — TimescaleDB extension not available on Replit (handled gracefully)
- **Tasks**: Celery + Redis (Redis defaults to localhost:6379)
- **Exchange**: Gate.io API v4

## Project Structure
```
frontend/     — Next.js app (App Router, port 5000)
backend/      — FastAPI app (port 8000)
  app/
    main.py   — FastAPI app factory, CORS, router mounting
    config.py — Settings (DATABASE_URL auto-converted to asyncpg)
    api/      — Route handlers
    models/   — SQLAlchemy ORM models
    schemas/  — Pydantic schemas
    services/ — Business logic
    engines/  — Trading engines
    tasks/    — Celery tasks
docs/         — Architecture docs and specs
```

## Workflows
- **Start application** — `cd frontend && npm run dev` (port 5000, webview)
- **Backend API** — `cd backend && uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload` (port 8000, console)

## Environment Variables
Required secrets (set in Replit Secrets):
- `DATABASE_URL` — PostgreSQL connection (auto-converted from postgresql:// to postgresql+asyncpg://)
- `JWT_SECRET` — JWT signing key (generate: openssl rand -hex 32)
- `ENCRYPTION_KEY` — AES key for encrypting API credentials (generate: openssl rand -hex 16)
- `REDIS_URL` — Redis connection (optional, defaults to redis://localhost:6379/0)
- `BACKEND_URL` — Backend URL for frontend proxy (defaults to http://localhost:8000)

## API Proxy
The frontend proxies all `/api/*` requests to the FastAPI backend via `frontend/app/api/[...path]/route.ts`. This keeps the backend URL server-side only.

## Trade Sync (Exchange Import)
- `POST /api/trades/sync?days=90` — imports closed spot orders from Gate.io into the trades table
- Uses FIFO matching to pair buy/sell orders per symbol and calculate P&L
- Deduplicates via `trades.exchange_order_id` (unique index, nullable)
- `trades.source` column: `"scalpyn"` (engine-initiated) vs `"exchange_import"` (synced)
- Frontend: "Import from Gate" button on the Dashboard page triggers the sync

## Key Notes
- TimescaleDB hypertable warnings on startup are expected (Replit PostgreSQL lacks this extension). The app falls back to regular PostgreSQL tables.
- The DATABASE_URL validator in `backend/app/config.py` automatically converts `postgresql://` to `postgresql+asyncpg://` (required by asyncpg).
- CORS allows all `*.replit.app`, `*.replit.dev`, and `*.repl.co` domains.

## Schema Bootstrap (Production)
- **Single source of truth**: Alembic migrations in `backend/alembic/versions/`. New schema changes MUST land as a migration, never only in `init_db.py`.
- **Cloud Run boot order** (`backend/start.sh`): `alembic upgrade head` is the ONLY schema gate. Three retries with backoff, time-boxed at 180s per attempt. `exit 1` on persistent failure causes Cloud Run to roll back to the previous revision automatically. Then Celery + uvicorn start.
- **Lock contention defense** (`backend/alembic/env.py`): every migration runs with `SET lock_timeout = '10s'` and `SET statement_timeout = '60s'`. During deploy the previous revision is still serving (Celery beat holds shared locks); without these timeouts an `ALTER TABLE` would block forever and blow past the ~240s Cloud Run startup probe window — that's exactly what broke the Task #44 deploy.
- **`init_db.py`** is a dev-only convenience for fresh local DBs. It runs from the FastAPI lifespan when `SKIP_LIFESPAN_INIT_DB` is unset; production exports `SKIP_LIFESPAN_INIT_DB=1` so it never touches the prod DB. Migration `021_init_db_parity_catchall.py` mirrors its DDL 1:1.
- **Health probe**: `GET /api/health/schema` queries `information_schema.columns` for the critical column list and returns 503 with `{ missing: [...] }` if any are absent. Use this — not `/api/health` — to verify a deploy succeeded. Canonical post-deploy check before testing the UI.
- **Adding new columns**: write an Alembic migration AND bump the critical column list in `backend/app/main.py::health_check_schema` so production drift is detected proactively.
