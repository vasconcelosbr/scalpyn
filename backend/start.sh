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

# ── Sentinel beacon (stderr, very first line) ────────────────────────────────
# Cloud Run aggregates stdout AND stderr, but operators commonly filter the
# log explorer by `severity>=ERROR` (which only shows stderr). When a deploy
# fails and "logs are empty", the question is always: did start.sh even
# execute? This single line answers it. K_REVISION/K_SERVICE are auto-
# injected by Cloud Run; absent locally, so the tag still reads cleanly in
# dev. Echo to BOTH streams so it shows up regardless of filter.
_BOOT_TAG="[start.sh] CONTAINER ENTRY pid=$$ k_service=${K_SERVICE:-local} k_revision=${K_REVISION:-local}"
echo "$_BOOT_TAG" >&2
echo "$_BOOT_TAG"

# ── ERR trap: surface implicit `set -e` exits with line + cmd context ────────
# Without this, any failing command silently exits the script and Cloud Run
# only sees "container exited 1" with nothing pointing to which line did it.
# BASH_COMMAND is the command that triggered the trap; LINENO is its line.
trap 'rc=$?; echo "==> [start.sh] FATAL exit rc=${rc} at line ${LINENO}: ${BASH_COMMAND}" >&2; exit $rc' ERR

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
# Default raised 50→90s (May 2026). With Cloud SQL under lock contention from
# the parallel collect_market_data workers, the previous 50s × 3 attempts +
# backoffs (~165s) was leaving <75s of the 240s startup-probe budget for the
# rest of boot (validate_critical_schema + redis ping + uvicorn workers cold-
# importing app.main). Result: scalpyn revisions intermittently failing with
# "Startup probe timed out after 4m" (revisions 00148-00163, 00450+).
# 90s × 3 + backoffs (~280s) is wider than the probe — but combined with the
# ASYNC_MIGRATIONS opt-in below, alembic no longer sits in the boot critical
# path for the API service.
ALEMBIC_TIMEOUT_PER_ATTEMPT=${ALEMBIC_TIMEOUT_PER_ATTEMPT:-90}

# ── Async migrations (opt-in, K_SERVICE=scalpyn only) ────────────────────────
# When ASYNC_MIGRATIONS=1, run alembic + critical-schema validation in a
# background subprocess, allowing uvicorn to bind :8080 immediately and
# satisfy the Cloud Run startup probe (TCP probe on PORT). The background
# process writes its result to /tmp/.migrations_done (success) or
# /tmp/.migrations_failed (failure). The watchdog below tears the container
# down on failure — Cloud Run then rolls back to the previous revision,
# preserving the existing safety contract. /api/health/schema independently
# probes information_schema and returns 503 while the file is missing, so
# clients have a clear "warming up" signal.
#
# This is an OPT-IN behavior keyed on env var, default OFF. The current
# Cloud Build wiring sets ASYNC_MIGRATIONS=1 only on the `scalpyn` API
# service; workers and beat keep the synchronous gate (their boot is not
# user-facing, and parallelizing there would just multiply the lock waits).
ASYNC_MIGRATIONS="${ASYNC_MIGRATIONS:-0}"

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

# run_schema_gate: runs the full alembic + critical-schema validation flow.
# Factored out so it can be invoked synchronously (workers/beat) or
# asynchronously in a background subshell (API service when
# ASYNC_MIGRATIONS=1). Returns 0 on success, exits non-zero on failure when
# called inline.
run_schema_gate() {
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
            return 1
        fi
    fi

    # Even when `alembic upgrade head` succeeds, the database may already be
    # drifted from a previous `stamp head` incident (e.g. version table advanced
    # to 032 while indicators.scheduler_group never existed). In that case Alembic
    # will happily apply only newer revisions and the app would boot broken unless
    # we probe information_schema here as well.
    validate_critical_schema
    return 0
}

