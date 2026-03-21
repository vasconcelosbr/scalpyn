# Scalpyn — Institutional-Grade Crypto Trading Platform

## Core Principles
1. **ZERO HARDCODE** — Every threshold, %, margin → config in DB (JSONB in config_profiles) → GUI-editable
2. **Score Drives Everything** — No trade without score >= threshold
3. **Spot: NEVER sell at loss** — Underwater positions stay in HOLDING indefinitely
4. **Futures: Anti-Liquidation 3 layers** — Stop ALWAYS executes before liquidation
5. **Leverage is CALCULATED** — Consequence of risk sizing + stop distance, never arbitrary
6. **Each position is independent** — Immutable entry_price, individual P&L

## Architecture
- Two profiles: SPOT (score-driven, no grids, never sell at loss) and FUTURES (5-Layer Institutional Scoring, anti-liquidation)
- Score-driven opportunistic: scanner ranks ~100 coins, buys if score >= threshold AND USDT balance available
- No grids. Each buy is an independent position regardless of coin or existing positions.

## Stack
- Frontend: Next.js 14 (App Router) + TypeScript + TailwindCSS + shadcn/ui
- Backend: FastAPI (Python 3.11+, async) + SQLAlchemy 2.0 + Alembic
- DB: PostgreSQL 16 + TimescaleDB (time series) + Redis (cache, pub/sub)
- Tasks: Celery + Redis
- Exchange: Gate.io API v4 (settle: usdt)
- Design: Dark luxury fintech, Plus Jakarta Sans + JetBrains Mono

## Key Areas
- Trading Desk: /trading-desk/spot and /trading-desk/futures (separate sub-sections)
- Spot: scanner → buy → 5 sell layers (never at loss) → holding underwater → optional DCA
- Futures: 4 gates → 5-layer scoring → position sizing → leverage calc → anti-liq → TP1/TP2/TP3 → trailing ATR

## Docs
Read docs/ folder for complete architecture specs, Gate.io API mapping, and implementation roadmap.
