# Pool Creation Issue - Full Stack Trace

## Frontend Flow (Next.js)
1. **User Action**: Click "Create Pool" button in `/frontend/app/pools/page.tsx`
2. **Handler**: `handleCreate()` calls `apiPost("/pools/", {...})`
3. **API Call**: `/frontend/lib/api.ts` `apiPost()` 
   - Uses relative URL: `/api/pools/`
   - Sends to `fetch("/api/pools/", {...})`
4. **Next.js Route Handler**: `/frontend/app/api/[...path]/route.ts`
   - Intercepts request to `/api/pools/`
   - BACKEND_URL env var: `http://localhost:8000/api`
   - Constructs target: `http://localhost:8000/api/pools/`
   - Forwards POST request with auth header

## Backend Flow (FastAPI)
1. **Backend URL**: `http://localhost:8000`
2. **FastAPI App**: `/backend/app/main.py` includes pools router
   - Router prefix: `/api/pools`
   - Full endpoint: `http://localhost:8000/api/pools`
3. **Endpoint**: `@router.post("/")` in `/backend/app/api/pools.py:46`
   - Expects: user_id from JWT token (via get_current_user_id)
   - Expects: payload with name, description, mode, is_active
   - Creates Pool in database

## Potential Issues to Check:
1. JWT Token validation - get_current_user_id dependency
2. Request payload format mismatch
3. Database connection or Pool model issue
4. Trailing slash handling
5. CORS configuration
6. HTTP status code mismatch