MIGRATIONS_DONE_FILE="/tmp/.migrations_done"
MIGRATIONS_FAILED_FILE="/tmp/.migrations_failed"
rm -f "$MIGRATIONS_DONE_FILE" "$MIGRATIONS_FAILED_FILE"

# ── SKIP_ALEMBIC (opt-in for worker/beat services) ───────────────────────────
# When SKIP_ALEMBIC=1, this container trusts that the API service (scalpyn)
# has already run alembic upgrade head and only validates the critical schema.
# This eliminates the ShareLock pile-up that occurs when all 6 services race
# to acquire the alembic_version row lock simultaneously on every deploy.
#
# Set SKIP_ALEMBIC=1 on: scalpyn-worker-micro, scalpyn-worker-structural,
#   scalpyn-worker-compute, scalpyn-worker-execution, scalpyn-beat.
# Leave unset (defaults to 0) on: scalpyn (the API service owns migrations).
SKIP_ALEMBIC="${SKIP_ALEMBIC:-0}"

if [ "$SKIP_ALEMBIC" = "1" ]; then
    echo "==> [migrations] SKIP_ALEMBIC=1 — skipping alembic upgrade (API service owns migrations)"
    validate_critical_schema
elif [ "$ASYNC_MIGRATIONS" = "1" ]; then
    # Background path: spawn a subshell that runs the full gate and signals
    # the result via a sentinel file. The watchdog (declared after uvicorn
    # background pieces below) polls the failure file and kills the container
    # if the gate fails — preserving the same "Cloud Run rolls back to
    # previous revision on failure" contract as the sync path. The success
    # case lets uvicorn keep serving as soon as the schema is valid.
    echo "==> [migrations] ASYNC_MIGRATIONS=1 — running schema gate in background (uvicorn will bind :8080 immediately)"
    (
        set +e
        # Safety net: validate_critical_schema() and other helpers use `exit 1`
        # which, inside this subshell, terminate ONLY the subshell. Without
        # this trap the watchdog would never see /tmp/.migrations_failed and
        # the container would happily serve traffic against a broken schema —
        # exactly the rollback contract we are protecting. Trap on EXIT writes
        # the failed sentinel if neither sentinel was created by the normal
        # branches below (and the FastAPI middleware in app/main.py keeps
        # returning 503 for non-health routes until /tmp/.migrations_done
        # appears, so no traffic touches an intermediate schema).
        trap '
            if [ ! -f "$MIGRATIONS_DONE_FILE" ] && [ ! -f "$MIGRATIONS_FAILED_FILE" ]; then
                touch "$MIGRATIONS_FAILED_FILE"
                echo "==> [migrations] Async subshell exited unexpectedly — marking FAILED" >&2
            fi
        ' EXIT
        if run_schema_gate; then
            touch "$MIGRATIONS_DONE_FILE"
            echo "==> [migrations] Async gate completed OK"
        else
            touch "$MIGRATIONS_FAILED_FILE"
            echo "==> [migrations] Async gate FAILED — watchdog will tear down the container" >&2
        fi
    ) &
    SCHEMA_GATE_PID=$!
    echo " [migrations] Background gate PID: $SCHEMA_GATE_PID"
else
    # Synchronous path: original behavior. Boot blocks here until the
    # schema is up-to-date. On failure exit 1 so Cloud Run rolls back.
    if ! run_schema_gate; then
        exit 1
    fi
fi

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

