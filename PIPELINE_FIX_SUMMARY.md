# Pipeline Reliability Fix — Summary Report

## Executive Summary

This document summarizes the comprehensive end-to-end fix for the SCALPYN pipeline:

**L3 → decision_logs → simulation → trade_simulations → ML**

All changes have been implemented in code. SQL scripts are provided separately for manual DBA execution.

---

## 🎯 Objectives Achieved

✅ **PART 1:** Fixed decision persistence to raise exceptions on failure (no silent failures)
✅ **PART 2:** Added decision deduplication at application level (5-minute window)
✅ **PART 3:** Added OHLCV validation before simulation (prevents dry runs)
✅ **PART 4:** Hardened simulation engine with structured logging
✅ **PART 5:** Verified simulation triggers automatically (Celery Beat every 10 min)
✅ **PART 6:** Added comprehensive logging improvements throughout pipeline
✅ **PART 7:** Created `/api/system/pipeline-status` validation endpoint
✅ **PART 8:** Generated SQL migration scripts (DO NOT EXECUTE — manual only)
✅ **PART 9:** Documented comprehensive testing plan

---

## 📊 Root Cause Analysis

### Issues Identified

1. **Silent Decision Persistence Failures**
   - Decision log errors were caught and logged but not raised
   - Pipeline continued without knowing decisions weren't saved
   - **Impact:** Broken data flow, missing ML training data

2. **No Deduplication Logic**
   - Multiple pipeline cycles could log the same decision repeatedly
   - No app-level or DB-level uniqueness constraint
   - **Impact:** Duplicate decisions, inflated metrics

3. **No OHLCV Validation**
   - Simulation batch ran without checking if candle data existed
   - Many simulations skipped silently due to missing data
   - **Impact:** Low simulation coverage, wasted compute

4. **Weak Simulation Reliability**
   - Skip reasons not logged clearly
   - No alerting on high skip rates
   - Simulation timeout logic existed but not well-validated
   - **Impact:** Poor observability, hard to debug

5. **Missing Pipeline Visibility**
   - No single endpoint to check pipeline health
   - Manual SQL queries required to diagnose issues
   - **Impact:** Slow incident response

---

## 🔧 Code Changes

### File: `backend/app/tasks/pipeline_scan.py`

#### Change 1.1: Exception Handling (Lines 2002-2011)

**Before:**
```python
except Exception as _dl_exc:
    logger.error(
        "[Decision] Failed to persist decision logs for watchlist %s: %s "
        "— verify migration 026 (direction/event_type columns) is applied",
        wl_id, _dl_exc,
    )
```

**After:**
```python
except Exception as _dl_exc:
    logger.error(
        "FATAL: Decision persistence failed for watchlist %s: %s "
        "— verify migration 026 (direction/event_type columns) is applied",
        wl_id, _dl_exc, exc_info=True
    )
    # CRITICAL: Re-raise exception to prevent silent failure
    raise RuntimeError(
        f"Decision persistence failed for watchlist {wl_id}: {_dl_exc}"
    ) from _dl_exc
```

**Impact:** Pipeline now fails fast on decision persistence errors instead of continuing silently.

#### Change 1.2: Decision Deduplication (Lines 977-1037)

**Added:** Complete deduplication logic in `_persist_decision_logs()`:
- Checks for existing decisions in last 5 minutes
- Filters out duplicates before insert
- Logs skip count for observability

**Code:**
```python
# DEDUPLICATION: Check for recent duplicate decisions (last 5 minutes)
now = datetime.now(timezone.utc)
recent_window = now - timedelta(minutes=5)

existing_result = await db.execute(text("""
    SELECT DISTINCT symbol, strategy, direction
    FROM decisions_log
    WHERE created_at >= :recent_window
      AND (symbol, strategy, COALESCE(direction, '')) IN :checks
"""), {"recent_window": recent_window, "checks": unique_checks})

existing_decisions = {
    (row.symbol, row.strategy, row.direction or None)
    for row in existing_result.fetchall()
}

# Filter out duplicates
for decision in decisions:
    key = (decision["symbol"], decision["strategy"], decision.get("direction"))
    if key in existing_decisions:
        skipped_count += 1
    else:
        decisions_to_insert.append(decision)
```

