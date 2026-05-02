# Runbook — `indicators.scheduler_group` schema drift

## Symptom

Sentry shows tens of thousands of recurring errors per day:

- `asyncpg.exceptions.UndefinedColumnError: column "scheduler_group" of relation "indicators" does not exist`
- `sqlalchemy.exc.InFailedSQLTransactionError: current transaction is aborted, commands ignored until end of transaction block`
- `QueuePool limit of size 5 overflow N reached, connection timed out, timeout 30.00`
- `asyncpg/protocol/protocol.pyx:165 .prepare`

Cloud Run logs include `==> [migrations] Stamped at head.` (i.e. the start.sh fallback fired) shortly before the errors begin.

## Root cause

Migration `032_add_indicators_scheduler_group.py` adds the column to `indicators`. The column is referenced unconditionally by both indicator schedulers (`structural_scheduler_service._persist_indicators` and `microstructure_scheduler_service._persist_indicators`).

When `alembic upgrade head` fails three times in a row (typically lock contention from the previous revision's Celery beat during a rolling Cloud Run deploy), `start.sh` falls back to `alembic stamp head`. **That writes `032` to `alembic_version` but never executes the `ALTER TABLE`**, so the column stays missing and every scheduler cycle fails for every symbol.

Since 2026-05-02 the start script invokes `python3 -m scripts.check_critical_schema` after the stamp fallback and aborts the boot when this drift is detected — Cloud Run rolls back to the previous revision instead of running silently broken. If you are reading this runbook, you are most likely in production where the gate has not been deployed yet, or the gate already fired and you need to fix the database before the next deploy.

## Verification

Run these queries against the production Cloud SQL instance (Cloud SQL Studio, `gcloud sql connect`, or a bastion):

```sql
-- 1. What does alembic think the head is?
SELECT version_num FROM alembic_version;
-- expected: '032'

-- 2. Does the column actually exist?
SELECT column_name
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'indicators'
  AND column_name = 'scheduler_group';
-- expected: one row 'scheduler_group'; zero rows = drift confirmed
```

If query 1 returns `032` and query 2 returns no rows, the drift is real.

You can also hit the running container at `GET /api/health/schema` — drift returns HTTP 503 with `missing` listing `{"table":"indicators","column":"scheduler_group"}`.

## Fix — apply the missing DDL manually

Both statements are exactly the body of `migration 032`'s `upgrade()` function and use `IF NOT EXISTS` guards, so they are safe to run even after the migration eventually executes on a future cold start.

```sql
SET LOCAL lock_timeout = '10s';

ALTER TABLE indicators
    ADD COLUMN IF NOT EXISTS scheduler_group VARCHAR(20) DEFAULT 'combined';

CREATE INDEX IF NOT EXISTS ix_indicators_symbol_group_time
    ON indicators (symbol, scheduler_group, time DESC);
```

`indicators` is a TimescaleDB hypertable; `ALTER TABLE … ADD COLUMN IF NOT EXISTS` propagates to all existing and future chunks automatically (TimescaleDB ≥ 2.x). The non-NULL `DEFAULT 'combined'` back-fills existing rows without rewriting the table.

The lock-timeout guard prevents the ALTER from blocking on a long-running query against the hypertable; if it expires, retry during a quieter window.

## Post-fix validation (within 30 minutes)

1. **Schema gate** — `curl https://<cloud-run-url>/api/health/schema` returns HTTP 200 with `"schema_ok": true` and `"missing": []`.
2. **Sentry** — the four error groups (`UndefinedColumnError column scheduler_group`, `InFailedSQLTransactionError`, `asyncpg ... .prepare`, `QueuePool limit`) stop incrementing. Filter by "Last seen ≤ 5 min" — the count should plateau.
3. **Backend logs** — `[STRUCT-SCHED] SCHEMA DRIFT` and `[MICRO-SCHED] SCHEMA DRIFT` no longer appear in subsequent boots. `[STRUCT-SCHED] indicators insert failed` and `[MICRO-SCHED] indicators insert failed` drop to zero.
4. **Pool config** — confirm `pool_size=5 max_overflow=5 pool_timeout=30s` in the boot log (line `DB pool configured: …`). If `max_overflow` is not 5, check the Cloud Run service definition for a `DB_MAX_OVERFLOW` env-var override and remove it (Task #160 is the canonical config).
5. **Connection budget** — `DB pool stats: …` log lines (every 60s) show `checked_out` well below `pool_size + max_overflow` after the cascade clears. The `DB pool SATURATED` and `DB pool OVERFLOW EXHAUSTED` warnings stop firing.

## Why we don't auto-apply the DDL on boot

The `init_db.py` "best-effort" `ALTER TABLE … IF NOT EXISTS` block at lines 323-340 runs only when `SKIP_LIFESPAN_INIT_DB` is unset — which production explicitly sets to `1` so start.sh owns bootstrap. Adding a second auto-DDL path would re-introduce the silent failure mode that migrations are supposed to prevent. The correct fix is to make the alembic upgrade succeed (resolve the lock contention) or apply the DDL manually; the schema gate ensures we know about drift in seconds rather than days.

## Related

- Task #178 — this runbook + the schema gate + scheduler defensive logging.
- Task #164 — created migration 032.
- Task #160 — pool budget reduction (`5+5`, was `10+10`).
- `backend/alembic/versions/032_add_indicators_scheduler_group.py`
- `backend/start.sh` (stamp-fallback + post-stamp gate)
- `backend/scripts/check_critical_schema.py`
- `backend/app/main.py::health_check_schema`
- `.agents/skills/alembic-migration-guardrails/SKILL.md`
