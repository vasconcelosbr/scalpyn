# Automatic Simulation Engine Execution

## Overview

The SCALPYN Simulation Engine now runs automatically every 10 minutes via Celerybeat, continuously processing new decisions from `decisions_log` into the `trade_simulations` table for ML training.

## Architecture

### Automatic Execution

**Schedule:** Every 10 minutes (crontab: `*/10`)

**Task:** `app.tasks.simulation.run_simulation_batch`

**Parameters:**
- `limit`: 200 decisions per batch
- `skip_existing`: True (idempotent, prevents duplicates)
- `exchange`: "gate"

### Task Configuration

The batch simulation task includes:

- **Timeout Protection:** 10-minute hard limit, 9-minute soft limit
- **Retry Logic:** 3 retries with 60-second delays
- **Error Handling:** Graceful failure handling, continues on errors
- **Enhanced Logging:** Detailed metrics for monitoring
- **Safety Controls:** Maximum batch size limit (1000)

## Configuration

### Celerybeat Schedule

Located in: `backend/app/tasks/celery_app.py`

```python
"run_simulation_batch_every_10min": {
    "task": "app.tasks.simulation.run_simulation_batch",
    "schedule": crontab(minute="*/10"),
    "kwargs": {
        "limit": 200,
        "skip_existing": True,
    },
}
```

### Task Options

File: `backend/app/tasks/simulation.py`

```python
@celery_app.task(
    name="app.tasks.simulation.run_simulation_batch",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    time_limit=600,      # 10 minutes
    soft_time_limit=540,  # 9 minutes
)
```

## Monitoring

### Status Endpoint

**GET** `/api/simulations/status`

Returns comprehensive system health:

```json
{
  "status": "healthy",  // healthy | warning | stale | empty
  "total_simulations": 5234,
  "last_simulation": "2026-04-29T18:00:00Z",
  "lag_minutes": 8,
  "is_current": true,
  "stats": {
    "total": 5234,
    "wins": 3140,
    "losses": 1894,
    "win_rate": 62.4,
    "unique_symbols": 127
  },
  "system": {
    "automatic_execution": true,
    "schedule": "every 10 minutes",
    "batch_size": 200
  }
}
```

### Health Status Definitions

- **healthy**: Simulations current (< 15 minutes old)
- **warning**: Simulations lag 15-60 minutes
- **stale**: Simulations lag > 60 minutes
- **empty**: No simulations exist yet

### Logging

All simulation runs log:
- Start time and parameters
- Progress metrics (processed, skipped, simulated, errors)
- Duration and completion time
- Error details with stack traces

**Log Format:**
```
[Simulation] Starting batch simulation: limit=200, skip_existing=True, exchange=gate
[Simulation] Batch complete in 45.23s: processed=150, skipped=50, simulated=300, errors=0, records_inserted=300
```

## Initial Bootstrap

### First-Time Setup

For initial dataset population, run a larger batch manually:

```bash
# CLI
cd /home/runner/work/scalpyn/scalpyn
python simulate.py run --limit 5000

# OR via API
curl -X POST "http://localhost:8000/api/simulations/run?limit=5000&skip_existing=false" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

**Note:** Use `skip_existing=false` for first run to process all historical decisions.

### Bootstrap Script

A helper script for initial backfill:

```python
#!/usr/bin/env python3
"""Bootstrap script for initial simulation backfill."""

import asyncio
import logging
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent / "backend"))

from app.database import AsyncSessionLocal
from app.services.simulation_service import SimulationService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def bootstrap():
    """Run initial backfill of simulations."""
    logger.info("Starting simulation bootstrap...")

    async with AsyncSessionLocal() as session:
        service = SimulationService(session)

        # Large batch for initial backfill
        result = await service.run_simulation_batch(
            limit=5000,
            skip_existing=False,  # Process all
            exchange="gate",
        )

        logger.info("Bootstrap complete: %s", result)

        # Show stats
        stats = await service.get_stats()
        logger.info("Total simulations: %d", stats.get("total", 0))
        logger.info("Win rate: %.2f%%", stats.get("win_rate", 0))

if __name__ == "__main__":
    asyncio.run(bootstrap())
```

## Safety Controls

### Idempotency

The system ensures no duplicate simulations:

1. **Database Constraint:** `UNIQUE (symbol, timestamp_entry, direction)`
2. **ON CONFLICT DO NOTHING:** Bulk inserts ignore duplicates
3. **Skip Existing:** Task checks `decision_id` before processing

### Resource Protection

- **Max Batch Size:** Limited to 1000 decisions
- **Timeout:** Tasks killed after 10 minutes
- **Rate Limiting:** Runs only every 10 minutes
- **Retry Logic:** Max 3 retries with backoff

### Error Handling

- **Transient Errors:** Automatically retried
- **Permanent Errors:** Logged and skipped
- **Batch Continuation:** One failure doesn't stop entire batch
- **No Blocking:** Main pipeline unaffected

## Database Optimization

### Existing Indexes

All required indexes are in place (migration 025):

```sql
-- Core indexes
CREATE INDEX idx_trade_simulations_symbol ON trade_simulations(symbol);
CREATE INDEX idx_trade_simulations_timestamp_entry ON trade_simulations(timestamp_entry);
CREATE INDEX idx_trade_simulations_decision_type ON trade_simulations(decision_type);