**Impact:** Prevents duplicate decision logging, ensures data integrity.

#### Change 1.3: Enhanced Logging (Lines 1083-1095)

**Added:** Structured logging with decision ID:

```python
logger.info(
    "[Decision] PERSISTED | id=%s | %s | score=%s | %s | event=%s",
    row.id, row.symbol, round(float(row.score or 0), 2),
    row.decision, row.event_type or "—",
)

logger.info(
    "[Decision] Batch persisted: %d decision(s) successfully logged to decisions_log table",
    len(payloads)
)
```

**Impact:** Clear audit trail for every decision logged.

---

### File: `backend/app/services/simulation_service.py`

#### Change 2.1: OHLCV Validation (Lines 252-283)

**Added:** Pre-flight check before simulation batch:

```python
# CRITICAL: Validate OHLCV data availability before processing batch
ohlcv_check = await self.session.execute(text("""
    SELECT COUNT(DISTINCT symbol) as symbol_count,
           MAX(time) as latest_time,
           COUNT(*) as total_candles
    FROM ohlcv
    WHERE exchange = :exchange
      AND timeframe = '1h'
      AND time >= NOW() - INTERVAL '24 hours'
"""), {"exchange": exchange})

ohlcv_row = ohlcv_check.fetchone()

if not ohlcv_row or not ohlcv_row.total_candles:
    error_msg = f"OHLCV validation FAILED: No recent candle data found"
    logger.error(error_msg)
    raise RuntimeError(error_msg)

if ohlcv_row.total_candles < 100:
    error_msg = f"OHLCV validation FAILED: Insufficient candle data"
    logger.error(error_msg)
    raise RuntimeError(error_msg)

logger.info(
    "[Simulation] OHLCV validation PASSED: %d symbols, %d candles, latest=%s",
    ohlcv_row.symbol_count, ohlcv_row.total_candles, ohlcv_row.latest_time
)
```

**Impact:** Prevents wasted simulation attempts, fails fast if data unavailable.

#### Change 2.2: Structured Logging (Lines 151-172, 205-209, 333-342)

**Before:**
```python
logger.warning("No candles found for %s after %s", decision.symbol, decision.created_at)
```

**After:**
```python
logger.warning(
    "[Simulation] SKIP: No candles found | symbol=%s | after=%s",
    decision.symbol, decision.created_at
)
```

Applied consistently across:
- No candles found
- Insufficient candles
- Failed entry price calculation
- Invalid simulation results

**Impact:** Uniform log format, easier to parse and alert on.

#### Change 2.3: Skip Rate Monitoring (Lines 305-370)

**Added:** Track and alert on high skip rates:

```python
skipped_no_candles = 0
skipped_invalid = 0

# ... in loop ...
if records:
    all_records.extend(records)
    simulated += len(records)
    logger.debug("[Simulation] SUCCESS | decision_id=%s | symbol=%s | records=%d", ...)
else:
    skipped_no_candles += 1
    logger.debug("[Simulation] SKIP | decision_id=%s | symbol=%s | reason=no_candles", ...)

# Calculate skip rate
total_attempts = processed
total_skipped = skipped_no_candles + skipped_invalid
skip_rate = (total_skipped / total_attempts * 100) if total_attempts > 0 else 0

# Alert if skip rate is excessive
if skip_rate > 50 and total_attempts > 10:
    logger.warning(
        "[Simulation] HIGH SKIP RATE: %.1f%% (%d/%d) — check OHLCV data quality",
        skip_rate, total_skipped, total_attempts
    )
```

**Impact:** Proactive alerting on data quality issues.

#### Change 2.4: Enhanced Return Metrics (Lines 384-393)

**Added to return dict:**
```python
return {
    "total_decisions": len(decisions),
    "processed": processed,
    "skipped": skipped,
    "simulated": simulated,
    "errors": errors,
    "records_inserted": len(all_records),
    "skipped_no_candles": skipped_no_candles,  # NEW
    "skip_rate": round(skip_rate, 2),          # NEW
}
```

**Impact:** Better observability of simulation batch health.

---

### File: `backend/app/api/system.py` (NEW)

**Created:** New API endpoint for pipeline health monitoring.

**Endpoint:** `GET /api/system/pipeline-status`

