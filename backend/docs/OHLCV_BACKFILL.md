# OHLCV Backfill System

Production-ready historical OHLCV data backfill for the Scalpyn trading platform.

## Overview

The OHLCV backfill system provides a robust solution for populating historical candlestick data from Gate.io API into PostgreSQL + TimescaleDB. It features:

- ✅ **Idempotent operations** — Safe to re-run without duplicates (UPSERT pattern)
- ✅ **Chunk-based backfill** — Fetches data in manageable chunks, backwards in time
- ✅ **Async/parallel processing** — Concurrent symbol processing with rate limiting
- ✅ **Rate limit handling** — Exponential backoff retry with 429 detection
- ✅ **Data validation** — OHLC relationship checks and positive value validation
- ✅ **Resume capability** — Automatically detects existing data and continues from gaps
- ✅ **TimescaleDB optimization** — Compression, retention policies, and efficient indexing
- ✅ **CLI and Celery interfaces** — Manual execution or automated scheduling

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Backfill Entry Points                     │
├─────────────────────────────────────────────────────────────┤
│  CLI Script                 │  Celery Task                   │
│  scripts/backfill.py        │  tasks/ohlcv_backfill.py      │
└───────────────┬─────────────┴──────────────┬────────────────┘
                │                            │
                └──────────┬─────────────────┘
                           ▼
        ┌──────────────────────────────────────┐
        │  OHLCVBackfillService                 │
        │  services/ohlcv_backfill_service.py   │
        ├───────────────────────────────────────┤
        │  • Chunk-based fetching               │
        │  • Async HTTP with retry              │
        │  • Data validation                    │
        │  • Parallel processing                │
        └───────────────┬───────────────────────┘
                        ▼
        ┌──────────────────────────────────────┐
        │  OHLCVRepository                      │
        │  repositories/ohlcv_repository.py     │
        ├───────────────────────────────────────┤
        │  • Bulk insert operations             │
        │  • Gap detection                      │
        │  • Status queries                     │
        └───────────────┬───────────────────────┘
                        ▼
        ┌──────────────────────────────────────┐
        │  PostgreSQL + TimescaleDB             │
        │  ohlcv hypertable                     │
        ├───────────────────────────────────────┤
        │  • Unique constraint on (time, symbol,│
        │    exchange, timeframe)               │
        │  • Indexes for performance            │
        │  • Compression (7-day policy)         │
        │  • Retention (365-day policy)         │
        └───────────────────────────────────────┘
```

## Files

### Core Implementation

- **`app/repositories/ohlcv_repository.py`** — Database access layer
  - Bulk insert with UPSERT
  - Gap detection
  - Status queries (earliest, latest, count)

- **`app/services/ohlcv_backfill_service.py`** — Backfill orchestration
  - Async HTTP with exponential backoff
  - Chunk-based fetching (backwards in time)
  - Data validation
  - Parallel symbol processing

- **`app/tasks/ohlcv_backfill.py`** — Celery task wrapper
  - `backfill()` — Main backfill task
  - `get_status()` — Status check task

### Infrastructure

- **`alembic/versions/024_ohlcv_backfill_constraints.py`** — Migration
  - Unique constraint: `(time, symbol, exchange, timeframe)`
  - Indexes: `(symbol, exchange, timeframe, time)`, `(timeframe, symbol, time)`
  - TimescaleDB compression policy (7 days)
  - TimescaleDB retention policy (365 days)

- **`scripts/backfill.py`** — CLI tool
  - Manual backfill execution
  - Status checking
  - Universe symbol fetching

## Usage

### 1. CLI (Manual Execution)

```bash
# Backfill specific symbols
python backend/scripts/backfill.py \
  --symbols BTC_USDT ETH_USDT SOL_USDT \
  --timeframes 1h 5m \
  --days 180

# Backfill all universe symbols (top 100 by volume)
python backend/scripts/backfill.py \
  --all \
  --timeframes 1h \
  --days 180 \
  --max-parallel 3

# Check backfill status
python backend/scripts/backfill.py --status --timeframes 1h 5m

# Check status for specific symbols
python backend/scripts/backfill.py \
  --status \
  --symbols BTC_USDT ETH_USDT \
  --timeframes 1h
```

### 2. Celery Task (Programmatic)

```python
from app.tasks.ohlcv_backfill import backfill, get_status

# Trigger backfill task
result = backfill.delay(
    symbols=["BTC_USDT", "ETH_USDT"],
    timeframes=["1h", "5m"],
    days=180,
    max_parallel=3,
)