# ── Queue topology (Task #216, operator spec parts 4 + 7 + 9) ────────────────
# WORKER_QUEUES selects which Celery queues this container consumes.
#   - "microstructure,structural,execution"  (default — single-container dev)
#   - "microstructure"                       (Cloud Run scalpyn-worker-micro)
#   - "structural"                           (Cloud Run scalpyn-worker-structural)
#   - "execution"                            (Cloud Run scalpyn-worker-execution)
#   - "" (empty)                             (Cloud Run scalpyn API or scalpyn-beat
#                                             — NO Celery worker is started here)
#
# RUN_BEAT controls the beat scheduler:
#   - "1" (default dev / scalpyn-beat)       → beat IS started here.
#   - "0" (every other Cloud Run service)    → beat is OFF; the dedicated
#                                              scalpyn-beat revision owns the
#                                              periodic schedule so it can
#                                              never double-fire.
#
# At least one of {worker, beat} must be enabled — an "all-off" container
# would be the API-only role, which is the intended shape for the `scalpyn`
# Cloud Run service that fronts HTTP traffic.
# Safety guard: if running in the Cloud Run beat service AND WORKER_QUEUES was
# not explicitly set, default to empty so the beat container does NOT also run
# a worker. Without this guard, an unset WORKER_QUEUES on scalpyn-beat causes
# it to consume all three queues simultaneously with the dedicated workers —
# confirmed root cause of 26 concurrent DB lock waits in May 2026.
# K_SERVICE is injected automatically by Cloud Run; absent locally so the
# dev single-container default ("all queues") is preserved unchanged.
if [ "${K_SERVICE:-}" = "scalpyn-beat" ] && [ -z "${WORKER_QUEUES+x}" ]; then
    echo "==> [queue] K_SERVICE=scalpyn-beat + WORKER_QUEUES unset — defaulting to empty (beat-only, no worker)"
    WORKER_QUEUES=""
else
    WORKER_QUEUES="${WORKER_QUEUES-microstructure,structural,execution}"
fi
RUN_BEAT="${RUN_BEAT:-1}"

# ── Safe concurrency defaults per Cloud Run service (Task #216 follow-up) ────
# If CELERY_CONCURRENCY is not explicitly set in Cloud Run env vars, apply
# safe per-service defaults to prevent concurrent task execution from
# causing DB lock contention during startup.
# structural worker: collect_all acquires per-symbol locks; 2 concurrent
# tasks = guaranteed lock contention when pool_coins has >1 symbol.
# micro worker: similar pattern with 5m indicators.
if [ -z "${CELERY_CONCURRENCY+x}" ]; then
    case "${K_SERVICE:-}" in
        scalpyn-worker-structural)
            CELERY_CONCURRENCY=1
            echo "==> [concurrency] K_SERVICE=scalpyn-worker-structural — defaulting CELERY_CONCURRENCY=1 (prevents concurrent collect_all lock contention)"
            ;;
        scalpyn-worker-micro)
            CELERY_CONCURRENCY=1
            echo "==> [concurrency] K_SERVICE=scalpyn-worker-micro — defaulting CELERY_CONCURRENCY=1"
            ;;
        *)
            # Keep existing default of 2 for other services and local dev
            ;;
    esac
fi

CELERY_WORKER_PID=""
CELERY_BEAT_PID=""

# ── Start Celery worker (only when WORKER_QUEUES is non-empty) ───────────────
# Setting WORKER_QUEUES="" on the API service skips the worker entirely so
# HTTP latency is never co-tenant with task execution. CELERY_CONCURRENCY
# is sized per queue by the deploy config (4 for microstructure burst, 2
# for structural/execution); the dev default of 2 keeps the single-container
# Replit setup responsive without overloading the local broker.
# ── Unique Celery nodename (Cloud Run hostname is always "localhost") ────────
# Without --hostname, every Cloud Run instance announces itself to the broker
# as `celery@localhost`. When the API calls `celery_app.control.inspect()`,
# replies from different instances collide on the same nodename — the client
# de-duplicates, returns 0 workers, and the dashboard alarms `worker_offline_60s`
# even though workers are healthy and draining queues. Use K_SERVICE (Cloud Run
# service name) + a random suffix so each instance is uniquely addressable.
NODENAME_SUFFIX="$(cat /proc/sys/kernel/random/uuid 2>/dev/null | tr -d '-' | head -c 8 || echo "$$")"
CELERY_NODENAME="${K_SERVICE:-celery}-${NODENAME_SUFFIX}"

