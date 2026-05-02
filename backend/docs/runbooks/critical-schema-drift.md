# Critical schema drift — manual recovery runbook

## When this runbook applies

The Cloud Run deploy fails with the generic message:

> `The user-provided container failed to start and listen on the port defined provided by the PORT=8080 environment variable within the allocated timeout.`

…AND the revision logs (Cloud Logging → resource `cloud_run_revision` → log
names `…/stdout` and `…/stderr` of the failed revision) contain:

```
==> [migrations] alembic upgrade head OK
==> [schema] Validating critical schema...
FATAL: critical schema drift - <N> of 22 columns missing:
  - <table>.<column>
==> Aborting startup: critical schema drift detected.
```

This means `alembic_version.version_num` claims a revision is applied, but
the DDL it should have run is not present in `information_schema.columns`.
The boot gate `scripts.check_critical_schema` (invoked by `start.sh`) exits 1
and Cloud Run rolls back the revision.

## Root cause (why drift happens despite alembic_version being current)

`backend/alembic/env.py` sets `lock_timeout = '10s'`. During a rolling deploy,
Cloud Run keeps the previous revision alive (`--min-instances=1` in
`cloudbuild.yaml`) so its Celery beat + OHLCV collector hold continuous write
locks on the hot hypertables (`ohlcv`, `indicators`, `decisions_log`,
`trades`, `pipeline_watchlist_assets`).

When the new revision's `alembic upgrade head` reaches an `ALTER TABLE` on
one of these tables, it contests the lock, `lock_timeout` fires after 10 s,
the migration aborts, `start.sh` retries 3 times then falls through to
`alembic stamp head` — which writes the revision id to `alembic_version`
**without running any DDL**. The container exits or the next cold start
discovers the drift via `validate_critical_schema`.

This has happened three times so far (May 2026):

| Migration | Column                          | Aplicado manual em |
|----------:|:--------------------------------|:-------------------|
| 032       | `indicators.scheduler_group`    | 2026-05-02         |
| 033       | `indicators.market_type`        | 2026-05-02         |
| 034       | `ohlcv.market_type`             | 2026-05-02         |

See skill `.agents/skills/alembic-migration-guardrails/SKILL.md` invariants
#7, #8 and #9 for the rollout discipline that prevents this going forward.

## Recovery — apply the missing DDL manually

### Step 1: confirm exactly which columns are missing

Easiest path — run the auditor from any machine with prod read credentials
(does NOT mutate the DB):

```bash
cd backend
DATABASE_URL='postgresql://...' python3 -m scripts.audit_prod_schema
```

It prints `alembic_version.version_num`, every missing critical column, and
for each missing column the migration file that originally introduced it.

Alternative — run by hand in Cloud SQL Studio (or any psql session against
the prod DB):

```sql
-- 1. The version alembic *thinks* it's at:
SELECT version_num FROM alembic_version;

-- 2. Probe every critical column. Adjust the (table, column) pairs to match
--    backend/app/_critical_schema.py::CRITICAL_COLUMNS — but the most common
--    drift cases are listed first.
SELECT table_name, column_name
FROM information_schema.columns
WHERE table_schema = 'public'
  AND (table_name, column_name) IN (
        ('indicators',  'scheduler_group'),
        ('indicators',  'market_type'),
        ('ohlcv',       'market_type'),
        ('decisions_log', 'direction'),
        ('decisions_log', 'event_type')
      );
```

Anything in `CRITICAL_COLUMNS` that is NOT returned by query #2 is missing.

### Step 2: apply the idempotent DDL

The original migration file is the source of truth — its `op.execute(...)`
blocks already use `IF NOT EXISTS` guards so they are safe to run by hand.
For the three known drift cases, the SQL is:

