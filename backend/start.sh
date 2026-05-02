#!/bin/bash
# Scalpyn backend startup script
#
# Strategy: Alembic is the AUTHORITATIVE schema gate.
#   1. `alembic upgrade head` (3 retries with backoff, time-boxed at 180s per
#      attempt, exit 1 on persistent failure).
#
# Migration 021 mirrors 1:1 every DDL in `backend/app/init_db.py`, so
# `alembic upgrade head` alone is enough to converge any production state to a
# known-good schema. `init_db.py` is kept as a dev-only convenience (lifespan
# runs it when SKIP_LIFESPAN_INIT_DB is unset).
#
# Defenses against the failure mode that broke the Task #44 deploy:
#   - asyncpg `server_settings` in alembic/env.py sets `lock_timeout=10s` and
#     `statement_timeout=60s` at session level, before any transaction. This
#     is more reliable than SET LOCAL inside alembic's transaction (which was
#     silently inert in production with asyncpg + run_sync + SQLAlchemy 2.0).
#     Migrations that try to ALTER a table held by the OLD revision's Celery
#     beat fail in 10s with a clear "lock timeout" error instead of blocking.
#   - `timeout 50s` per attempt (3 retries × 50s + 35s delays = 185s max) —
#     bounded wall-clock so the container never exceeds the Cloud Run startup
#     probe window (~240s). With lock_timeout firing at 10s, expected total
#     on contention is ~65s.
#
# If the gate fails, the container exits non-zero — Cloud Run rolls back to
# the previous revision automatically.  /api/health/schema independently
# probes information_schema for critical columns post-boot.

set -e

# Tell the FastAPI lifespan to skip its own init_db() call — start.sh owns
# bootstrap now and double-running it just slows boot.
export SKIP_LIFESPAN_INIT_DB=1

# ── Boot diagnostics ──────────────────────────────────────────────────────────
# Print PRESENT/MISSING for required env vars so deploy failures can be
# diagnosed from Cloud Run logs.  Never log values or lengths.
echo "==> [boot] Scalpyn backend starting (PORT=${PORT:-8080}, WEB_CONCURRENCY=${WEB_CONCURRENCY:-2})"
for var in DATABASE_URL JWT_SECRET ENCRYPTION_KEY REDIS_URL AI_KEYS_ENCRYPTION_KEY; do
    if [ -n "${!var}" ]; then
        echo " [boot] env $var: PRESENT"
    else
        echo " [boot] env $var: MISSING"
    fi
done

# ── Schema gate: Alembic migrations (authoritative, time-boxed) ──────────────
ALEMBIC_TIMEOUT_PER_ATTEMPT=${ALEMBIC_TIMEOUT_PER_ATTEMPT:-50}

run_alembic_upgrade() {
    local max_attempts=3
    local delay=5
    local attempt=1

    echo "==> [migrations] alembic upgrade head"

    while [ $attempt -le $max_attempts ]; do
        echo " [migrations] attempt $attempt/$max_attempts (timeout ${ALEMBIC_TIMEOUT_PER_ATTEMPT}s) ..."
        # `timeout` exits 124 on hard wall-clock expiry; treat that the same
        # as an alembic failure so the retry/backoff loop kicks in.
        if timeout "${ALEMBIC_TIMEOUT_PER_ATTEMPT}s" alembic upgrade head; then
            echo "==> [migrations] alembic upgrade head OK"
            return 0
        fi
        rc=$?
        if [ "$rc" = "124" ]; then
            echo " [migrations] attempt $attempt timed out after ${ALEMBIC_TIMEOUT_PER_ATTEMPT}s -- retry in ${delay}s"
        else
            echo " [migrations] attempt $attempt failed (exit $rc) -- retry in ${delay}s"
        fi
        sleep $delay
        delay=$((delay * 2))
        attempt=$((attempt + 1))
    done

    echo "==> [migrations] FATAL: alembic upgrade head failed after ${max_attempts} attempts" >&2
    return 1
}

validate_critical_schema() {
    echo "==> [schema] Validating critical schema..."
    if ! python3 -m scripts.check_critical_schema; then
        echo "==> Aborting startup: critical schema drift detected." >&2
        echo "==> See docs/runbooks/critical-schema-drift.md to apply missing DDL manually." >&2
        exit 1
    fi
    echo "==> [schema] Critical schema OK."
}

if ! run_alembic_upgrade; then
    # ── Stamp fallback ────────────────────────────────────────────────────
    # If alembic upgrade head fails (typically lock contention from the old
    # Celery beat kept alive by Cloud Run --min-instances=1 during rolling
    # deploy), stamp head as a last resort so uvicorn can start.
    #
    # "alembic stamp head" only writes to the alembic_version table — no DDL
    # locks on data tables needed.  /api/health/schema will detect any real
    # schema drift post-boot and return 503 if critical columns are missing,
    # providing a clear signal for follow-up action.
    echo "==> [migrations] All attempts failed (lock contention from old revision)." >&2
    echo "==> [migrations] Attempting alembic stamp head fallback..." >&2
    echo "==> [migrations] Rationale: DDL may already exist from a previous init_db.py run." >&2
    if timeout 30s alembic stamp head 2>&1; then
        echo "==> [migrations] Stamped at head."
        # Stamp head only writes to alembic_version — it never runs DDL.
        validate_critical_schema
    else
        echo "==> Aborting startup: cannot upgrade or stamp schema." >&2
        exit 1
    fi
