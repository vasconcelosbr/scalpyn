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
and tuneable via ``PIPELINE_SCHEDULER_INTERVAL_SECONDS`` (default 600 s =
10 min, twice the historical Celery beat to keep API latency low) and
``PIPELINE_SCHEDULER_FIRST_RUN_DELAY_SECONDS`` (default 60 s — used as a
hard timeout only when ``SKIP_BACKGROUND_SCHEDULER=1``; otherwise the
pipeline scheduler waits indefinitely for the indicator scheduler to land
its first cycle so the first pipeline run lands on top of fresh data).
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

# Hard upper bound on how long the readiness handshake will block startup,
# even when the indicator scheduler is enabled.  Belt-and-suspenders:
# scheduler_service now sets `_first_cycle_done_event` from a try/finally
# so it should always fire, but if a future regression breaks that
# guarantee the pipeline scheduler must NOT deadlock on a fresh DB — the
# whole point of this scheduler is to repair that exact state.
DEFAULT_MAX_READINESS_WAIT_SECONDS = 300

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


async def _count_active_watchlists() -> Optional[int]:
    """Cheap pre-scan COUNT so we can include it in the cycle start log."""
    try:
        from sqlalchemy import text
        from ..database import AsyncSessionLocal
        async with AsyncSessionLocal() as db:
            row = (await db.execute(text(
                "SELECT COUNT(*) FROM pipeline_watchlists WHERE auto_refresh = true"
            ))).first()
            return int(row[0]) if row else 0
    except Exception as exc:
        logger.debug(
            "[PIPELINE-SCHED] active-watchlist count query failed: %s", exc
        )
        return None


async def _run_one_cycle() -> None:
    """Invoke the pipeline scan exactly once and log a structured summary."""
    # Imported lazily so `import app.services.pipeline_scheduler_service` stays
    # cheap (pipeline_scan pulls in Celery, market data, FeatureEngine, …).
    from ..tasks.pipeline_scan import _run_pipeline_scan

    cycle_start = datetime.now(timezone.utc)
    active_count = await _count_active_watchlists()
    if active_count is not None:
        logger.info(
            "[PIPELINE-SCHED] starting pipeline scan cycle for %d watchlists",
            active_count,
        )
    else:
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

    # Dual-scheduler readiness handshake:
    # Wait for BOTH structural (15 min) AND microstructure (5 min) schedulers
    # to complete their first cycle before running the first pipeline scan.
    # This ensures the first scan lands on top of fresh indicators.
    #
    # Policy:
    #   * If both new schedulers are disabled (SKIP_*=1) we fall back to the
    #     combined scheduler's event (scheduler_service.wait_for_first_cycle).
    #   * If all schedulers are disabled, we sleep first_run_delay and proceed.
    #   * Hard upper bound: max_readiness_wait seconds (default 300 s).
    #     Microstructure fires at ~15 s + 5 min cycle, structural at ~30 s +
    #     15 min cycle.  We advance if microstructure is ready AND structural
    #     lag < 30 min (structural may still be on its first run).
    max_readiness_wait = _env_int(
        "PIPELINE_SCHEDULER_MAX_READINESS_WAIT_SECONDS",
        DEFAULT_MAX_READINESS_WAIT_SECONDS,
    )

    all_schedulers_disabled = (
        os.environ.get("SKIP_STRUCTURAL_SCHEDULER") == "1"
        and os.environ.get("SKIP_MICROSTRUCTURE_SCHEDULER") == "1"
        and os.environ.get("ENABLE_COMBINED_SCHEDULER") != "1"
    )

    try:
        if all_schedulers_disabled:
            logger.info(
                "[PIPELINE-SCHED] all indicator schedulers disabled — "
                "sleeping %ds before first pipeline run",
                first_run_delay,
            )
            try:
                await asyncio.sleep(first_run_delay)
            except asyncio.CancelledError:
                return
        else:
            from .structural_scheduler_service import (
                wait_for_first_cycle as _wait_structural,
            )
            from .microstructure_scheduler_service import (
                wait_for_first_cycle as _wait_micro,
            )

            # Wait for microstructure first (fast — completes in ~15 s + 5 min).
            micro_ok = await _wait_micro(timeout=float(max_readiness_wait))
            if micro_ok:
                logger.info(
                    "[PIPELINE-SCHED] microstructure scheduler signaled first "
                    "cycle complete — proceeding (structural may still be running)"
                )
            else:
                logger.warning(
                    "[PIPELINE-SCHED] no microstructure readiness signal within "
                    "%ds — proceeding to avoid startup deadlock",
                    max_readiness_wait,
                )

    except asyncio.CancelledError:
        return
    except Exception as exc:
        logger.warning(
            "[PIPELINE-SCHED] readiness handshake failed (%s) — "
            "falling back to %ds sleep", exc, first_run_delay,
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