```sql
-- Migration 032 — indicators.scheduler_group
ALTER TABLE indicators
    ADD COLUMN IF NOT EXISTS scheduler_group VARCHAR(20) DEFAULT 'combined';
CREATE INDEX IF NOT EXISTS ix_indicators_symbol_group_time
    ON indicators (symbol, scheduler_group, time DESC);

-- Migration 033 — indicators.market_type
ALTER TABLE indicators
    ADD COLUMN IF NOT EXISTS market_type VARCHAR(10) DEFAULT 'spot';
CREATE INDEX IF NOT EXISTS ix_indicators_market_type_time
    ON indicators (market_type, time DESC);

-- Migration 034 — ohlcv.market_type
ALTER TABLE ohlcv
    ADD COLUMN IF NOT EXISTS market_type VARCHAR(10) DEFAULT 'spot';
-- The 034 index uses CREATE INDEX CONCURRENTLY in the migration.  Cloud SQL
-- Studio runs each statement in an implicit transaction, which is incompatible
-- with CONCURRENTLY.  Open a psql session instead:
--   psql "$DATABASE_URL" -c \
--     "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_ohlcv_market_type_time \
--      ON ohlcv (market_type, time DESC);"
```

For any other drift case, open `backend/alembic/versions/<NNN>_*.py`,
look at the body of `upgrade()`, and paste the `op.execute(sa.text("..."))`
contents directly — they are already idempotent in this repo by convention.

### Step 3: verify

```sql
-- Re-run the probe from Step 1; every (table, column) must now return.

-- Sanity-check the type and default of the freshly added column:
SELECT column_name, data_type, column_default
FROM information_schema.columns
WHERE table_name = '<table>' AND column_name = '<column>';
```

Then re-run the auditor:

```bash
DATABASE_URL='postgresql://...' python3 -m scripts.audit_prod_schema
# Expected: "OK — no drift detected.  Safe to push."
```

### Step 4: re-trigger the deploy

A trivial redeploy is enough — Cloud Run only needs another cold start so
the new revision can finally pass `validate_critical_schema`:

```bash
git commit --allow-empty -m "trigger redeploy after manual DDL"
git push
```

(Or click "Retry" on the failed Cloud Build.)

Expected `start.sh` log on the new revision:

```
==> [migrations] alembic upgrade head OK
==> [schema] Critical schema OK.
==> Starting Celery worker...
==> Starting Celery beat...
==> Starting uvicorn...
INFO:     Application startup complete.
```

### Step 5: post-fix validation

1. **Health probe** — `curl https://<service-url>/api/health/schema` returns
   200 with `{ "status": "ok" }`. A 503 with `{ "missing": [...] }` means
   you missed a column.
2. **Sentry / log volume** — within 5 minutes the rate of
   `UndefinedColumnError` / `column "X" of relation "Y" does not exist`
   should drop to zero. If it persists, you fixed the wrong column or the
   schedulers are still running on a cached connection (they self-recover
   on next cycle).
3. **Pool budget** — `GET /api/health/db` should report `checked_out` near
   zero. Drift cases produce `InFailedSQLTransactionError` cascades that
   poison sessions and starve the pool — this metric is the cleanest signal
   that the cascade is over.
4. **Scheduler write success** — for the in-process schedulers
   (`structural_scheduler_service`, `microstructure_scheduler_service`), one
   full cycle (up to 15 min) should complete without the boot-once `SCHEMA
   DRIFT` log line they emit on a known drift signature.

## Prevention

- **Auditor before every push** that touches `_critical_schema.py` or
  `backend/alembic/versions/`:
  `DATABASE_URL='<prod>' python3 -m scripts.audit_prod_schema`.
  Refuse to push if it reports drift.
- **Hot-table DDL gets pre-applied manually** before push — see invariant #8
  in the skill. The migration's `IF NOT EXISTS` guards then make the Cloud
  Run cold start a no-op.
- **`CRITICAL_COLUMNS` rollout in two deploys** — see invariant #7 in the
  skill. Migration first, `_critical_schema.py` entry second, never both.

## Why we cannot just "alembic downgrade" out of this

`alembic stamp head` left `alembic_version.version_num` pointing at a
revision whose DDL never ran. `alembic downgrade <prev>` would call the
`downgrade()` of that revision, which tries to `DROP COLUMN` a column that
does not exist (or `DROP INDEX` an index that was never created). On hot
tables the downgrade also contests the same locks that broke the original
upgrade. The manual `ALTER TABLE … ADD COLUMN IF NOT EXISTS` path is faster,
safer, and aligns the live schema with what `alembic_version` already
believes is true — no further alembic action required.
