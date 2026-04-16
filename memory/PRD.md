# Scalpyn - PRD

## Product Summary
Scalpyn is an institutional-grade crypto trading platform with a 3-level pipeline watchlist system (L1 → L2 → L3) that filters and scores crypto assets using configurable strategy profiles.

## Tech Stack
- **Frontend**: Next.js 14/15, React, Tailwind CSS, Shadcn UI (port 3000)
- **Backend**: FastAPI (port 8001), Celery + Redis (background tasks), WebSockets
- **Database**: PostgreSQL (SQLAlchemy async ORM, asyncpg)
- **External APIs**: Gate.io (market data, order execution)
- **Deployment**: Cloud Run (backend), Vercel (frontend)

## Core Architecture
```
/app/
├── backend/
│   ├── app/
│   │   ├── api/            # FastAPI routes
│   │   │   ├── watchlists.py         # Pipeline watchlists (L1/L2/L3) — ACTIVE
│   │   │   ├── pipeline_watchlists.py # INACTIVE (not registered in main.py)
│   │   │   ├── pools.py              # Pool management
│   │   │   ├── market.py             # Market data endpoints
│   │   │   └── pipeline_watchlists.py # Debug endpoint moved to watchlists.py
│   │   ├── models/         # SQLAlchemy ORM models
│   │   ├── services/       # score_engine, profile_engine, market_data_service
│   │   ├── tasks/          # Celery: collect_market_data.py, pipeline_scan.py
│   │   └── utils/          # symbol_filters.py
├── frontend/
│   ├── app/                # Next.js App Router
│   │   ├── watchlist/page.tsx        # Pipeline watchlist page
│   │   └── pools/[id]/page.tsx       # Pool management
│   └── components/
│       └── watchlist/PipelineAssetTable.tsx  # Score breakdown table
```

## Key Models
- **Pool** / **PoolCoin**: `BTC_USDT` format (Gate.io with underscore) — CRITICAL
- **PipelineWatchlist**: {level, source_pool_id, source_watchlist_id, profile_id, auto_refresh}
- **PipelineWatchlistAsset**: {symbol, alpha_score, level_direction ('up'|'down'|null)}
- **market_metadata**: {symbol in `BTC_USDT` format, price, price_change_24h, volume_24h, market_cap}
- **Trade**: {market_type (not 'profile'!), holding_seconds, ...}

## Symbol Format Convention — CRITICAL
**ALL symbols MUST be in `BTC_USDT` format (with underscore).**
- market_metadata stores: `BTC_USDT`
- pool_coins must store: `BTC_USDT`
- Normalization function: if `"_" not in s and s.endswith("USDT"): return s[:-4] + "_USDT"`
- Normalization is applied in: pools.py (add_pool_coin), watchlists.py (_get_base_symbols), pipeline_scan.py, collect_market_data.py

## What's Been Implemented

### Phase 1 — MVP Pipeline (Completed)
- L1/L2/L3 pipeline watchlist system with Celery background scanning
- Score Engine with configurable scoring rules
- Profile Engine with configurable filter conditions
- Market data collection from Gate.io (all tickers, OHLCV, orderbook)

### Phase 2 — UI Improvements (Completed)
- Score Breakdown Table (PipelineAssetTable.tsx) with RSI, Vol, Taker, ADX, MACD, EMA scores
- Accordion drilldown for indicator details
- Status colors: 🟢 🟡 🔴 per metric
- Centralized Strategy Profile assignment in Watchlist UI

### Phase 3 — Bug Fixes (Completed)
- Fixed Celery event loop crash: CeleryAsyncSessionLocal with NullPool
- Fixed Trade.market_type rename (was Trade.profile) across 8 files
- Fixed ScoreEngine config key mismatch ("rules" vs "scoring_rules")
- Fixed Market Cap strict filtering in pipeline_scan.py

### Phase 4 — L1 POOLGATE Fix (Completed — 2026-04-16)
**Root Causes Fixed:**
1. Symbol format mismatch: `market.py` returned `BTCUSDT` (no underscore) — FIXED to return `BTC_USDT`
2. `_get_base_symbols()` didn't normalize pool coins — FIXED to apply `_normalize_sym()`
3. `_seed_market_metadata_bg()` didn't normalize symbols — FIXED
4. `pools.py add_pool_coin()` didn't normalize — FIXED
5. `pools.py scan_and_populate_pool()` used `get_market_metadata()` returning `BTCUSDT` — FIXED
6. `collect_market_data.py` had 500-symbol cap — REMOVED
7. `_resolve_and_persist()` applied strict meta filters even when no market data → wiped watchlist — FIXED (skip meta conditions when meta_map is empty)
8. `profile_config_for_score` NameError in pipeline_watchlists.py — FIXED
9. Frontend port mismatch: package.json had `next start -p 5000` but K8s routes to 3000 — FIXED to port 3000
10. New **debug endpoint** `GET /api/watchlists/{watchlist_id}/debug` — IMPLEMENTED

## Key API Endpoints
- `GET /api/watchlists/` — List all pipeline watchlists
- `POST /api/watchlists/` — Create pipeline watchlist
- `GET /api/watchlists/{id}/assets` — Get assets with scores
- `POST /api/watchlists/{id}/refresh` — Manual pipeline refresh
- `GET /api/watchlists/{id}/debug` — **NEW** Pipeline observability report
- `POST /api/pools/{id}/coins` — Add coin to pool (normalizes to BTC_USDT)
- `GET /api/market/spot-currencies` — Market data (returns BTC_USDT format)
- `POST /api/pipeline/{wl_id}/refresh` — (via pipeline_watchlists.py — INACTIVE)

## Critical Notes for Future Agents
1. **`pipeline_watchlists.py` is NOT registered in main.py** — only `watchlists.py` is active
2. **DB is PostgreSQL**, NOT MongoDB. Do NOT use PyObjectId or Mongo methods.
3. **Celery tasks** MUST use `CeleryAsyncSessionLocal` (NullPool), not `AsyncSessionLocal`
4. **Trade model**: use `Trade.market_type`, NOT `Trade.profile`
5. **Symbol format**: ALWAYS `BTC_USDT` with underscore, never `BTCUSDT`

## Backlog / Upcoming Tasks
### P1
- Frontend button to trigger `GET /api/watchlists/{id}/debug` and show results in UI
- Better pipeline observability: log why each asset was dropped (per condition)

### P2
- Celery task health monitoring (show last run time in UI)
- L2/L3 watchlist testing with real profile filters
- Score breakdown UI verification with live Gate.io data

### P3
- TimescaleDB extension for time-series optimization (currently using plain PostgreSQL)
- Performance optimization for large pools (>500 coins)