**Returns:**
```json
{
  "status": "healthy" | "warning" | "degraded" | "error",
  "timestamp": "ISO8601",
  "pipeline": {
    "decisions": {
      "total": 1250,
      "allow": 890,
      "block": 360,
      "last_time": "ISO8601",
      "lag_minutes": 5,
      "last_hour": 45
    },
    "simulations": {
      "total": 2340,
      "unique_decisions": 1100,
      "last_time": "ISO8601",
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
  "errors": ["NO_DECISIONS", ...],
  "warnings": ["STALE_SIMULATIONS: Last simulation 25 minutes ago", ...],
  "health_summary": {
    "pipeline_operational": true,
    "decisions_flowing": true,
    "simulations_running": true,
    "data_quality": "good"
  }
}
```

**Logic:**
- Queries `decisions_log` and `trade_simulations`
- Calculates coverage, lag, and health metrics
- Returns errors/warnings for common issues
- Overall status derived from checks

**Impact:** Single endpoint for pipeline health monitoring and alerting.

---

### File: `backend/app/main.py`

**Changes:**
- Added `system` to imports (line 36)
- Registered `system.router` (line 244)

**Impact:** Makes `/api/system/*` endpoints available.

---

## 📁 SQL Migration Scripts

### File: `backend/alembic/SQL_MIGRATIONS_MANUAL.sql` (NEW)

**DO NOT EXECUTE AUTOMATICALLY**

Contains 5 migrations for manual DBA execution:

1. **UNIQUE constraint for deduplication**
   - Partial index on recent decisions (last 5 min)
   - Enforces uniqueness at DB level (defense in depth)

2. **Composite index for deduplication queries**
   - Optimizes the app-level deduplication lookup
   - Covers `(symbol, strategy, direction, created_at DESC)`

3. **Index for simulation pipeline**
   - Speeds up "decisions needing simulation" query
   - Covers `(created_at DESC, id) WHERE decision = 'ALLOW'`

4. **Optional: created_at default**
   - Defensive default for timestamp column
   - Usually handled by ORM, but good practice

5. **Index for pipeline status endpoint**
   - Optimizes `/api/system/pipeline-status` queries
   - Covers `(created_at DESC, decision)`

**Includes:**
- Diagnostic queries to check for existing duplicates
- Cleanup script for duplicates (commented out)
- Verification queries
- Rollback instructions

**Why Manual?**
- Safety: Review before execution
- Timing: Run during low-traffic window
- `CREATE INDEX CONCURRENTLY` requires manual monitoring
- May need duplicate cleanup first

---

## 📖 Testing & Validation

### File: `PIPELINE_TESTING_PLAN.md` (NEW)

Comprehensive testing guide with:

1. **Phase 1: Verify Decision Persistence**
   - Check initial state
   - Trigger L3 approval
   - Verify decision logged
   - Verify deduplication

2. **Phase 2: Verify Simulation Execution**
   - Check simulation status before
   - Trigger simulation batch
   - Monitor logs
   - Verify simulations created
   - Check for errors

3. **Phase 3: Verify Pipeline Status Endpoint**
   - Call endpoint
   - Verify response structure
   - Validate metrics against SQL

4. **Phase 4: End-to-End Integration Test**
   - Add new symbol to L3
   - Wait for pipeline cycle
   - Verify full flow (decision → simulation)

**Includes:**
- Error scenarios & troubleshooting
- Success criteria
- Monitoring & alerting recommendations
- Quick diagnostic queries

---

## 🔍 Validation Steps (Quick Start)

### 1. Trigger L3 Approval

```bash
# Wait for pipeline scan (every 5 minutes) OR trigger manually
celery -A app.tasks.celery_app call app.tasks.pipeline_scan.scan
```

### 2. Verify Decision Logged

```sql
SELECT id, symbol, decision, l3_pass, created_at
FROM decisions_log
ORDER BY created_at DESC
LIMIT 5;
```

Expected: New decision(s) with recent timestamp.

### 3. Wait for Simulation (10 minutes)

```bash
# OR trigger manually
celery -A app.tasks.celery_app call app.tasks.simulation.run_simulation_batch \
  --kwargs '{"limit": 100, "skip_existing": true}'
```

### 4. Verify Simulations Created

