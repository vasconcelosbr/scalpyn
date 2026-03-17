# Scalpyn - PRD

## Original Problem Statement
- Frontend Vercel: scalpyn.vercel.app
- Backend: https://scalpyn-330575088921.us-central1.run.app
- Erro: "Failed: Not Found" ao criar novos pools

## Architecture
- **Frontend**: Next.js 15 (Vercel)
- **Backend**: FastAPI (Google Cloud Run)
- **Database**: PostgreSQL/TimescaleDB

## What's Been Implemented (March 2026)

### Bug Fix - Pools Creation Error
- Fixed trailing slash issue causing 308 redirects
- Updated proxy to add trailing slash for collection routes
- Updated fallback BACKEND_URL

## Core Requirements
- Trading platform for crypto
- Strategy Pools management
- Analytics and reports
- Exchange integrations

## Prioritized Backlog
- P0: Core pools functionality (DONE)
- P1: Verify all other API endpoints work correctly
- P2: Add error handling for edge cases

## Next Tasks
- User testing of pools creation
- Verify exchange connection works
- Test analytics endpoints
