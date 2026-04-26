#!/bin/bash
# Scalpyn backend startup script
#
# Strategy: schema bootstrap is AUTHORITATIVE on this script.  Two gates run
# before uvicorn / Celery:
#   1. `alembic upgrade head` (3 retries, exit 1 on persistent failure)
#   2. `python -m app.init_db` (single attempt, exit 1 on failure)
#
# If either gate fails, the container exits non-zero — Cloud Run will roll
# back to the previous revision and surface a real error instead of serving
# traffic with a half-broken schema.  This eliminates the silent-failure mode
# that caused two production incidents (Task #41: market_mode missing,
# follow-up: last_scanned_at missing) where /api/watchlists returned 500 and
# the UI hid the failure.
#
# The /api/health/schema endpoint independently probes information_schema for
# critical columns, so external monitors can detect drift even if both gates
# unexpectedly skip.

set -e

# Tell the FastAPI lifespan to skip its own init_db() call — start.sh owns
# bootstrap now and double-running it just slows boot.
export SKIP_LIFESPAN_INIT_DB=1

# ── Gate 1: Alembic migrations (authoritative) ───────────────────────────────
run_alembic_upgrade() {
    local max_attempts=3
    local delay=5
    local attempt=1

    echo "==> [migrations] alembic upgrade head"

    # Stamp empty alembic_version on a DB that was create_all'd before alembic
    # was introduced.  Without this, alembic refuses to run "fresh" migrations
    # against a populated DB.
    python - <<'PYEOF'
import os, sys
try:
    from sqlalchemy import create_engine, text, inspect
    from alembic.config import Config
    from alembic import command

    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        print(" [migrations] DATABASE_URL not set -- aborting")
        sys.exit(1)

    sync_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
    engine = create_engine(sync_url, connect_args={"connect_timeout": 10})
    with engine.connect() as conn:
        insp = inspect(conn)
        has_alembic = insp.has_table("alembic_version")
        has_pools = insp.has_table("pools")
        versions = []
        if has_alembic:
            versions = [r[0] for r in conn.execute(text("SELECT version_num FROM alembic_version"))]
    engine.dispose()

    if has_pools and not versions:
        print(" [migrations] DB has tables but no alembic_version -- stamping at base.")
        cfg = Config("/app/alembic.ini")
        command.stamp(cfg, "base")
    elif versions:
        print(f" [migrations] Current revision(s): {versions}")
    else:
        print(" [migrations] Fresh DB -- alembic will run all migrations.")
except Exception as e:
    print(f" [migrations] Stamp pre-check failed: {e}", file=sys.stderr)
    # Do not exit 1 here — alembic upgrade itself is the real gate.
PYEOF

    while [ $attempt -le $max_attempts ]; do
        echo " [migrations] attempt $attempt/$max_attempts ..."
        if alembic upgrade head; then
            echo "==> [migrations] alembic upgrade head OK"
            return 0
        fi
        echo " [migrations] attempt $attempt failed -- retry in ${delay}s"
        sleep $delay
        delay=$((delay * 2))
        attempt=$((attempt + 1))
    done

    echo "==> [migrations] FATAL: alembic upgrade head failed after ${max_attempts} attempts" >&2
    return 1
}

if ! run_alembic_upgrade; then
    echo "==> Aborting startup: schema migrations failed.  Cloud Run will roll back." >&2
    exit 1
fi

# ── Gate 2: init_db.py safety net ────────────────────────────────────────────
# Even with alembic at head, init_db.py runs as a redundant idempotent
# safety net.  Its CRITICAL blocks raise on failure, so a non-zero exit means
# real schema mismatch.
echo "==> [init_db] python -m app.init_db"
if ! python -m app.init_db; then
    echo "==> Aborting startup: init_db failed.  Cloud Run will roll back." >&2
    exit 1
fi
echo "==> [init_db] OK"

# ── Start Celery worker ──────────────────────────────────────────────────────
echo "==> Starting Celery worker..."
celery -A app.tasks.celery_app worker \
    --loglevel=info \
    --concurrency="${CELERY_CONCURRENCY:-1}" \
    --queues=celery \
    &
CELERY_WORKER_PID=$!
echo " Celery worker PID: $CELERY_WORKER_PID"

# ── Start Celery beat ────────────────────────────────────────────────────────
echo "==> Starting Celery beat..."
celery -A app.tasks.celery_app beat \
    --loglevel=info \
    &
CELERY_BEAT_PID=$!
echo " Celery beat PID: $CELERY_BEAT_PID"

# Capture the PID that exec uvicorn will inherit (shell PID becomes uvicorn PID)
MAIN_PID=$$

# Grace period (seconds) before watchdog starts checking Celery health.
# This allows Celery to retry its Redis connection on startup without
# triggering an immediate container shutdown.
WATCHDOG_GRACE=${WATCHDOG_GRACE:-120}

# Watchdog: wait for grace period, then monitor Celery health.
# Only kill uvicorn if Celery is STILL dead after retries.
(
    echo " [watchdog] Waiting ${WATCHDOG_GRACE}s grace period before monitoring Celery..."
    sleep "$WATCHDOG_GRACE"
    echo " [watchdog] Grace period over -- monitoring Celery processes."

    while sleep 30; do
        WORKER_OK=true
        BEAT_OK=true

        if ! kill -0 "$CELERY_WORKER_PID" 2>/dev/null; then
            WORKER_OK=false
        fi
        if ! kill -0 "$CELERY_BEAT_PID" 2>/dev/null; then
            BEAT_OK=false
        fi

        if [ "$WORKER_OK" = false ] || [ "$BEAT_OK" = false ]; then
            echo "WARNING: Celery process down (worker=$WORKER_OK beat=$BEAT_OK) -- shutting down container"
            kill -TERM "$MAIN_PID" 2>/dev/null
            break
        fi
    done
) &

# Graceful cleanup: when uvicorn/container receives SIGTERM, stop children first
cleanup() {
    echo "==> SIGTERM received -- stopping background processes..."
    kill -TERM "$CELERY_WORKER_PID" "$CELERY_BEAT_PID" 2>/dev/null
    wait "$CELERY_WORKER_PID" "$CELERY_BEAT_PID" 2>/dev/null
}
trap cleanup TERM INT

# ── Start uvicorn (schema is already up-to-date) ────────────────────────────
echo "==> Starting uvicorn..."
exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port "${PORT:-8080}" \
    --workers "${WEB_CONCURRENCY:-2}"
