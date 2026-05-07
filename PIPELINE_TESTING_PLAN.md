# Pipeline Reliability Testing & Validation Plan

## Overview

This document provides step-by-step validation procedures to verify the end-to-end pipeline:

**L3 → decision_logs → simulation → trade_simulations → ML**

## Prerequisites

- Backend running with all migrations applied
- Celery workers running
- Redis running
- PostgreSQL with TimescaleDB
- At least one active user with a configured L3 watchlist

---

## Test Plan

### Phase 1: Verify Decision Persistence

**Objective:** Ensure L3 approvals are correctly persisted to `decision_logs`

#### Step 1.1: Check Initial State

```sql
-- Count existing decisions
SELECT COUNT(*) as decision_count FROM decisions_log;

-- Check most recent decision
SELECT id, symbol, decision, created_at, direction
FROM decisions_log
ORDER BY created_at DESC
LIMIT 5;
```

Record the count and latest timestamp.

#### Step 1.2: Trigger L3 Approval

**Option A: Via Pipeline Scan (Recommended)**

Wait for the next pipeline scan cycle (runs every 5 minutes), OR manually trigger:

```bash
# From backend directory
celery -A app.tasks.celery_app call app.tasks.pipeline_scan.scan
```

**Option B: Via Manual L3 Asset Addition**

1. Go to frontend watchlist UI
2. Add a symbol to your L3 watchlist
3. Ensure the symbol passes all gates (check logs)

#### Step 1.3: Verify Decision Logged

Wait 30 seconds, then check:

```sql
-- Verify decision_logs increased
SELECT COUNT(*) as decision_count FROM decisions_log;

-- Check latest decisions
SELECT id, symbol, strategy, decision, l3_pass, direction, created_at
FROM decisions_log
ORDER BY created_at DESC
LIMIT 10;
```

**Expected Results:**
- Count increased by at least 1
- New decision(s) with `l3_pass = TRUE` and `decision = 'ALLOW'`
- `created_at` timestamp within last few minutes

**Check Logs:**
```bash
# Backend logs should show:
grep "Decision.*PERSISTED" logs/backend.log
```

Expected log format:
```
[Decision] PERSISTED | id=12345 | BTC_USDT | score=0.85 | ALLOW | event=L3_APPROVAL
[Decision] Batch persisted: 3 decision(s) successfully logged to decisions_log table
```

#### Step 1.4: Verify Deduplication

Trigger another scan immediately:

```bash
celery -A app.tasks.celery_app call app.tasks.pipeline_scan.scan
```

**Check Logs:**
```bash
grep "SKIP duplicate" logs/backend.log
```

Expected output:
```
[Decision] Deduplication: skipped 3 duplicate(s), inserting 0 new decision(s)
```

**Verify in DB:**
```sql
-- Should see no new duplicate decisions for same symbol/strategy/direction in last 5 min
SELECT symbol, strategy, direction, COUNT(*) as cnt
FROM decisions_log
WHERE created_at >= NOW() - INTERVAL '5 minutes'
GROUP BY symbol, strategy, direction
HAVING COUNT(*) > 1;
```

Should return 0 rows.

---

### Phase 2: Verify Simulation Execution

**Objective:** Ensure simulations run for new decisions

#### Step 2.1: Check Simulation Status (Before)

```bash
# Check via API
curl http://localhost:8000/api/system/pipeline-status | jq .
```

Record:
- `pipeline.simulations.total`
- `pipeline.simulations.last_time`
- `pipeline.coverage.percentage`

OR via SQL:

```sql
SELECT
    COUNT(*) as total_simulations,
    MAX(created_at) as last_simulation
FROM trade_simulations;
```

#### Step 2.2: Trigger Simulation Batch

**Option A: Wait for Scheduled Run (10 minutes)**

The simulation batch runs automatically every 10 minutes via Celery Beat.

**Option B: Manual Trigger**

```bash
# Trigger simulation batch manually
celery -A app.tasks.celery_app call app.tasks.simulation.run_simulation_batch \
  --kwargs '{"limit": 200, "skip_existing": true}'
```

OR via API:

