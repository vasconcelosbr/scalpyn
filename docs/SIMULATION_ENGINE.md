# Trade Simulation Engine

## Overview

The Trade Simulation Engine is a production-ready system for generating labeled trade outcomes using historical OHLCV data. This system generates the dataset used to train XGBoost models for the SCALPYN platform.

## Architecture

### Components

1. **Database Layer** (`backend/alembic/versions/025_trade_simulations.py`)
   - `trade_simulations` table with idempotency constraints
   - Indexes for efficient querying
   - Foreign key to `decisions_log`

2. **Model** (`backend/app/models/trade_simulation.py`)
   - SQLAlchemy ORM model for `trade_simulations`
   - Validation constraints for result, direction, and decision types

3. **Repository** (`backend/app/repositories/simulation_repository.py`)
   - Bulk insert operations with conflict resolution
   - Statistics aggregation
   - Data querying and filtering

4. **Core Engine** (`backend/app/services/simulation_engine.py`)
   - Entry price calculation (next candle open)
   - TP/SL calculation for LONG/SHORT/SPOT
   - Trade simulation through candles
   - Gap detection and validation

5. **Service** (`backend/app/services/simulation_service.py`)
   - Orchestrates the simulation process
   - Fetches OHLCV data from database
   - Handles batch processing
   - Configuration management

6. **Celery Tasks** (`backend/app/tasks/simulation.py`)
   - Async task execution
   - Single and batch simulation support
   - Progress tracking

7. **API Endpoints** (`backend/app/api/simulations.py`)
   - POST `/api/simulations/run` - Trigger batch simulation
   - POST `/api/simulations/run/{decision_id}` - Simulate single decision
   - GET `/api/simulations/stats` - Get statistics
   - GET `/api/simulations/config` - Get configuration

8. **CLI Tool** (`simulate.py`)
   - Command-line interface for simulations
   - Progress tracking
   - Statistics display

## Core Flow

For each decision log entry:

1. **Fetch Decision**: Get decision from `decisions_log`
2. **Load Configuration**: Get simulation config (TP%, SL%, timeout)
3. **Fetch OHLCV**: Get candles after decision timestamp
4. **Calculate Entry**: Entry price = OPEN of next candle
5. **Determine Directions**:
   - SPOT: Only LONG
   - FUTURES: Both LONG and SHORT
6. **Simulate Trade**: Iterate through candles checking TP/SL
7. **Store Result**: Bulk insert to `trade_simulations`

## Simulation Logic

### Entry Price

```
entry_price = OPEN of first candle after decision timestamp
entry_timestamp = timestamp of that candle
```

### TP/SL Calculation

**LONG (or SPOT)**:
```
TP = entry_price × (1 + tp_pct)
SL = entry_price × (1 + sl_pct)
```

**SHORT**:
```
TP = entry_price × (1 - tp_pct)
SL = entry_price × (1 - sl_pct)
```

### Trade Outcome Detection

For each candle after entry (up to `timeout_candles`):

**LONG**:
- If `high >= TP` → WIN
- If `low <= SL` → LOSS
- Neither after timeout → TIMEOUT

**SHORT**:
- If `low <= TP` → WIN
- If `high >= SL` → LOSS
- Neither after timeout → TIMEOUT

### Gap Handling (CRITICAL)

If any gap between consecutive candles exceeds the expected timeframe interval:
- Mark simulation as INVALID
- Do NOT store in database
- Log the gap for debugging

## Configuration

Simulation configuration is stored in `config_profiles` table with `config_type = 'ai_settings'`:

```json
{
  "entry_mode": "next_candle_open",
  "tp_pct": 0.012,
  "sl_pct": -0.008,
  "timeout_candles": 10
}
```

Configuration can be accessed via:
- API: `GET /api/config/ai_settings`
- Service: `SimulationService.get_simulation_config()`

## Database Schema

### `trade_simulations` Table

```sql
CREATE TABLE trade_simulations (
    id UUID PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    timestamp_entry TIMESTAMPTZ NOT NULL,
    entry_price NUMERIC(20, 8) NOT NULL,

    tp_price NUMERIC(20, 8) NOT NULL,
    sl_price NUMERIC(20, 8) NOT NULL,

    exit_price NUMERIC(20, 8),
    exit_timestamp TIMESTAMPTZ,

    result VARCHAR(10) NOT NULL,  -- WIN | LOSS | TIMEOUT
    time_to_result INTEGER,       -- seconds

    direction VARCHAR(10) NOT NULL,  -- LONG | SHORT | SPOT

    is_simulated BOOLEAN DEFAULT TRUE,
    source VARCHAR(30) DEFAULT 'SIMULATION',

    decision_type VARCHAR(10) NOT NULL,  -- ALLOW | BLOCK
    decision_id BIGINT REFERENCES decisions_log(id),

    features_snapshot JSONB,
    config_snapshot JSONB,

    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_simulation_symbol_entry_direction
        UNIQUE (symbol, timestamp_entry, direction)
);
```

## Usage

### CLI Usage

Run batch simulation:
```bash
python simulate.py run --limit 1000
```

Skip existing simulations:
```bash
python simulate.py run --limit 500 --no-skip-existing
```

Show statistics:
```bash
python simulate.py stats
```

### API Usage

