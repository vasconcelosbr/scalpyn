# Scalpyn - PRD (Product Requirements Document)

## Original Problem Statement
- Frontend Vercel: scalpyn.vercel.app
- Backend: https://scalpyn-330575088921.us-central1.run.app
- Initial Issue: "Failed: Not Found" ao criar pools
- Feature Request: Profile Engine implementation

## Architecture
- **Frontend**: Next.js 15 (Vercel)
- **Backend**: FastAPI (Google Cloud Run)
- **Database**: PostgreSQL/TimescaleDB

## User Personas
1. **Quant Trader**: Needs dynamic strategy configuration
2. **Platform Admin**: Manages system configurations

## Core Requirements (Static)
- Trading platform for crypto
- Strategy Pools management
- Dynamic Profile Engine for strategy definition
- Analytics and reports
- Exchange integrations

## What's Been Implemented (March 2026)

### Bug Fix - Pools Creation Error (Session 1)
- Fixed trailing slash causing 308 redirects
- Updated proxy to add trailing slash for collection routes
- Added `overrides` column migration

### Profile Engine Implementation (Session 2)
**Backend:**
- Profile model (PostgreSQL JSONB)
- WatchlistProfile junction table
- RuleEngine for condition evaluation
- ProfileEngine integrating Score/Signal engines
- Full CRUD API + testing endpoints

**Frontend:**
- ProfilesPage with grid view
- ProfileBuilder with tabbed interface
- ConditionBuilder for dynamic rules
- WeightSliders for Alpha Score weights

## Prioritized Backlog

### P0 (Critical)
- [x] Fix pools creation error
- [x] Implement Profile Engine backend
- [x] Implement Profile Builder UI

### P1 (High)
- [ ] Integrate profile filtering in Watchlist view
- [ ] Add profile assignment to pools
- [ ] Implement backtesting for profiles

### P2 (Medium)
- [ ] Profile import/export
- [ ] Profile performance analytics
- [ ] Automated profile suggestions

## Next Tasks
1. Test pools creation after deploy
2. Test profile creation and testing
3. Integrate profiles into Watchlist page
4. Add profile stats to dashboard

## Technical Decisions
- JSONB for profile config (flexibility)
- Generic RuleEngine (no hardcoded indicators)
- Backward compatible (no profile = default behavior)
