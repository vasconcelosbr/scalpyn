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
- [x] Add Market Type selector (Spot, Futures, TradFi) to pools
- [x] Add Strategy Profile selector to pools
- [x] Add Alpha Score Weights toggle to Profile Builder

### P1 (High)
- [x] Integrate profile filtering in Watchlist view (L1/L2/L3 tabs)
- [x] Add profile assignment to pools
- [ ] Implement backtesting for profiles
- [ ] Implement "Test Profile" endpoint (simulate without saving)

### P2 (Medium)
- [ ] Profile import/export
- [ ] Profile performance analytics
- [ ] Automated profile suggestions
- [ ] Redis caching for L2/L3 endpoints
- [ ] Add vercel.json for trailing slash configuration

## Next Tasks
1. Deploy changes to production (push to GitHub)
2. Test pool creation with Market Type and Profile selectors
3. Test Alpha Score Weights toggle in Profile Builder
4. Verify L1/L2/L3 profile assignment in Watchlist

## Technical Decisions
- JSONB for profile config (flexibility)
- Generic RuleEngine (no hardcoded indicators)
- Backward compatible (no profile = default behavior)
