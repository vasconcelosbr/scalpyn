#!/bin/sh
# Scalpyn backend startup script
#
# Strategy: start uvicorn IMMEDIATELY so Cloud Run startup probe passes,
# then run Alembic migrations and Celery in the background.
# All migrations use IF NOT EXISTS so they are safe to run concurrently.

set -e

# ── Background migration runner ────────────────────────────────────────────
run_migrations() {
    local max_attempts=5
    local delay=3
    local attempt=1

    echo "==> [migrations] Checking alembic state..."

    python - <<'PYEOF'
import os, sys
try:
    from sqlalchemy import create_engine, text, inspect
    from alembic.config import Config
    from alembic import command

    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        print("  [migrations] DATABASE_URL not set — skipping stamp check")
        sys.exit(0)

    sync_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
    engine = create_engine(sync_url, connect_args={"connect_timeout": 10})

    with engine.connect() as conn:
        insp = inspect(conn)
        has_alembic = insp.has_table("alembic_version")
        versions = []
        if has_alembic:
            result = conn.execute(text("SELECT version_num FROM alembic_version"))
            versions = [r[0] for r in result]
        has_pools = insp.has_table("pools")
    engine.dispose()

    if has_pools and not versions:
        print("  [migrations] Stamping alembic to base revision (create_all DB)...")
        cfg = Config("/app/alembic.ini")
        command.stamp(cfg, "base")
        print("  [migrations] Stamped at base.")
    elif versions:
        print(f"  [migrations] Current revision(s): {versions}")
    else:
        print("  [migrations] Fresh database — running all migrations.")
except Exception as e:
    print(f"  [migrations] Stamp check failed ({e}) — proceeding with upgrade anyway")
PYEOF

    while [ $attempt -le $max_attempts ]; do
        echo "  [migrations] alembic upgrade head (attempt $attempt/$max_attempts)..."
        if alembic upgrade head; then
            echo "==> [migrations] Complete."
            return 0
        fi
        echo "  [migrations] Failed — retrying in ${delay}s..."
        sleep $delay
        attempt=$((attempt + 1))
    done
    echo "  [migrations] WARNING: all attempts failed — server running without latest schema."
    return 0
}

# ── Run migrations in the background ─────────────────────────────────────
run_migrations &
MIGRATIONS_PID=$!

# ── Start Celery worker ───────────────────────────────────────────────────
echo "==> Starting Celery worker..."
celery -A app.tasks.celery_app worker \
    --loglevel=info \
    --concurrency="${CELERY_CONCURRENCY:-2}" \
    --queues=celery \
    &
CELERY_WORKER_PID=$!
echo "  Celery worker PID: $CELERY_WORKER_PID"

# ── Start Celery beat ─────────────────────────────────────────────────────
echo "==> Starting Celery beat..."
celery -A app.tasks.celery_app beat \
    --loglevel=info \
    &
CELERY_BEAT_PID=$!
echo "  Celery beat PID: $CELERY_BEAT_PID"

# Capture the PID that exec uvicorn will inherit (shell PID becomes uvicorn PID)
MAIN_PID=$$

# Watchdog: if Celery worker or beat exits unexpectedly, signal uvicorn to shut
# down so Cloud Run restarts the container and recovers the trading pipeline.
( while sleep 30; do
    if ! kill -0 "$CELERY_WORKER_PID" 2>/dev/null; then
        echo "ERROR: Celery worker (PID $CELERY_WORKER_PID) exited — shutting down container"
        kill -TERM "$MAIN_PID" 2>/dev/null
        break
    fi
    if ! kill -0 "$CELERY_BEAT_PID" 2>/dev/null; then
        echo "ERROR: Celery beat (PID $CELERY_BEAT_PID) exited — shutting down container"
        kill -TERM "$MAIN_PID" 2>/dev/null
        break
    fi
done ) &

# Graceful cleanup: when uvicorn/container receives SIGTERM, stop children first
cleanup() {
    echo "==> SIGTERM received — stopping background processes..."
    kill -TERM "$CELERY_WORKER_PID" "$CELERY_BEAT_PID" "$MIGRATIONS_PID" 2>/dev/null
    wait "$CELERY_WORKER_PID" "$CELERY_BEAT_PID" 2>/dev/null
}
trap cleanup TERM INT

# ── Start uvicorn immediately (port 8080 must bind before Cloud Run probe) ──
echo "==> Starting uvicorn (migrations running in background)..."
exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port "${PORT:-8080}" \
    --workers "${WEB_CONCURRENCY:-2}"