if [ -n "$WORKER_QUEUES" ]; then
    echo "==> STARTING CELERY WORKER (queues=${WORKER_QUEUES} concurrency=${CELERY_CONCURRENCY:-2} loglevel=${CELERY_LOGLEVEL} hostname=celery@${CELERY_NODENAME})..."
    celery -A app.tasks.celery_app worker \
        --loglevel="${CELERY_LOGLEVEL}" \
        --concurrency="${CELERY_CONCURRENCY:-2}" \
        --queues="${WORKER_QUEUES}" \
        --hostname="celery@${CELERY_NODENAME}" \
        &
    CELERY_WORKER_PID=$!
    echo " Celery worker PID: $CELERY_WORKER_PID"
else
    echo "==> SKIPPING CELERY WORKER (WORKER_QUEUES is empty; this container is API-only or beat-only)"
fi

# ── Start Celery beat (only when RUN_BEAT=1) ─────────────────────────────────
if [ "$RUN_BEAT" = "1" ]; then
    echo "==> STARTING CELERY BEAT (loglevel=${CELERY_LOGLEVEL})..."
    celery -A app.tasks.celery_app beat \
        --loglevel="${CELERY_LOGLEVEL}" \
        &
    CELERY_BEAT_PID=$!
    echo " Celery beat PID: $CELERY_BEAT_PID"
else
    echo "==> SKIPPING CELERY BEAT (RUN_BEAT=${RUN_BEAT}; this container only runs workers or API)"
fi

# ── Fail-fast: 5s post-start liveness check (Tarefas 1, 4, 5) ────────────────
# If a Celery process we did start dies within 5 seconds of fork, the
# container exits 1 so Cloud Run rolls back to the previous revision. This
# catches:
#   - Redis URL malformed (parse error in celery_app.py module load)
#   - Module import errors (missing dependency, syntax error in tasks)
#   - Beat schedule wiring errors (invalid cron, missing task ref)
# Without this gate, the watchdog only catches deaths AFTER the 120s grace
# period, by which time Cloud Run has already marked the revision Ready.
# An API-only container has neither PID set, so this check is a no-op for it.
if [ -n "$CELERY_WORKER_PID" ] || [ -n "$CELERY_BEAT_PID" ]; then
    echo "==> [celery-check] Waiting 5s for Celery processes to stabilize..."
    sleep 5
    echo "==> [celery-check] ps aux | grep -E 'celery|beat' (excluding grep):"
    ps aux | grep -E 'celery|beat' | grep -v grep || true

    CELERY_FAILED=false
    if [ -n "$CELERY_WORKER_PID" ] && ! kill -0 "$CELERY_WORKER_PID" 2>/dev/null; then
        echo "ERROR: Celery WORKER (PID $CELERY_WORKER_PID) died within 5s of start" >&2
        CELERY_FAILED=true
    fi
    if [ -n "$CELERY_BEAT_PID" ] && ! kill -0 "$CELERY_BEAT_PID" 2>/dev/null; then
        echo "ERROR: Celery BEAT (PID $CELERY_BEAT_PID) died within 5s of start" >&2
        CELERY_FAILED=true
    fi
    if [ "$CELERY_FAILED" = true ]; then
        echo "==> CELERY FAILED TO START -- aborting container (Cloud Run will roll back)" >&2
        echo "==> Check the lines above for the celery worker/beat traceback." >&2
        echo "==> Re-deploy with CELERY_LOGLEVEL=debug for verbose startup output." >&2
        exit 1
    fi
    if [ -n "$CELERY_WORKER_PID" ] && [ -n "$CELERY_BEAT_PID" ]; then
        echo "==> [celery-check] Worker (PID $CELERY_WORKER_PID) and Beat (PID $CELERY_BEAT_PID) both alive after 5s. ✓"
    elif [ -n "$CELERY_WORKER_PID" ]; then
        echo "==> [celery-check] Worker (PID $CELERY_WORKER_PID) alive after 5s. ✓ (beat skipped)"
    else
        echo "==> [celery-check] Beat (PID $CELERY_BEAT_PID) alive after 5s. ✓ (worker skipped)"
    fi
