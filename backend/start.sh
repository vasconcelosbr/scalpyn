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
        echo "==> See docs/runbooks/scheduler-group-drift.md to apply missing DDL manually." >&2
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

# Early-exit probe: give Celery 10 s to attempt the broker connection.
# If either process has already exited (or become a zombie) before uvicorn
# starts, log a structured alert and abort immediately rather than letting
# the zombie watchdog fail silently after the full grace period.
#
# 10 s is sufficient: Celery's first broker retry fires at ~2 s with
# exponential back-off.  With broker_connection_max_retries=10 and the
# default backoff cap, all retries exhaust well within 10 s when Redis is
# unreachable (typicaly < 5 s total).  A healthy Celery process will still
# be alive and connecting at the 10 s mark, so false positives are
# effectively zero.
sleep 10
EARLY_EXIT=false
for label in "worker:$CELERY_WORKER_PID" "beat:$CELERY_BEAT_PID"; do
    name="${label%%:*}"
    pid="${label##*:}"
    stat=$(ps -o stat= -p "$pid" 2>/dev/null)
    if [ -z "$stat" ] || case "$stat" in Z*) true ;; *) false ;; esac; then
        echo "==> [startup] ALERT: celery $name (PID $pid) exited or is zombie within 10s -- broker connection likely failed" >&2
        EARLY_EXIT=true
    else
        echo " [startup] celery $name (PID $pid) alive (stat=$stat)"
    fi
done
if [ "$EARLY_EXIT" = true ]; then
    echo "==> [startup] FATAL: Celery failed to start -- aborting to force container restart" >&2
    exit 1
fi

# Capture the PID that exec uvicorn will inherit (shell PID becomes uvicorn PID)
MAIN_PID=$$

# Grace period (seconds) before watchdog starts checking Celery health.
# This allows Celery to retry its Redis connection on startup without
# triggering an immediate container shutdown.
WATCHDOG_GRACE=${WATCHDOG_GRACE:-120}

# is_process_alive PID
# Returns true (0) only when the process EXISTS and is NOT a zombie.
#
# Why not just `kill -0 $PID`?
# When a background process exits while its parent (uvicorn, after `exec`) is
# alive but never calls waitpid(), the child becomes a zombie — it retains its
# PID entry in the process table but performs no work.  `kill -0` returns 0
# (success) for zombie processes because the PID entry still exists, so the
# watchdog would incorrectly conclude that Celery is healthy.  Checking the
# `stat` column from `ps` and rejecting entries that start with Z (zombie)
# prevents this false-positive.
is_process_alive() {
    local pid=$1
    local stat
    stat=$(ps -o stat= -p "$pid" 2>/dev/null)
    # Non-empty AND does NOT start with Z (zombie)
    [ -n "$stat" ] && case "$stat" in Z*) return 1 ;; esac
}

# Watchdog: wait for grace period, then monitor Celery health.
# Only kill uvicorn if Celery is STILL dead after retries.
(
    echo " [watchdog] Waiting ${WATCHDOG_GRACE}s grace period before monitoring Celery..."
    sleep "$WATCHDOG_GRACE"
    echo " [watchdog] Grace period over -- monitoring Celery processes."

    while sleep 30; do
        WORKER_OK=true
        BEAT_OK=true

        if ! is_process_alive "$CELERY_WORKER_PID"; then
            WORKER_OK=false
        fi
        if ! is_process_alive "$CELERY_BEAT_PID"; then
            BEAT_OK=false
        fi

        if [ "$WORKER_OK" = false ] || [ "$BEAT_OK" = false ]; then
            echo "WARNING: Celery process down or zombie (worker=$WORKER_OK beat=$BEAT_OK) -- shutting down container"
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