Trigger batch simulation:
```bash
curl -X POST "http://localhost:8001/api/simulations/run?limit=100" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

Get statistics:
```bash
curl "http://localhost:8001/api/simulations/stats" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

### Celery Task Usage

```python
from app.tasks.simulation import run_simulation_batch

# Trigger async task
task = run_simulation_batch.apply_async(
    kwargs={
        "limit": 100,
        "skip_existing": True,
    }
)

# Check result
result = task.get(timeout=300)
```

## Idempotency

The system ensures idempotency through:

1. **Database Constraint**: `UNIQUE (symbol, timestamp_entry, direction)`
2. **ON CONFLICT DO NOTHING**: Bulk inserts ignore duplicates
3. **Skip Existing**: Optional flag to skip already-simulated decisions

## Validation

Before simulation, the engine validates:

1. **OHLCV Availability**: Sufficient candles after decision
2. **Candle Count**: At least `timeout_candles + 1` available
3. **Gap Detection**: No gaps larger than timeframe interval
4. **Data Quality**: All required OHLCV fields present

Invalid simulations are:
- Logged with reason
- NOT stored in database
- Counted in error statistics

## Performance

### Optimization Strategies

1. **Bulk Insert**: Insert 500 records per batch
2. **Parallel Processing**: Process multiple symbols concurrently
3. **Skip Existing**: Avoid re-processing simulated decisions
4. **Index Usage**: Efficient queries via composite indexes
5. **Batch Size**: Configurable limit parameter

### Expected Performance

- ~100-500 decisions per batch
- ~1-2 seconds per decision (including DB queries)
- ~2-10 minutes for 1000 decisions (with OHLCV data available)

## Logging

The engine logs:

- **INFO**: Decision processed, records inserted, progress updates
- **WARNING**: Insufficient candles, gaps detected, config fetch failures
- **ERROR**: Database errors, simulation failures, task errors

Example log output:
```
INFO: Processing 100 decisions for simulation
INFO: Progress: 10/100 decisions processed
INFO: Simulated decision 12345: 2 records inserted
WARNING: Gap detected between 2026-04-29T10:00:00Z and 2026-04-29T12:00:00Z
INFO: Batch simulation complete: {'processed': 98, 'skipped': 0, 'simulated': 196, 'errors': 2}
```

## ML Dataset Usage

The dataset for ML training MUST be built ONLY from `trade_simulations` table:

```sql
SELECT
    symbol,
    direction,
    result,
    features_snapshot,
    config_snapshot,
    time_to_result
FROM trade_simulations
WHERE result IN ('WIN', 'LOSS')  -- Exclude TIMEOUT if desired
ORDER BY timestamp_entry DESC;
```

Features for ML model:
- Extract from `features_snapshot` (decision metrics)
- Include: `direction`, `config_snapshot` (TP%, SL%)
- Target: `result` (WIN=1, LOSS=0)

## Critical Rules

1. **DO NOT use current price** - Always use historical OHLCV
2. **DO NOT use future leaks** - Entry is strictly next candle open
3. **ALWAYS use next candle open** - No other entry modes
4. **DISCARD invalid simulations** - Never store incomplete data
5. **Each position is independent** - No correlation assumptions

## Future Enhancements

Potential improvements:

1. **Parallel Processing**: Distribute across multiple workers
2. **Streaming**: Real-time simulation as decisions arrive
3. **Advanced Entry**: Support for limit orders, market depth
4. **Partial Exits**: Simulate layered take-profit strategies
5. **Slippage Modeling**: Account for execution delays
6. **Commission Modeling**: Include trading fees

## Troubleshooting

### No candles found

**Problem**: `WARNING: No candles found for BTC_USDT after 2026-04-29T10:00:00Z`

**Solution**:
- Ensure OHLCV backfill is complete
- Check timeframe matches decision timeframe
- Verify symbol format (use `_` not `/`)

### Gap detected

**Problem**: `WARNING: Gap detected: expected 1:00:00, got 2:00:00`

**Solution**:
- Run OHLCV backfill to fill gaps
- Check exchange data availability
- Consider different timeframe

### Insufficient candles

**Problem**: `WARNING: Insufficient candles for BTC_USDT: got 5, need at least 2`

**Solution**:
- Increase OHLCV data collection range
- Reduce `timeout_candles` in config
- Focus on more recent decisions

## Migration

To apply the database migration:

```bash
cd backend
alembic upgrade head
```

This will create the `trade_simulations` table and all required indexes.

## Testing

Basic tests to verify functionality:

```python
import asyncio
from app.database import AsyncSessionLocal
from app.services.simulation_service import SimulationService

async def test_simulation():
    async with AsyncSessionLocal() as session:
        service = SimulationService(session)

        # Test config loading
        config = await service.get_simulation_config()
        assert "tp_pct" in config

        # Test stats (may be empty initially)
        stats = await service.get_stats()
        assert "total" in stats

        print("✓ Simulation service tests passed")

asyncio.run(test_simulation())
```

## Monitoring

Key metrics to monitor:

1. **Simulation Rate**: Decisions processed per minute
2. **Success Rate**: Valid simulations / total attempts
3. **Win Rate**: Wins / (Wins + Losses)
4. **Error Rate**: Errors / total attempts
5. **Avg Time to Result**: Average holding time
6. **Data Coverage**: Unique symbols simulated

Access via `/api/simulations/stats` endpoint.
