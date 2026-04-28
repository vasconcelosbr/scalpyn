"""Internal periodic scheduler for the pipeline scan (POOL → L1 → L2 → L3).

Runs as an asyncio task inside the FastAPI lifespan so the platform does not
need a separate Celery worker (Replit/Vercel deployments don't run one).  Every
interval it invokes ``_run_pipeline_scan()`` — the same coroutine wrapped by
the Celery task ``app.tasks.pipeline_scan.scan`` — which:

  * iterates every ``PipelineWatchlist`` with ``auto_refresh = true``;
  * resolves the symbol universe per stage (POOL/L1/L2/L3);
  * applies profile filters and persists ``pipeline_watchlist_assets`` and
    ``pipeline_watchlist_rejections`` snapshots; and
  * stamps ``pipeline_watchlist_assets.refreshed_at`` and
    ``pipeline_watchlist.last_scanned_at`` so the on-read fallback in
    ``_auto_refresh_watchlist_assets_if_needed`` no longer fires for stale
    rows.

Without this loop the Rejected tab keeps serving an empty
``pipeline_watchlist_rejections`` table (or rows whose indicator values drift
versus the live ``indicators`` table) — the bug captured by Task #92.

Companion to ``scheduler_service.py`` (which keeps OHLCV / indicators /
spread fresh).  This scheduler is opt-out via ``SKIP_PIPELINE_SCHEDULER=1``
and tuneable via ``PIPELINE_SCHEDULER_INTERVAL_SECONDS`` (default 600 s = 5
min, matching the historical Celery beat) and
``PIPELINE_SCHEDULER_FIRST_RUN_DELAY_SECONDS`` (default 60 s — long enough
for the indicator scheduler to land its first cycle before we score).
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = 600
DEFAULT_FIRST_RUN_DELAY_SECONDS = 60

_scheduler_task: Optional[asyncio.Task] = None


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return max(int(raw), 1)
    except (TypeError, ValueError):
        logger.warning(
            "[PIPELINE-SCHED] Invalid int for %s=%r — using default %d",
            name, raw, default,
        )
        return default


async def _run_one_cycle() -> None:
    """Invoke the pipeline scan exactly once and log a structured summary."""
    # Imported lazily so `import app.services.pipeline_scheduler_service` stays
    # cheap (pipeline_scan pulls in Celery, market data, FeatureEngine, …).
    from ..tasks.pipeline_scan import _run_pipeline_scan

    cycle_start = datetime.now(timezone.utc)
    logger.info("[PIPELINE-SCHED] starting pipeline scan cycle")

    try:
        result = await _run_pipeline_scan()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        duration = (datetime.now(timezone.utc) - cycle_start).total_seconds()
        logger.exception(
            "[PIPELINE-SCHED] pipeline scan crashed after %.1fs: %s",
            duration, exc,
        )
        return

    duration = (datetime.now(timezone.utc) - cycle_start).total_seconds()
    if isinstance(result, dict):
        logger.info(
            "[PIPELINE-SCHED] cycle finished in %.1fs — watchlists=%s "
            "new_signals=%s errors=%s execution_id=%s",
            duration,
            result.get("watchlists"),
            result.get("new_signals"),
            result.get("errors"),
            result.get("execution_id"),
        )
    else:
        logger.info(
            "[PIPELINE-SCHED] cycle finished in %.1fs — result=%r",
            duration, result,
        )


async def _scheduler_loop() -> None:
    interval = _env_int(
        "PIPELINE_SCHEDULER_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS
    )
    first_run_delay = _env_int(
        "PIPELINE_SCHEDULER_FIRST_RUN_DELAY_SECONDS",
        DEFAULT_FIRST_RUN_DELAY_SECONDS,
    )

    logger.info(
        "[PIPELINE-SCHED] pipeline scheduler starting "
        "(interval=%ds, first_run_delay=%ds)",
        interval, first_run_delay,
    )

    try:
        await asyncio.sleep(first_run_delay)
    except asyncio.CancelledError:
        return

    while True:
        try:
            await _run_one_cycle()
        except asyncio.CancelledError:
            logger.info("[PIPELINE-SCHED] scheduler cancelled — exiting loop")
            raise
        except Exception as exc:  # defensive — _run_one_cycle already swallows
            logger.exception("[PIPELINE-SCHED] cycle crashed: %s", exc)

        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            logger.info("[PIPELINE-SCHED] scheduler cancelled — exiting loop")
            raise


def start_pipeline_scheduler() -> Optional[asyncio.Task]:
    """Launch the pipeline scheduler as a background task.

    Returns the task handle (so the lifespan can cancel it on shutdown), or
    ``None`` when the scheduler is disabled via ``SKIP_PIPELINE_SCHEDULER=1``.
    """
    global _scheduler_task

    if os.environ.get("SKIP_PIPELINE_SCHEDULER") == "1":
        logger.info(
            "[PIPELINE-SCHED] SKIP_PIPELINE_SCHEDULER=1 — scheduler disabled"
        )
        return None

    if _scheduler_task is not None and not _scheduler_task.done():
        logger.debug(
            "[PIPELINE-SCHED] scheduler already running — reusing existing task"
        )
        return _scheduler_task

    loop = asyncio.get_event_loop()
    _scheduler_task = loop.create_task(
        _scheduler_loop(), name="scalpyn-pipeline-scheduler"
    )
    return _scheduler_task


async def stop_pipeline_scheduler() -> None:
    """Cancel the pipeline scheduler task and wait for it to exit."""
    global _scheduler_task
    if _scheduler_task is None or _scheduler_task.done():
        return
    _scheduler_task.cancel()
    try:
        await _scheduler_task
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        pass
    _scheduler_task = None
