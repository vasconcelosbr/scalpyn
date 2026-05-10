"""Celery task — Orphan Transaction Watchdog (Task #256).

Kills Postgres backends with ``xact_start`` older than the configured
threshold. Replaces the manual `pg_terminate_backend` runbook step that
was needed five times since 2026-05-08 (see
``backend/docs/runbooks/2026-05-08-pipeline-recovery.md``).

Filter contract — covers BOTH classes of orphan TX:

* ``state IN ('idle in transaction', 'idle in transaction (aborted)')``
  — the original pattern from 2026-05-08 (container killed mid-tx).
* ``state = 'active'`` with ``xact_start`` older than the threshold —
  the pattern observed on 2026-05-10 (PID 795563, ``SAVEPOINT
  sa_savepoint_148`` held for 7h31min). The previous runbook filter
  missed this entirely.

**Kill scoping (security)**: the candidate set is restricted to backends
whose ``application_name LIKE 'scalpyn%'``. This guarantees the watchdog
never terminates operator-initiated sessions (psql / Cloud SQL Studio /
maintenance) even if they exceed the threshold. Backends without an
application_name are reported as candidates (``code="unknown_app"``)
but NOT killed — see the ``_NON_SCALPYN_SKIPPED`` log line.

The threshold is intentionally generous (default 15 min) so legitimate
long-running maintenance never gets killed. Override via env var
``ORPHAN_TX_THRESHOLD_MINUTES``.

The watchdog uses a dedicated admin engine (``OrphanWatchdogSessionLocal``,
``NullPool`` — opens at most one connection per task invocation, mandatory
for cross-event-loop safety in Celery) so it never competes with task
workers for connections and is trivially identifiable in
``pg_stat_activity`` by its ``application_name='scalpyn-orphan-watchdog'``. The kill statement
requires either ``rds_superuser`` (Cloud SQL) or the ``pg_signal_backend``
role; we surface permission failures via
``scalpyn_orphan_tx_scan_errors_total`` rather than crashing the task.

Registered as: ``app.tasks.orphan_tx_watchdog.kill_orphans``
"""

from __future__ import annotations

import asyncio
import logging
import os

from sqlalchemy import text

from .celery_app import celery_app
from ..database import OrphanWatchdogSessionLocal
from ..services.orphan_tx_metrics import record_killed, record_scan

logger = logging.getLogger(__name__)


def _threshold_minutes() -> int:
    raw = os.environ.get("ORPHAN_TX_THRESHOLD_MINUTES", "15")
    try:
        v = int(raw)
        return v if v > 0 else 15
    except (TypeError, ValueError):
        return 15


# Allowlist pattern. Anything not matching is REPORTED but NOT killed.
# Set via env to widen / narrow without redeploy if needed.
_APP_ALLOWLIST_PATTERN = os.environ.get(
    "ORPHAN_TX_APP_ALLOWLIST", "scalpyn%"
)


_SCAN_SQL = text(
    """
    SELECT pid,
           COALESCE(application_name, '')      AS application_name,
           state,
           EXTRACT(EPOCH FROM (NOW() - xact_start))::int AS xact_age_seconds,
           LEFT(COALESCE(query, ''), 200)      AS query_preview,
           (COALESCE(application_name, '') LIKE :app_pattern) AS is_scalpyn
    FROM pg_stat_activity
    WHERE datname = current_database()
      AND pid <> pg_backend_pid()
      AND xact_start IS NOT NULL
      AND NOW() - xact_start > make_interval(mins => :threshold_minutes)
      AND state IN (
          'active',
          'idle in transaction',
          'idle in transaction (aborted)'
      )
    ORDER BY xact_start ASC
    LIMIT 50
    """
)


@celery_app.task(
    name="app.tasks.orphan_tx_watchdog.kill_orphans",
    bind=True,
    max_retries=0,
)
def kill_orphans(self) -> str:
    """Beat-driven sweep — kill scalpyn-owned TXs older than the threshold."""
    threshold = _threshold_minutes()

    async def _inner() -> dict:
        async with OrphanWatchdogSessionLocal() as session:
            rows = (await session.execute(
                _SCAN_SQL,
                {
                    "threshold_minutes": threshold,
                    "app_pattern": _APP_ALLOWLIST_PATTERN,
                },
            )).all()
            if not rows:
                return {"victims": 0, "killed": 0, "errors": 0, "skipped": 0}

            killed = 0
            errors = 0
            skipped_non_scalpyn = 0
            for r in rows:
                if not r.is_scalpyn:
                    skipped_non_scalpyn += 1
                    logger.warning(
                        "[orphan-tx-watchdog] SKIP non-scalpyn pid=%s "
                        "app=%r state=%r xact_age=%ss query=%r — kept alive "
                        "(out of allowlist %r)",
                        r.pid, r.application_name, r.state,
                        r.xact_age_seconds, r.query_preview,
                        _APP_ALLOWLIST_PATTERN,
                    )
                    continue

                logger.warning(
                    "[orphan-tx-watchdog] candidate pid=%s state=%r app=%r "
                    "xact_age=%ss query=%r",
                    r.pid, r.state, r.application_name,
                    r.xact_age_seconds, r.query_preview,
                )
                try:
                    ok_row = (await session.execute(
                        text("SELECT pg_terminate_backend(:pid) AS ok"),
                        {"pid": int(r.pid)},
                    )).one()
                    if bool(ok_row.ok):
                        killed += 1
                        record_killed(state=r.state, app=r.application_name)
                        logger.warning(
                            "[orphan-tx-watchdog] terminated pid=%s "
                            "(state=%r, xact_age=%ss)",
                            r.pid, r.state, r.xact_age_seconds,
                        )
                    else:
                        errors += 1
                        logger.warning(
                            "[orphan-tx-watchdog] pg_terminate_backend(%s) "
                            "returned false — permission denied or pid gone",
                            r.pid,
                        )
                except Exception as exc:
                    errors += 1
                    logger.warning(
                        "[orphan-tx-watchdog] terminate failed pid=%s: %s",
                        r.pid, exc,
                    )
            # Read-only path — no commit needed; pg_terminate_backend is a
            # function call that sends a signal, not a transactional write.
            return {
                "victims": len(rows),
                "killed": killed,
                "errors": errors,
                "skipped": skipped_non_scalpyn,
            }

    try:
        result = asyncio.run(_inner())
        record_scan(success=True)
        if result["victims"] > 0:
            logger.warning(
                "[orphan-tx-watchdog] threshold=%dmin victims=%d killed=%d "
                "errors=%d skipped_non_scalpyn=%d",
                threshold, result["victims"], result["killed"],
                result["errors"], result["skipped"],
            )
        return (
            f"OrphanTxWatchdog: threshold={threshold}min "
            f"victims={result['victims']} killed={result['killed']} "
            f"errors={result['errors']} skipped={result['skipped']}"
        )
    except Exception as exc:
        record_scan(success=False)
        logger.error("[orphan-tx-watchdog] scan failed: %s", exc, exc_info=True)
        # Idempotent + beat-driven: never re-raise (would re-queue under
        # the global ``acks_late=True``). Return the error string so the
        # operator sees it in the Celery result log.
        return f"OrphanTxWatchdog: ERROR {type(exc).__name__}: {exc}"