# Or use universe symbols (auto-fetch)
result = backfill.delay(
    symbols=None,  # Will fetch from universe
    timeframes=["1h"],
    days=180,
)

# Check status
status_result = get_status.delay(
    symbols=None,  # Will fetch from universe
    timeframe="1h",
    target_days=180,
)
```

### 3. Python API (Direct)

```python
from app.database import CeleryAsyncSessionLocal as AsyncSessionLocal
from app.services.ohlcv_backfill_service import OHLCVBackfillService

async def backfill_example():
    async with AsyncSessionLocal() as db:
        service = OHLCVBackfillService(
            session=db,
            exchange="gate.io",
            max_concurrent=5,
            rate_limit_delay=0.5,
        )

        # Backfill single symbol
        result = await service.backfill_symbol(
            symbol="BTC_USDT",
            timeframe="1h",
            days=180,
            chunk_size=1000,
        )
        print(result)

        # Backfill multiple symbols
        results = await service.backfill_multiple_symbols(
            symbols=["BTC_USDT", "ETH_USDT"],
            timeframe="1h",
            days=180,
            max_parallel=3,
        )

        # Check status
        status = await service.get_backfill_status(
            symbols=["BTC_USDT"],
            timeframe="1h",
            target_days=180,
        )
```

## Configuration

### Service Parameters

- **`exchange`** — Exchange identifier (default: `"gate.io"`)
- **`max_concurrent`** — Max concurrent HTTP requests per service instance (default: `5`)
- **`rate_limit_delay`** — Delay between requests in seconds (default: `0.5`)

### Backfill Parameters

- **`days`** — Number of days to backfill (default: `180`)
- **`chunk_size`** — Candles per API request (default: `1000`)
- **`max_parallel`** — Max symbols to process in parallel (default: `3`)

### Timeframes Supported

- `1m` — 1 minute
- `5m` — 5 minutes
- `15m` — 15 minutes
- `1h` — 1 hour (mandatory)
- `4h` — 4 hours
- `1d` — 1 day

## Database Schema

### OHLCV Table

```sql
CREATE TABLE ohlcv (
  time TIMESTAMPTZ NOT NULL,
  symbol VARCHAR(20) NOT NULL,
  exchange VARCHAR(50) NOT NULL,
  timeframe VARCHAR(10) NOT NULL,
  open DECIMAL(20,8) NOT NULL,
  high DECIMAL(20,8) NOT NULL,
  low DECIMAL(20,8) NOT NULL,
  close DECIMAL(20,8) NOT NULL,
  volume DECIMAL(20,4) NOT NULL,
  quote_volume DECIMAL(20,4) NOT NULL,

  CONSTRAINT uq_ohlcv_time_symbol_exchange_timeframe
    UNIQUE (time, symbol, exchange, timeframe)
);

-- Indexes
CREATE INDEX idx_ohlcv_symbol_exchange_timeframe_time
  ON ohlcv (symbol, exchange, timeframe, time DESC);

CREATE INDEX idx_ohlcv_timeframe_symbol_time
  ON ohlcv (timeframe, symbol, time DESC);
