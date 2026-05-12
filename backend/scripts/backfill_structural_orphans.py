"""One-shot backfill for symbols missing fresh structural indicators.

Usage
-----
    cd backend && python scripts/backfill_structural_orphans.py
    # or, from repo root:
    python backend/scripts/backfill_structural_orphans.py

What it does
------------
1. Runs the same query as ``app.tasks.health_checks.check_structural_coverage``
   (gap threshold from ``STRUCTURAL_GAP_ALERT_HOURS`` env var, default 2 h).
2. For each orphan symbol, logs ``Backfill dispatched for: <symbol>`` so
   operators can grep the run log against ``pool_coins`` membership.
3. Enqueues the universe-wide collector tasks once each (they process
   every is_active=true symbol in one pass — there is no per-symbol
   variant of compute_structural / compute_microstructure):

       * ``app.tasks.collect_structural_30m.run``
       * ``app.tasks.collect_market_data.collect_5m``

   Dispatch uses Celery's standard ``send_task`` (this script lives
   outside ``app/tasks/``, so the dedup-wrapper invariant does not
   apply — see ``backend/tests/test_celery_routing_invariants.py``).

Exit codes
----------
* 0 — query OK (regardless of whether any orphans were found / dispatched)
* 1 — DB or broker failure
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

# Make ``app`` importable when invoked from the repo root or from inside
# ``backend/``. The script intentionally tolerates both invocation styles
# documented in the module docstring.
_HERE = Path(__file__).resolve().parent
_BACKEND_ROOT = _HERE.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("backfill_structural_orphans")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


async def _fetch_orphans(gap_hours: float) -> list[str]:
    from sqlalchemy import text
    from app.database import CeleryAsyncSessionLocal as AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            text(
                """
                SELECT pc.symbol
                FROM pool_coins pc
                LEFT JOIN (
                    SELECT symbol, MAX(time) AS last_structural
                    FROM indicators
                    WHERE scheduler_group = 'structural'
                    GROUP BY symbol
                ) i ON pc.symbol = i.symbol
                WHERE pc.is_active = true
                  AND (
                        i.last_structural IS NULL
                     OR i.last_structural < NOW() - make_interval(hours => :gap_hours)
                  )
                ORDER BY pc.symbol
                """
            ),
            {"gap_hours": gap_hours},
        )).fetchall()

    return [r.symbol for r in rows]


def _dispatch_universe_collectors() -> list[str]:
    """Send the two universe-wide collector tasks. Returns the list of
    task names that were actually accepted by the broker."""
    from app.tasks.celery_app import celery_app

    sent: list[str] = []
    for task_name in (
        "app.tasks.collect_structural_30m.run",
        "app.tasks.collect_market_data.collect_5m",
    ):
        try:
            async_result = celery_app.send_task(task_name)
            sent.append(task_name)
            logger.info("Enqueued %s (id=%s)", task_name, async_result.id)
        except Exception as exc:
            logger.error("Failed to enqueue %s: %s", task_name, exc)
    return sent


async def _main_async() -> int:
    gap_hours = _env_float("STRUCTURAL_GAP_ALERT_HOURS", 2.0)
    logger.info("Scanning for structural orphans (threshold=%.2fh)…", gap_hours)

    try:
        orphans = await _fetch_orphans(gap_hours)
    except Exception as exc:
        logger.error("Orphan query failed: %s", exc)
        return 1

    if not orphans:
        logger.info("No orphans found. Structural coverage is OK.")
        print("Backfill complete. 0 symbols queued.")
        return 0

    for sym in orphans:
        logger.info("Backfill dispatched for: %s", sym)

    sent = _dispatch_universe_collectors()
    if not sent:
        logger.error(
            "No universe-wide collectors were accepted by the broker — "
            "orphans were detected but nothing was enqueued."
        )
        print(f"Backfill FAILED. {len(orphans)} orphans detected, 0 tasks queued.")
        return 1

    print(f"Backfill complete. {len(orphans)} symbols queued.")
    return 0


def main() -> int:
    return asyncio.run(_main_async())


if __name__ == "__main__":
    sys.exit(main())