```bash
curl -X POST "http://localhost:8000/api/simulations/run?limit=100&skip_existing=true" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

#### Step 2.3: Monitor Simulation Logs

```bash
# Watch simulation progress
tail -f logs/backend.log | grep "\[Simulation\]"
```

Expected log sequence:
```
[Simulation] OHLCV validation PASSED: 95 symbols, 2400 candles, latest=2026-04-30...
[Simulation] Processing 50 decisions for simulation
[Simulation] Progress: 10/50 decisions processed | simulated=15 | skipped=0
[Simulation] Progress: 20/50 decisions processed | simulated=32 | skipped=0
...
[Simulation] Bulk insert complete: 85 records
[Simulation] Batch complete | decisions=50 | processed=50 | simulated=85 | skipped_existing=0 | skipped_no_data=0 | errors=0
```

#### Step 2.4: Verify Simulations Created

```sql
-- Check simulation count increased
SELECT COUNT(*) as total_simulations FROM trade_simulations;

-- Check recent simulations
SELECT
    id,
    symbol,
    direction,
    result,
    entry_price,
    exit_price,
    decision_id,
    created_at
FROM trade_simulations
ORDER BY created_at DESC
LIMIT 10;

-- Verify linkage to decisions
SELECT
    ts.id,
    ts.symbol,
    ts.result,
    ts.direction,
    dl.decision,
    dl.l3_pass
FROM trade_simulations ts
JOIN decisions_log dl ON ts.decision_id = dl.id
ORDER BY ts.created_at DESC
LIMIT 10;
```

**Expected Results:**
- Simulation count increased
- New simulations with `decision_id` matching decisions from Phase 1
- Various `result` values (WIN/LOSS/TIMEOUT)
- `created_at` within last few minutes

#### Step 2.5: Check for Errors

```bash
# Check for simulation errors
grep "Simulation.*ERROR" logs/backend.log

# Check for OHLCV validation failures
grep "OHLCV validation FAILED" logs/backend.log

# Check for high skip rates
grep "HIGH SKIP RATE" logs/backend.log
```

Should see minimal or no errors. If errors found, investigate root cause.

---

### Phase 3: Verify Pipeline Status Endpoint

**Objective:** Ensure the new status endpoint provides accurate metrics

#### Step 3.1: Call Pipeline Status

```bash
curl http://localhost:8000/api/system/pipeline-status | jq .
```

#### Step 3.2: Verify Response Structure

Expected response:

```json
{
  "status": "healthy",
  "timestamp": "2026-04-30T19:30:00.000Z",
  "pipeline": {
    "decisions": {
      "total": 1250,
      "allow": 890,
      "block": 360,
      "last_time": "2026-04-30T19:25:00.000Z",
      "lag_minutes": 5,
      "last_hour": 45
    },
    "simulations": {
      "total": 2340,
      "unique_decisions": 1100,
      "last_time": "2026-04-30T19:20:00.000Z",
      "lag_minutes": 10,
      "last_hour": 85,
      "wins": 1250,
      "losses": 890,
      "timeouts": 200
    },
    "coverage": {
      "percentage": 88.0,
      "simulated": 1100,
      "total_decisions": 1250
    }
  },
  "errors": [],
  "warnings": [],
  "health_summary": {
    "pipeline_operational": true,
    "decisions_flowing": true,
    "simulations_running": true,
    "data_quality": "good"
  }
}
```

#### Step 3.3: Validate Metrics

Cross-check with direct SQL queries:

```sql
-- Validate decision counts
SELECT
    COUNT(*) as total,
    COUNT(*) FILTER (WHERE decision = 'ALLOW') as allow_count,
    COUNT(*) FILTER (WHERE decision = 'BLOCK') as block_count
FROM decisions_log;

-- Validate simulation counts
SELECT
    COUNT(*) as total,
    COUNT(DISTINCT decision_id) as unique_decisions
FROM trade_simulations;

-- Calculate coverage manually
SELECT
    ROUND(
        (COUNT(DISTINCT ts.decision_id)::numeric / COUNT(DISTINCT dl.id)) * 100,
        2
    ) as coverage_pct
FROM decisions_log dl
LEFT JOIN trade_simulations ts ON dl.id = ts.decision_id;
```

Numbers should match the API response within ±5%.

---

### Phase 4: End-to-End Integration Test

**Objective:** Full pipeline flow from L3 approval to simulation

#### Step 4.1: Clean Slate Test

1. Pick a symbol NOT currently in L3 watchlist (e.g., `ATOM_USDT`)
2. Ensure symbol has recent OHLCV data:

```sql
SELECT COUNT(*) as candle_count
FROM ohlcv
WHERE symbol = 'ATOM_USDT'
  AND timeframe = '1h'
  AND time >= NOW() - INTERVAL '24 hours';