fi

# Even when `alembic upgrade head` succeeds, the database may already be
# drifted from a previous `stamp head` incident (e.g. version table advanced
# to 032 while indicators.scheduler_group never existed). In that case Alembic
# will happily apply only newer revisions and the app would boot broken unless
# we probe information_schema here as well.
validate_critical_schema

# ── Pre-flight: Redis connectivity (Tarefas 3+7) ─────────────────────────────
# Hard fail-safe: if the broker is unreachable, abort startup with exit 1
# instead of letting Celery silently retry-and-give-up
# (broker_connection_max_retries=10 in celery_app.py). Cloud Run will then
# roll back to the previous revision automatically. This catches misconfigured
# REDIS_URL (e.g. missing /0 db suffix) before the pipeline silently stalls.
echo "==> [redis] Verifying Redis connectivity..."
if ! python3 - <<'PY'
import os, sys
try:
    import redis
except ImportError as e:
    print(f"ERROR: redis package not installed: {e}", file=sys.stderr)
    sys.exit(1)

url = os.environ.get("REDIS_URL", "")
if not url:
    print("ERROR: REDIS_URL is empty -- cannot connect to broker", file=sys.stderr)
    sys.exit(1)

try:
    r = redis.from_url(url, socket_connect_timeout=5, socket_timeout=5)
    if r.ping() is not True:
        print("ERROR: Redis ping returned False", file=sys.stderr)
        sys.exit(1)
    print("[redis] connected OK")
except Exception as e:
    # Never log the URL itself -- it carries the broker password.
    print(f"ERROR: Redis connection failed: {type(e).__name__}: {e}", file=sys.stderr)
    sys.exit(1)
PY
then
    echo "==> Aborting startup: Redis connection failed" >&2
    exit 1
fi

# Loglevel for Celery worker/beat. Default `info`. Set CELERY_LOGLEVEL=debug
# in Cloud Run env to capture startup-error tracebacks (broker URL parse,
# import errors, beat schedule wiring) without rebuilding.
CELERY_LOGLEVEL="${CELERY_LOGLEVEL:-info}"

# ── Start Celery worker ──────────────────────────────────────────────────────
echo "==> STARTING CELERY WORKER (loglevel=${CELERY_LOGLEVEL})..."
celery -A app.tasks.celery_app worker \
    --loglevel="${CELERY_LOGLEVEL}" \
    --concurrency="${CELERY_CONCURRENCY:-1}" \
    --queues=celery \
    &
CELERY_WORKER_PID=$!
echo " Celery worker PID: $CELERY_WORKER_PID"

# ── Start Celery beat ────────────────────────────────────────────────────────
echo "==> STARTING CELERY BEAT (loglevel=${CELERY_LOGLEVEL})..."
celery -A app.tasks.celery_app beat \
    --loglevel="${CELERY_LOGLEVEL}" \
    &
CELERY_BEAT_PID=$!
echo " Celery beat PID: $CELERY_BEAT_PID"

# ── Fail-fast: 5s post-start liveness check (Tarefas 1, 4, 5) ────────────────
# If either Celery process dies within 5 seconds of fork, the container exits 1
# so Cloud Run rolls back to the previous revision. This catches:
#   - Redis URL malformed (parse error in celery_app.py module load)
#   - Module import errors (missing dependency, syntax error in tasks)
#   - Beat schedule wiring errors (invalid cron, missing task ref)
# Without this gate, the watchdog only catches deaths AFTER the 120s grace
# period, by which time Cloud Run has already marked the revision Ready.
echo "==> [celery-check] Waiting 5s for Celery processes to stabilize..."
sleep 5
echo "==> [celery-check] ps aux | grep -E 'celery|beat' (excluding grep):"
ps aux | grep -E 'celery|beat' | grep -v grep || true

CELERY_FAILED=false
if ! kill -0 "$CELERY_WORKER_PID" 2>/dev/null; then
    echo "ERROR: Celery WORKER (PID $CELERY_WORKER_PID) died within 5s of start" >&2
    CELERY_FAILED=true
fi
if ! kill -0 "$CELERY_BEAT_PID" 2>/dev/null; then
    echo "ERROR: Celery BEAT (PID $CELERY_BEAT_PID) died within 5s of start" >&2
    CELERY_FAILED=true
fi
if [ "$CELERY_FAILED" = true ]; then
    echo "==> CELERY FAILED TO START -- aborting container (Cloud Run will roll back)" >&2
    echo "==> Check the lines above for the celery worker/beat traceback." >&2
    echo "==> Re-deploy with CELERY_LOGLEVEL=debug for verbose startup output." >&2
    exit 1
fi
echo "==> [celery-check] Worker (PID $CELERY_WORKER_PID) and Beat (PID $CELERY_BEAT_PID) both alive after 5s. ✓"

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
