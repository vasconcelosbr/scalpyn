"""Bounded diagnostic work, run only after committed L3 authorizations drain."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import monotonic

logger = logging.getLogger(__name__)


@dataclass
class WatchlistAuxiliaryWork:
    watchlist_name: str
    timeout_seconds: float
    run: Callable[[], Awaitable[None]]


async def run_watchlist_auxiliary_work(
    jobs: list[WatchlistAuxiliaryWork], *, max_concurrency: int,
) -> dict[str, int]:
    """Each job owns its session; a timeout cannot roll back authorization."""
    semaphore = asyncio.Semaphore(max(1, max_concurrency))
    counts = {"completed": 0, "failed": 0, "timed_out": 0}

    async def run_one(job: WatchlistAuxiliaryWork) -> None:
        async with semaphore:
            started = monotonic()
            try:
                await asyncio.wait_for(job.run(), timeout=job.timeout_seconds)
                counts["completed"] += 1
            except asyncio.TimeoutError:
                counts["timed_out"] += 1
                logger.warning(
                    "[L3_AUXILIARY] timeout wl=%s; authorization already drained",
                    job.watchlist_name,
                )
            except Exception:
                counts["failed"] += 1
                logger.exception("[L3_AUXILIARY] failed wl=%s", job.watchlist_name)
            finally:
                logger.info(
                    "[L3_AUXILIARY] finished wl=%s elapsed_seconds=%.3f",
                    job.watchlist_name, monotonic() - started,
                )

    await asyncio.gather(*(run_one(job) for job in jobs))
    return counts