-- Composite indexes
CREATE INDEX idx_trade_simulations_symbol_timestamp
    ON trade_simulations(symbol, timestamp_entry DESC);

-- Foreign key
CREATE CONSTRAINT fk_trade_simulations_decision_id
    FOREIGN KEY (decision_id) REFERENCES decisions_log(id);
```

No additional indexes needed.

## Operational Procedures

### Starting the System

1. **Ensure Celerybeat is running:**
   ```bash
   celery -A app.tasks.celery_app beat --loglevel=info
   ```

2. **Ensure Celery workers are running:**
   ```bash
   celery -A app.tasks.celery_app worker --loglevel=info
   ```

3. **Verify schedule loaded:**
   ```bash
   celery -A app.tasks.celery_app inspect scheduled
   ```

### Initial Backfill

After deployment, run bootstrap once:

```bash
python simulate.py run --limit 5000
```

This populates initial dataset. After that, automatic execution handles new decisions.

### Monitoring

Check status regularly:

```bash
# Via API
curl "http://localhost:8000/api/simulations/status" \
  -H "Authorization: Bearer YOUR_TOKEN"

# Via CLI
python simulate.py stats
```

### Troubleshooting

**Problem:** Status shows "stale"

**Solution:**
1. Check Celerybeat is running
2. Check Celery workers are running
3. Check logs for task failures
4. Manually trigger: `POST /api/simulations/run?limit=200`

**Problem:** No simulations being created

**Solution:**
1. Check `decisions_log` has data
2. Check OHLCV data availability
3. Check logs for specific errors
4. Verify task is scheduled: `celery inspect scheduled`

**Problem:** Task timeouts

**Solution:**
1. Reduce batch size in beat schedule (default: 200)
2. Check database performance
3. Check OHLCV query performance

## Performance Metrics

### Expected Behavior

- **Batch Size:** 200 decisions
- **Processing Time:** 30-120 seconds
- **Frequency:** Every 10 minutes
- **Throughput:** ~1200-2400 simulations/hour
- **Daily Generation:** ~28,800-57,600 new simulations

### Capacity Planning

For 100+ symbols with continuous trading:
- Decisions per day: ~14,400 (100 symbols × 144 hourly decisions)
- Simulations per day: ~28,800 (2 directions × 14,400)
- System easily handles load with 10-minute batches

## Integration with ML Pipeline

### Data Flow

```
decisions_log → [Simulation Engine] → trade_simulations → [ML Training] → model.pkl
     ↓              (every 10 min)          ↓                                  ↓
  Real-time                          Training Dataset                    Predictions
  Decisions                          Always Fresh                        Always Accurate
```

### Training Workflow

1. **Continuous Simulation:** Runs every 10 minutes
2. **ML Training:** Trigger when needed (weekly/on-demand)
3. **Model Deployment:** Reload after training
4. **Predictions:** Use latest model for L3 ranking

### Training Command

```bash
# Train with latest simulations
curl -X POST "http://localhost:8000/api/ml/train" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{"model_name": "model.pkl"}'
```

## Configuration Options

### Adjusting Schedule

To change frequency, edit `celery_app.py`:

```python
# More frequent (every 5 minutes)
"schedule": crontab(minute="*/5"),

# Less frequent (every 30 minutes)
"schedule": crontab(minute="*/30"),

# Specific times (on the hour)
"schedule": crontab(minute=0),
```

### Adjusting Batch Size

Edit the kwargs in beat schedule:

```python
"kwargs": {
    "limit": 500,  # Increase for larger batches
    "skip_existing": True,
}
```

### Disabling Automatic Execution

Remove or comment out the schedule entry:

```python
# "run_simulation_batch_every_10min": {
#     "task": "app.tasks.simulation.run_simulation_batch",
#     ...
# },
```

Then restart Celerybeat.

## Best Practices

1. **Monitor Regularly:** Check `/api/simulations/status` daily
2. **Watch Logs:** Monitor for errors or warnings
3. **Initial Backfill:** Always run bootstrap after deployment
4. **Periodic Training:** Retrain ML model weekly or when dataset grows significantly
5. **Health Alerts:** Set up alerts if status becomes "stale"
6. **Resource Monitoring:** Watch database size growth
7. **Performance Tuning:** Adjust batch size based on load

## Security Considerations

- Status endpoint requires authentication
- Task execution logged for audit trail
- No sensitive data exposed in logs
- Database credentials managed securely via environment

## Rollback Procedure

If automatic execution causes issues:

1. **Disable Schedule:** Remove from `beat_schedule`
2. **Restart Celerybeat:** Apply configuration change
3. **Revert to Manual:** Use CLI or API for controlled execution
4. **Investigate:** Check logs, fix issues
5. **Re-enable:** Add schedule back, restart

## Future Enhancements

Potential improvements:

- [ ] Adaptive batch sizing based on load
- [ ] Priority queue for recent decisions
- [ ] Parallel processing across symbols
- [ ] Real-time streaming simulation
- [ ] Automatic ML retraining triggers
- [ ] Advanced health metrics dashboard
- [ ] Alert integration (Slack, email)
- [ ] Performance metrics tracking

---

**Version:** 1.0.0
**Last Updated:** 2026-04-29
**Status:** Production Ready