```

Should have at least 20 candles.

#### Step 4.2: Add Symbol to L3

Via frontend or API, add the symbol to L3 watchlist.

#### Step 4.3: Wait for Pipeline Cycle (5 minutes)

Monitor logs:

```bash
tail -f logs/backend.log | grep -E "(ATOM_USDT|Decision|Simulation)"
```

#### Step 4.4: Verify Full Flow

After 15-20 minutes (1 pipeline scan + 1 simulation batch):

```sql
-- Check decision logged
SELECT id, symbol, decision, l3_pass, created_at
FROM decisions_log
WHERE symbol = 'ATOM_USDT'
ORDER BY created_at DESC
LIMIT 1;

-- Check simulation created
SELECT
    ts.id,
    ts.symbol,
    ts.result,
    ts.direction,
    ts.created_at,
    dl.id as decision_id
FROM trade_simulations ts
JOIN decisions_log dl ON ts.decision_id = dl.id
WHERE ts.symbol = 'ATOM_USDT'
ORDER BY ts.created_at DESC
LIMIT 3;
```

**Expected Results:**
- One decision_log entry for ATOM_USDT with ALLOW
- 1-3 simulation entries (LONG/SHORT or SPOT depending on profile)
- Simulations linked to decision via `decision_id`

---

## Error Scenarios & Troubleshooting

### Scenario 1: No Decisions Being Logged

**Symptoms:**
- `decisions_log` table empty or not updating
- No "[Decision] PERSISTED" logs

**Checks:**
1. Is L3 watchlist active?
   ```sql
   SELECT * FROM watchlists WHERE level = 'L3' AND is_active = TRUE;
   ```

2. Are there approved assets in L3?
   ```sql
   SELECT * FROM pipeline_watchlist_assets WHERE watchlist_id IN (
       SELECT id FROM watchlists WHERE level = 'L3'
   ) AND level_direction = 'up';
   ```

3. Check pipeline scan logs:
   ```bash
   grep "pipeline.*scan" logs/backend.log | tail -20
   ```

4. Check for exceptions in decision persistence:
   ```bash
   grep "FATAL.*Decision persistence" logs/backend.log
   ```

### Scenario 2: Simulations Not Running

**Symptoms:**
- `trade_simulations` table empty or stale
- Simulation lag > 30 minutes

**Checks:**
1. OHLCV data available?
   ```sql
   SELECT COUNT(*) FROM ohlcv
   WHERE timeframe = '1h'
     AND time >= NOW() - INTERVAL '24 hours';
   ```
   Should have > 100 candles.

2. Check simulation task logs:
   ```bash
   grep "Simulation" logs/backend.log | tail -50
   ```

3. Check Celery worker status:
   ```bash
   celery -A app.tasks.celery_app inspect active
   ```

4. Check beat schedule:
   ```bash
   celery -A app.tasks.celery_app inspect scheduled
   ```

### Scenario 3: High Skip Rate

**Symptoms:**
- Log shows "HIGH SKIP RATE" warning
- Many simulations skipped due to "no_candles"

**Checks:**
1. Check OHLCV coverage for symbols in decisions:
   ```sql
   SELECT
       dl.symbol,
       COUNT(DISTINCT o.time) as candle_count
   FROM decisions_log dl
   LEFT JOIN ohlcv o ON dl.symbol = o.symbol
       AND o.timeframe = '1h'
       AND o.time >= dl.created_at
   WHERE dl.created_at >= NOW() - INTERVAL '1 hour'
   GROUP BY dl.symbol
   ORDER BY candle_count ASC;
   ```

2. Symbols with low candle counts need OHLCV backfill
3. Check market data collection:
   ```bash
   grep "collect_market_data" logs/backend.log | tail -20
   ```

### Scenario 4: Pipeline Status Shows Errors

**Symptoms:**
- `/api/system/pipeline-status` returns `status: "error"`
- Error flags in response

**Actions:**
1. Review specific errors in response
2. Common errors:
   - `NO_DECISIONS`: Check L3 watchlist setup
   - `NO_SIMULATIONS`: Check OHLCV data and Celery tasks
   - `LOW_COVERAGE`: Normal for first few hours, should improve over time

---

## Success Criteria

The pipeline is considered **healthy** when:

1. ✅ Decisions are logged within 5 minutes of L3 approval
2. ✅ No duplicate decisions in recent window (5 min)
3. ✅ Simulations run within 10-15 minutes of decision
4. ✅ Coverage > 80% after 24 hours
5. ✅ Skip rate < 20%
6. ✅ No "FATAL" errors in logs
7. ✅ Pipeline status endpoint returns `"status": "healthy"`

---

## Monitoring & Alerts

### Key Metrics to Monitor

1. **Decision Log Rate**
   ```sql
   SELECT COUNT(*) FROM decisions_log
   WHERE created_at >= NOW() - INTERVAL '1 hour';
   ```
   Expected: 10-100/hour depending on market conditions

2. **Simulation Coverage**
   ```sql
   SELECT
       COUNT(DISTINCT decision_id)::float / NULLIF(COUNT(DISTINCT dl.id), 0) * 100 as coverage
   FROM decisions_log dl
   LEFT JOIN trade_simulations ts ON dl.id = ts.decision_id
   WHERE dl.created_at >= NOW() - INTERVAL '24 hours';
   ```
   Expected: > 80%

3. **Simulation Lag**
   ```sql
   SELECT
       AVG(EXTRACT(EPOCH FROM (ts.created_at - dl.created_at)) / 60) as avg_lag_minutes
   FROM trade_simulations ts
   JOIN decisions_log dl ON ts.decision_id = dl.id
   WHERE ts.created_at >= NOW() - INTERVAL '1 hour';
   ```
   Expected: < 15 minutes

### Recommended Alerts

Set up alerts for:
- ⚠️ Decision lag > 15 minutes
- ⚠️ Simulation lag > 30 minutes
- 🚨 No decisions logged in 1 hour
- 🚨 No simulations created in 1 hour
- ⚠️ Coverage < 70% after 24 hours
- 🚨 "FATAL" in logs
- ⚠️ Skip rate > 30%

---

## Appendix: Quick Diagnostic Queries

### Pipeline Health Check (One Query)

```sql
WITH decision_stats AS (
    SELECT
        COUNT(*) as total_decisions,
        COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '1 hour') as recent_decisions,
        MAX(created_at) as last_decision
    FROM decisions_log
),
simulation_stats AS (
    SELECT
        COUNT(*) as total_simulations,
        COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '1 hour') as recent_simulations,
        MAX(created_at) as last_simulation,
        COUNT(DISTINCT decision_id) as decisions_with_sims
    FROM trade_simulations
)
SELECT
    d.total_decisions,
    d.recent_decisions,
    d.last_decision,
    s.total_simulations,
    s.recent_simulations,
    s.last_simulation,
    ROUND((s.decisions_with_sims::numeric / NULLIF(d.total_decisions, 0)) * 100, 2) as coverage_pct,
    EXTRACT(EPOCH FROM (NOW() - d.last_decision)) / 60 as decision_lag_min,
    EXTRACT(EPOCH FROM (NOW() - s.last_simulation)) / 60 as simulation_lag_min
FROM decision_stats d, simulation_stats s;
```

### Find Decisions Without Simulations

```sql
SELECT
    dl.id,
    dl.symbol,
    dl.decision,
    dl.created_at,
    EXTRACT(EPOCH FROM (NOW() - dl.created_at)) / 60 as age_minutes
FROM decisions_log dl
LEFT JOIN trade_simulations ts ON dl.id = ts.decision_id
WHERE ts.id IS NULL
  AND dl.created_at >= NOW() - INTERVAL '24 hours'
  AND dl.decision = 'ALLOW'
ORDER BY dl.created_at DESC
LIMIT 20;
```

---

## Next Steps

After validating the pipeline:

1. Monitor for 24-48 hours to ensure stability
2. Review any warnings in `/api/system/pipeline-status`
3. Check ML training pipeline can consume `trade_simulations` data
4. Set up production alerts based on metrics above
5. Consider manual SQL migration execution (see SQL_MIGRATIONS_MANUAL.sql)

---

**Document Version:** 1.0
**Date:** 2026-04-30
**Author:** Pipeline Reliability Task Force