else
    echo "==> [celery-check] No Celery worker or beat started — API-only container."
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
# Only kill uvicorn if a Celery process we *started* is STILL dead after retries.
# An API-only container (no worker, no beat) skips the watchdog entirely.
if [ -n "$CELERY_WORKER_PID" ] || [ -n "$CELERY_BEAT_PID" ]; then
    (
        echo " [watchdog] Waiting ${WATCHDOG_GRACE}s grace period before monitoring Celery..."
        sleep "$WATCHDOG_GRACE"
        echo " [watchdog] Grace period over -- monitoring Celery processes."

        while sleep 30; do
            WORKER_OK=true
            BEAT_OK=true

            # Empty PID means the role is owned by a sibling container; do not
            # mark it as failing on this side.
            if [ -n "$CELERY_WORKER_PID" ] && ! is_process_alive "$CELERY_WORKER_PID"; then
                WORKER_OK=false
            fi
            if [ -n "$CELERY_BEAT_PID" ] && ! is_process_alive "$CELERY_BEAT_PID"; then
                BEAT_OK=false
            fi

            if [ "$WORKER_OK" = false ] || [ "$BEAT_OK" = false ]; then
                echo "WARNING: Celery process down or zombie (worker=$WORKER_OK beat=$BEAT_OK) -- shutting down container"
                kill -TERM "$MAIN_PID" 2>/dev/null
                break
            fi
        done
    ) &
fi

# ── Async-migrations watchdog (ASYNC_MIGRATIONS=1 only) ──────────────────────
# Tears down the container if the background schema gate writes the failure
# sentinel. Runs every 5s for up to 15 minutes (enough headroom for a
# contended Cloud SQL: alembic 90s × 3 + backoffs + stamp fallback ≈ 5 min,
# leaving 10 min safety margin). After 15 minutes without either sentinel,
# logs a warning and stops polling — at that point /api/health/schema is the
# remaining signal.
if [ "$ASYNC_MIGRATIONS" = "1" ]; then
    (
        deadline=$(($(date +%s) + 900))
        while [ "$(date +%s)" -lt "$deadline" ]; do
            if [ -f "$MIGRATIONS_FAILED_FILE" ]; then
                echo "==> [migrations-watchdog] Schema gate FAILED — tearing down container so Cloud Run rolls back" >&2
                kill -TERM "$MAIN_PID" 2>/dev/null
                exit 0
            fi
            if [ -f "$MIGRATIONS_DONE_FILE" ]; then
                echo "==> [migrations-watchdog] Schema gate completed — uvicorn is now backed by an up-to-date schema."
                exit 0
            fi
            sleep 5
        done
        echo " [migrations-watchdog] No sentinel after 15min — gate may be hung. /api/health/schema is the remaining signal." >&2
    ) &
fi

# Graceful cleanup: when uvicorn/container receives SIGTERM, stop children first
cleanup() {
    echo "==> SIGTERM received -- stopping background processes..."
    PIDS_TO_KILL=""
    [ -n "$CELERY_WORKER_PID" ] && PIDS_TO_KILL="$PIDS_TO_KILL $CELERY_WORKER_PID"
    [ -n "$CELERY_BEAT_PID" ]   && PIDS_TO_KILL="$PIDS_TO_KILL $CELERY_BEAT_PID"
    [ -n "${SCHEMA_GATE_PID:-}" ] && PIDS_TO_KILL="$PIDS_TO_KILL $SCHEMA_GATE_PID"
    if [ -n "$PIDS_TO_KILL" ]; then
        kill -TERM $PIDS_TO_KILL 2>/dev/null
        wait $PIDS_TO_KILL 2>/dev/null
    fi
}
trap cleanup TERM INT

# ── Start uvicorn (schema is already up-to-date, OR coming up async) ────────
echo "==> Starting uvicorn..."
exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port "${PORT:-8080}" \
    --workers "${WEB_CONCURRENCY:-1}"
