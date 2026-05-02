# Database Connection Pool Budget

## Connection Budget Formula

Each uvicorn worker process holds its own SQLAlchemy `QueuePool`.
Celery worker(s) and beat each use a `NullPool` engine (one connection per
active task, returned immediately after use).

```
total_ceiling = uvicorn_workers × (pool_size + max_overflow)
              + celery_workers
              + 1  (celery beat)
```

`pool_size` connections are kept open permanently; `max_overflow` extra
connections are borrowed on demand and closed after use.  A new connection
request that arrives while all `pool_size + max_overflow` slots are occupied
blocks for up to `pool_timeout` seconds before raising `QueuePool limit of
size X reached, connection timed out`.

---

## Current Production Numbers

| Parameter | Value | Source |
|-----------|-------|--------|
| `DB_POOL_SIZE` | **5** | `backend/app/database.py` default (reduced from 10 in Task #160) |
| `DB_MAX_OVERFLOW` | **5** | `backend/app/database.py` default (reduced from 10 in Task #160) |
| `DB_POOL_TIMEOUT` | 30 s | `backend/app/database.py` default |
| `WEB_CONCURRENCY` (uvicorn workers) | **2** | `backend/Dockerfile` runtime stage `ENV WEB_CONCURRENCY=2` and `backend/start.sh` default |
| `CELERY_CONCURRENCY` (Celery task slots) | 1 | `backend/start.sh` `--concurrency="${CELERY_CONCURRENCY:-1}"` |
| Celery beat | 1 | always 1 |

> **`CELERY_CONCURRENCY` vs `CELERY_WORKERS`**: Celery uses `NullPool`
> (each active task opens and closes its own connection).  The number of
> simultaneous Celery DB connections therefore equals the task-slot concurrency
> (`--concurrency` flag / `CELERY_CONCURRENCY` env var in `start.sh`), **not**
> the number of Celery processes.  The `database.py` budget reads
> `CELERY_CONCURRENCY` to match `start.sh`.

```
total_ceiling = 2 × (5 + 5) + 1 + 1 = 22 connections
```

| Cloud SQL tier | `max_connections` | Headroom |
|----------------|-------------------|----------|
| `db-f1-micro`  | 25                | 3 (tight — not recommended) |
| `db-g1-small`  | 50                | **28**   |
| `db-n1-standard-1` | 100          | 78       |

> **Note:** Pool defaults were reduced from 10+10 to 5+5 in Task #160 to fit
> comfortably within `db-g1-small` (28-connection headroom vs 8 previously).
> If request-handler latency increases under load, raise `DB_POOL_SIZE` and
> `DB_MAX_OVERFLOW` via Cloud Run env vars — always verify headroom with
> `SHOW max_connections;` first.

### Note on `cloud-run-job.yaml`

`cloud-run-job.yaml` defines the `bootstrap-simulations` one-off job, **not**
the API server process.  It does not set `WEB_CONCURRENCY` or
`CELERY_CONCURRENCY`, so those env vars default to values inherited from the
container image or Cloud Run job configuration.  The bootstrap job is a
short-lived batch script — it does not start uvicorn workers or Celery — so
its DB connections are not included in the formula above and do not compete
with the API server pool.

The computed budget is printed at startup:

```
INFO DB pool configured: pool_size=5 max_overflow=5 pool_timeout=30s |
     connection budget: uvicorn_workers=2 × (pool_size+max_overflow)=10
     + celery_concurrency=1 + beat=1 = 22 total ceiling
```

---

## Pool Saturation Warnings

`log_pool_stats()` in `backend/app/database.py` emits a `WARNING` (once per
state transition, not on every tick) in two situations:

| Condition | Message keyword | Meaning |
|-----------|-----------------|---------|
| `checked_out >= pool_size` | `DB pool SATURATED` | Overflow connections are now in use |
| `overflow >= max_overflow` | `DB pool OVERFLOW EXHAUSTED` | Next request will block until `pool_timeout` |

Both messages include the computed `budget_ceiling` so you can correlate
against `SHOW max_connections;` in Cloud Logging without a histogram query.

---

## Silent DB Failure Audit

The table below lists every `except Exception` site that touched the database
and documents the decision taken during this audit (Task #133, 2026-05-01).

| File | Function | Old behaviour | Decision | Rationale |
|------|----------|---------------|----------|-----------|
| `services/simulation_service.py` | `get_simulation_config` | `WARNING` + silent fallback to defaults | **Now `ERROR` + `exc_info=True`** | Genuine DB failure; fallback hides outages |
| `services/ai_keys_service.py` | `get_ai_key_info` | `WARNING` + return `None` | **Now `ERROR` + `exc_info=True`** | DB read failure; `None` return kept (non-breaking endpoint contract) |
| `services/pipeline_scheduler_service.py` | `_count_active_watchlists` | `DEBUG` + return `None` | **Now `ERROR` + `exc_info=True`** | Pre-scan COUNT query failure; `None` sentinel kept (cycle still runs, only the log line is affected) |
| `services/scheduler_service.py` | `_persist_ohlcv` (per-row insert) | `DEBUG` ("skipped") | **Now `ERROR` + `exc_info=True`** | DB insert failure losing OHLCV data; `ON CONFLICT DO NOTHING` already handles duplicates without raising |
| `services/scheduler_service.py` | `_persist_indicators` | `WARNING` | **Now `ERROR` + `exc_info=True`** | DB write failure losing indicator data |
| `services/scheduler_service.py` | `_refresh_market_metadata` | `WARNING` | **Now `ERROR` + `exc_info=True`** | DB upsert failure losing market metadata |
| `services/structural_scheduler_service.py` | `_persist_indicators` | `WARNING` | **Now `ERROR` + `exc_info=True`** | Same as above |
| `services/structural_scheduler_service.py` | `_refresh_market_metadata` | `DEBUG` ("skipped") | **Now `ERROR` + `exc_info=True`** | SAVEPOINT isolates the failure but it should not be silent |
| `services/microstructure_scheduler_service.py` | `_persist_indicators` | `WARNING` | **Now `ERROR` + `exc_info=True`** | Same as structural |
| `services/microstructure_scheduler_service.py` | `_refresh_market_metadata` | `DEBUG` ("skipped") | **Now `ERROR` + `exc_info=True`** | Same as structural |
| `services/preset_ia_service.py` | `run_preset_ia` (Skill DB lookup) | `WARNING` | **Now `ERROR` + `exc_info=True`** | DB read failure; falls back to default prompt |
| `services/trade_sync_service.py` | `sync_trades` (commit) | `ERROR` without `exc_info` | **Now `ERROR` + `exc_info=True`** | Already correct level; added stack trace |
| `services/portfolio_service.py` | `get_portfolio` | `WARNING` + Gate API fallback | **Kept as-is (WARNING)** | Catches Gate exchange API failure (not a DB failure); DB fallback is intentional |
| `services/config_service.py` | `get_config`, `update_config` | `WARNING` on Redis ops | **Kept as-is (WARNING)** | Catches Redis cache failures, not DB failures; DB calls propagate naturally |
| `services/trade_sync_service.py` | `_fetch_all_closed_orders` | `WARNING` on page fetch | **Kept as-is (WARNING)** | Catches Gate exchange API pagination; not a DB operation |
| `services/ai_keys_service.py` | `get_decrypted_api_key` | Silent `return None` | **Kept as-is** | Catches Fernet decryption error; not a DB failure |
| `services/preset_ia_service.py` | `_get_market_snapshot` | `WARNING` | **Kept as-is (WARNING)** | Catches market data API failure; not a DB operation |

---

## Tuning Checklist (before changing pool numbers)

1. Run `SHOW max_connections;` on the live Cloud SQL instance.
2. Update the formula table above with the new numbers.
3. Ensure `total_ceiling < max_connections - 5` (5 spare for admin/monitoring).
4. Deploy and confirm the startup log prints the expected budget.
5. Monitor `DB pool SATURATED` / `DB pool OVERFLOW EXHAUSTED` warnings in
   Cloud Logging for at least one full load cycle before raising further.
