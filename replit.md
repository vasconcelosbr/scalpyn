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

## Key Notes
- TimescaleDB hypertable warnings on startup are expected (Replit PostgreSQL lacks this extension). The app falls back to regular PostgreSQL tables.
- The DATABASE_URL validator in `backend/app/config.py` automatically converts `postgresql://` to `postgresql+asyncpg://` (required by asyncpg).
- CORS allows all `*.replit.app`, `*.replit.dev`, and `*.repl.co` domains.