```

### TimescaleDB Configuration

- **Hypertable** — `ohlcv` (partitioned by `time`)
- **Compression** — Enabled for chunks older than 7 days
  - Segment by: `symbol, exchange, timeframe`
  - Order by: `time DESC`
- **Retention** — Automatically drop data older than 365 days

## Features

### 1. Idempotency (Safe Re-runs)

The unique constraint on `(time, symbol, exchange, timeframe)` ensures that:
```sql
INSERT INTO ohlcv (...)
VALUES (...)
ON CONFLICT (time, symbol, exchange, timeframe) DO NOTHING
```

This allows you to re-run backfills without creating duplicates. If the backfill is interrupted, simply re-run — existing data will be skipped.

### 2. Gap Detection & Resume

The service automatically checks for existing data:
- Queries `MIN(time)` to find earliest data
- If earliest is after target start date, backfills the gap
- If data already exists for the full period, skips the symbol

### 3. Rate Limiting & Retry

- **429 Detection** — Respects `Retry-After` header
- **Exponential Backoff** — 1s, 2s, 4s delays for transient errors
- **Server Error Retry** — Automatic retry for 5xx errors
- **Max Retries** — Configurable per-symbol (default: 3)

### 4. Data Validation

Each candle is validated before insertion:
- OHLC relationship: `low ≤ open ≤ high`, `low ≤ close ≤ high`, `low ≤ high`
- Positive values: `open, high, low, close, volume, quote_volume > 0`
- Valid timestamp: `time` is a datetime object

### 5. Parallel Processing

- **Symbol-level parallelism** — Process multiple symbols concurrently
- **Semaphore-controlled** — Prevents overwhelming the API
- **Independent sessions** — Each symbol gets its own database transaction

## Monitoring & Logging

All operations are logged with structured context:

```
[BACKFILL] Starting BTC_USDT 1h - 180 days
[BACKFILL] BTC_USDT 1h - fetched 1000 candles, oldest: 2025-11-01 00:00:00+00:00
[BACKFILL] BTC_USDT 1h - processed 18000 records, inserted 17856
[BACKFILL] BTC_USDT 1h complete - duration: 45.2s
```

Errors are logged with full context:
```
[BACKFILL] Rate limited for ETH_USDT, retry after 5s (attempt 1/3)
[BACKFILL] Server error for SOL_USDT (attempt 2/3), retrying in 2s
[BACKFILL] Failed to fetch ADA_USDT after 3 attempts: Connection timeout
```

## Performance

### Benchmarks (approximate)

- **Single symbol, 1h, 180 days** — ~15-30 seconds (4,320 candles)
- **Single symbol, 5m, 180 days** — ~60-120 seconds (51,840 candles)
- **100 symbols, 1h, 180 days** — ~30-60 minutes (432,000 candles)
- **100 symbols, 5m, 180 days** — ~3-6 hours (5,184,000 candles)

### Optimization Tips

1. **Increase `max_parallel`** — Process more symbols concurrently (respect API limits)
2. **Increase `rate_limit_delay`** — Reduce chance of hitting rate limits
3. **Use chunking** — Keep `chunk_size` at 1000 for best performance
4. **Monitor compression** — TimescaleDB compression reduces storage by 90%+

## Troubleshooting

### "Rate limited" errors

Increase `rate_limit_delay` to 1.0 or higher:
```python
service = OHLCVBackfillService(session=db, rate_limit_delay=1.0)
```

### "No data returned" warnings

Some symbols may not have historical data on Gate.io. This is normal for newly listed assets.

### "Invalid OHLC" warnings

Rare data quality issues from the exchange. These candles are automatically skipped.

### Database connection errors

Ensure PostgreSQL and Redis are running:
```bash
docker-compose up -d postgres redis
```

### Migration not applied

Run Alembic migration:
```bash
cd backend
alembic upgrade head
```

## Migration Path

### Applying the Migration

```bash
cd backend
alembic upgrade head
```

This creates:
- Unique constraint for idempotency
- Indexes for performance
- TimescaleDB compression policy
- TimescaleDB retention policy

### Rollback (if needed)

```bash
alembic downgrade -1
```

## Maintenance

### Monitoring Data Growth

```sql
-- Check table size
SELECT pg_size_pretty(pg_total_relation_size('ohlcv'));

-- Check compression ratio
SELECT
  hypertable_name,
  total_chunks,
  number_compressed_chunks,
  pg_size_pretty(before_compression_total_bytes) AS before,
  pg_size_pretty(after_compression_total_bytes) AS after,
  round((1 - after_compression_total_bytes::float / before_compression_total_bytes) * 100, 2) AS compression_ratio_pct
FROM timescaledb_information.compression_stats
WHERE hypertable_name = 'ohlcv';
```

### Manual Compression (if needed)

```sql
-- Compress specific chunk
SELECT compress_chunk('_timescaledb_internal._hyper_X_Y_chunk');

-- Compress all eligible chunks
SELECT compress_chunk(c)
FROM show_chunks('ohlcv', older_than => INTERVAL '7 days') c;
```

### Adjusting Retention Policy

```sql
-- Change retention to 730 days (2 years)
SELECT remove_retention_policy('ohlcv');
SELECT add_retention_policy('ohlcv', INTERVAL '730 days');
```

## Next Steps

1. **Run initial backfill** — Populate historical data for top 100 symbols
2. **Schedule periodic backfills** — Add to Celery beat schedule if needed
3. **Monitor compression** — Verify TimescaleDB compression is working
4. **Tune parameters** — Adjust `max_parallel` and `rate_limit_delay` based on API quotas

## Support

For issues or questions, check:
- Backend logs: `docker-compose logs -f backend`
- Celery logs: `docker-compose logs -f celery`
- Database logs: `docker-compose logs -f postgres`
