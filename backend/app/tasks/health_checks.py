"""Health-check tasks for pipeline coverage.

Operational role
----------------
Detects symbols sitting in ``pool_coins`` (``is_active = true``) whose
``indicators`` row in ``scheduler_group = 'structural'`` is older than
``STRUCTURAL_GAP_ALERT_HOURS`` hours (default 2 h) — or never existed
at all.

This is the failure mode behind the 2026-05-03 incident: the structural
worker crashed mid-batch; symbols added to the pool afterwards (e.g.
ZEC_USDT on 2026-05-07) never had structural indicators computed, so
``is_complete()`` permanently quarantined them inside ``execute_buy``.
There was no alert because the existing ``ingestion_stale`` probe is
pool-wide and gets suppressed by ``pool_starved`` — it does not detect
per-symbol gaps.

Auto-recovery
-------------
When orphans are found, the task re-enqueues the universe-wide
collectors via ``task_dispatch.enqueue`` (deduped):

    * ``app.tasks.collect_structural_30m.run`` — chain:
          collect_structural_30m → compute_30m → score → evaluate
    * ``app.tasks.collect_market_data.collect_5m`` — chain:
          collect_5m → compute_5m → pipeline_scan

Both tasks process the **entire active pool** (no per-symbol arg
exists), so a single dispatch covers every orphan in the same cycle.
The orphan list is logged explicitly for traceability.

Knobs
-----
* ``STRUCTURAL_GAP_ALERT_HOURS`` — float, default ``2``. Threshold
  above which a symbol is considered an orphan.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import List

from sqlalchemy import text

from ..tasks.celery_app import celery_app
from . import task_dispatch

logger = logging.getLogger(__name__)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _run_async(coro):
    """Run async coroutine in a sync Celery task.

    Mirrors the helper used by every other task in this package
    (``compute_indicators._run_async`` etc.) — drains pending asyncpg
    tasks before closing the loop so the next invocation does not hit
    ``RuntimeError: Event loop is closed``.
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        try:
            pending = asyncio.all_tasks(loop)
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        except Exception:
            pass
        loop.close()


async def _query_orphans(gap_hours: float) -> List[str]:
    """Return symbols active in ``pool_coins`` but missing fresh structural data.

    Uses the same query shape as the operator's audit SQL — LEFT JOIN on
    the latest ``scheduler_group='structural'`` row per symbol, gap
    threshold parameterised so we do not hardcode the interval.
    """
    from ..database import CeleryAsyncSessionLocal as AsyncSessionLocal

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


async def _check_structural_coverage_async() -> dict:
    gap_hours = _env_float("STRUCTURAL_GAP_ALERT_HOURS", 2.0)

    try:
        orphans = await _query_orphans(gap_hours)
    except Exception as exc:
        logger.error("[health-check] structural coverage query failed: %s", exc)
        raise

    if not orphans:
        logger.info(
            "Structural coverage OK — all active symbols have fresh indicators "
            "(threshold=%.2fh)",
            gap_hours,
        )
        return {"orphans": 0, "gap_hours": gap_hours, "dispatched": []}

    # Cap the printed list so a 200-symbol gap does not blow up the log line.
    sample = orphans[:50]
    suffix = "" if len(orphans) <= 50 else f" (+{len(orphans) - 50} more)"
    logger.warning(
        "Structural coverage gap: %d symbols without fresh structural indicators "
        "(threshold=%.2fh): %s%s",
        len(orphans),
        gap_hours,
        sample,
        suffix,
    )

    # Per-symbol traceability log (matches the operator spec wording so the
    # ops grep contract is preserved).
    for sym in orphans:
        logger.info("Bootstrap triggered for orphan pool symbol: %s", sym)

    # Auto-recovery: enqueue both universe-wide collectors. They process
    # every is_active=true symbol in one pass, so a single dispatch covers
    # the whole orphan list. Dedup TTLs are sized to span one full cycle:
    # structural-30m runs at minute 0/30 (max 1800 s gap), 5m chain runs
    # every 300 s (TTL 600 s leaves headroom).
    dispatched: List[str] = []

    structural_id = task_dispatch.enqueue(
        "app.tasks.collect_structural_30m.run",
        dedup_key="health_check:collect_structural_30m",
        ttl_seconds=1800,
    )
    if structural_id:
        dispatched.append("collect_structural_30m.run")
    else:
        logger.info(
            "[health-check] collect_structural_30m.run dedup-skipped — "
            "another dispatch is in flight (orphans will be covered by it)."
        )

    micro_id = task_dispatch.enqueue(
        "app.tasks.collect_market_data.collect_5m",
        dedup_key="health_check:collect_5m",
        ttl_seconds=600,
    )
    if micro_id:
        dispatched.append("collect_market_data.collect_5m")
    else:
        logger.info(
            "[health-check] collect_market_data.collect_5m dedup-skipped — "
            "another dispatch is in flight (orphans will be covered by it)."
        )

    return {
        "orphans": len(orphans),
        "gap_hours": gap_hours,
        "dispatched": dispatched,
        "symbols": orphans,
    }


@celery_app.task(name="app.tasks.health_checks.check_structural_coverage")
def check_structural_coverage():
    """Beat-driven coverage probe (default every 30 min).

    Returns a dict for visibility in the Celery result backend; primary
    output is the WARNING / INFO log line consumed by the alert pipeline.
    """
    return _run_async(_check_structural_coverage_async())