```sql
SELECT
    ts.id,
    ts.symbol,
    ts.result,
    dl.id as decision_id
FROM trade_simulations ts
JOIN decisions_log dl ON ts.decision_id = dl.id
ORDER BY ts.created_at DESC
LIMIT 10;
```

Expected: Simulations linked to decisions.

### 5. Check Pipeline Status

```bash
curl http://localhost:8000/api/system/pipeline-status | jq .
```

Expected: `"status": "healthy"` with no errors.

---

## 🚨 Expected Results

After implementation and validation:

1. ✅ **Decision logs persist reliably**
   - No silent failures
   - Exceptions raised and logged on errors
   - Clear audit trail

2. ✅ **No duplicate decisions**
   - 5-minute deduplication window enforced
   - Skip count logged
   - DB constraint available (after manual migration)

3. ✅ **Simulations run predictably**
   - OHLCV validation prevents dry runs
   - Skip reasons logged clearly
   - High skip rate triggers warning

4. ✅ **Pipeline is observable**
   - Single status endpoint
   - Structured logs throughout
   - Coverage and lag metrics

5. ✅ **ML pipeline has clean data**
   - `trade_simulations` table populated
   - Each simulation linked to decision
   - Idempotency via UNIQUE constraint

---

## 📈 Monitoring Recommendations

### Key Metrics

1. **Decision Lag**
   - Alert if > 15 minutes
   - Check L3 watchlist and pipeline scan

2. **Simulation Lag**
   - Alert if > 30 minutes
   - Check OHLCV data and Celery workers

3. **Coverage**
   - Alert if < 70% after 24 hours
   - Check OHLCV availability for symbols

4. **Skip Rate**
   - Alert if > 30%
   - Check OHLCV data quality

### Alerting

Set up alerts on:
- `/api/system/pipeline-status` returning `"status": "error"`
- Log pattern: `FATAL.*Decision persistence`
- Log pattern: `OHLCV validation FAILED`
- Log pattern: `HIGH SKIP RATE`

---

## 🔄 Next Steps

1. **Deploy Changes**
   - Review code changes
   - Deploy to staging first
   - Validate with testing plan

2. **Execute SQL Migrations (Manual)**
   - Review `SQL_MIGRATIONS_MANUAL.sql`
   - Run during low-traffic window
   - Use `CONCURRENTLY` for indexes
   - Verify with diagnostic queries

3. **Monitor for 24-48 Hours**
   - Check `/api/system/pipeline-status` hourly
   - Watch logs for errors/warnings
   - Verify coverage improves

4. **Set Up Production Alerts**
   - Use metrics recommendations above
   - Alert on errors and warnings
   - Dashboard for coverage/lag

5. **ML Pipeline Integration**
   - Verify ML training can consume `trade_simulations`
   - Check feature extraction logic
   - Validate model training

---

## 📌 Summary

| Component | Before | After |
|-----------|--------|-------|
| **Decision Persistence** | Silent failures | Raises exceptions |
| **Deduplication** | None | 5-minute window check |
| **OHLCV Validation** | None | Pre-flight check with 100-candle minimum |
| **Logging** | Inconsistent | Structured with [Simulation] tags |
| **Skip Monitoring** | None | Track rate, alert if > 50% |
| **Pipeline Visibility** | Manual SQL only | `/api/system/pipeline-status` endpoint |
| **Testing** | Ad-hoc | Comprehensive plan in PIPELINE_TESTING_PLAN.md |
| **SQL Migrations** | N/A | 5 scripts in SQL_MIGRATIONS_MANUAL.sql |

---

## ✅ Checklist

- [x] PART 1: Fixed decision persistence exception handling
- [x] PART 2: Added decision deduplication logic
- [x] PART 3: Added OHLCV validation before simulation
- [x] PART 4: Hardened simulation engine with structured logging
- [x] PART 5: Verified simulation triggers (Celery Beat every 10 min)
- [x] PART 6: Added comprehensive logging improvements
- [x] PART 7: Created pipeline-status validation endpoint
- [x] PART 8: Generated SQL migration scripts (DO NOT EXECUTE)
- [x] PART 9: Documented testing plan and validation steps

---

**Document Version:** 1.0
**Date:** 2026-04-30
**Task:** Fix decision_logs → simulation pipeline reliability
**Status:** ✅ COMPLETE — Ready for validation
