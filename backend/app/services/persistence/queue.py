"""Bounded asyncio queue with per-category backpressure policy.

Three categories with explicit drop policy (see ``messages.Category``):

* ``critical`` — block the producer indefinitely.  Used for trades, fills,
  decisions: never drop.
* ``compute``  — block the producer with a configurable timeout.  Used by
  schedulers and indicator computation: prefer to slow down the producer
  rather than lose a write.
* ``ingest``   — non-blocking ``put_nowait`` with drop-oldest fallback.
  Used by high-frequency streams (orderbook ticks, taker trades) where
  losing the oldest sample is preferable to backpressuring the producer.

The queue is a singleton bound to the running event loop — the first call
to ``get_queue()`` lazily creates it.  Workers and producers must run on
the same loop (the FastAPI/uvicorn loop).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import defaultdict
from typing import Any, Optional

from . import metrics as _m

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return max(int(raw), 1)
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return max(float(raw), 0.0)
    except (TypeError, ValueError):
        return default


class PersistenceQueue:
    """Bounded async queue with per-category accounting and drop policy."""

    def __init__(self, maxsize: int, compute_put_timeout_s: float) -> None:
        self._q: asyncio.Queue[Any] = asyncio.Queue(maxsize=maxsize)
        self._maxsize = maxsize
        self._compute_put_timeout_s = compute_put_timeout_s
        self._depth_by_category: dict[str, int] = defaultdict(int)
        self._closed = False

    @property
    def maxsize(self) -> int:
        return self._maxsize

    def qsize(self) -> int:
        return self._q.qsize()

    def depth_by_category(self) -> dict[str, int]:
        return dict(self._depth_by_category)

    async def put(self, msg: Any) -> bool:
        """Enqueue *msg*; return True if accepted, False if dropped/rejected.

        Behaviour by category:
          * ``critical`` — ``await put`` forever (subject to graceful shutdown).
          * ``compute``  — ``await put`` with timeout; on timeout, drop with
                            metric and warn.
          * ``ingest``   — ``put_nowait``; if full, pop oldest message and
                            retry once.  Counts as ``queue_full`` drop.
        """
        if self._closed:
            _m.record_drop(getattr(msg, "category", "unknown"),
                           getattr(msg, "kind", type(msg).__name__),
                           "shutdown")
            return False

        category = getattr(msg, "category", "compute")
        kind = getattr(msg, "kind", type(msg).__name__)

        if category == "critical":
            await self._q.put(msg)
            self._depth_by_category[category] += 1
            _m.record_enqueue(category, kind)
            _m.set_queue_depth(category, self._depth_by_category[category])
            return True

        if category == "compute":
            try:
                await asyncio.wait_for(self._q.put(msg), timeout=self._compute_put_timeout_s)
            except asyncio.TimeoutError:
                _m.record_drop(category, kind, "queue_full")
                logger.warning(
                    "[persistence-queue] DROP compute msg=%s — queue full for %.1fs",
                    kind, self._compute_put_timeout_s,
                )
                return False
            self._depth_by_category[category] += 1
            _m.record_enqueue(category, kind)
            _m.set_queue_depth(category, self._depth_by_category[category])
            return True

        # ingest
        try:
            self._q.put_nowait(msg)
        except asyncio.QueueFull:
            # Drop oldest then retry once.
            try:
                old = self._q.get_nowait()
                old_cat = getattr(old, "category", "ingest")
                self._depth_by_category[old_cat] = max(
                    0, self._depth_by_category[old_cat] - 1)
                _m.record_drop(old_cat,
                               getattr(old, "kind", type(old).__name__),
                               "queue_full")
                self._q.task_done()
            except asyncio.QueueEmpty:
                pass
            try:
                self._q.put_nowait(msg)
            except asyncio.QueueFull:
                _m.record_drop(category, kind, "queue_full")
                return False
        self._depth_by_category[category] += 1
        _m.record_enqueue(category, kind)
        _m.set_queue_depth(category, self._depth_by_category[category])
        return True

    async def get(self) -> Any:
        msg = await self._q.get()
        category = getattr(msg, "category", "compute")
        self._depth_by_category[category] = max(
            0, self._depth_by_category[category] - 1)
        _m.set_queue_depth(category, self._depth_by_category[category])
        return msg

    def task_done(self) -> None:
        self._q.task_done()

    def close(self) -> None:
        self._closed = True

    async def join(self) -> None:
        await self._q.join()


_queue: Optional[PersistenceQueue] = None


def get_queue() -> PersistenceQueue:
    """Return the process-wide singleton queue, creating it on first call."""
    global _queue
    if _queue is None:
        maxsize = _env_int("PERSISTENCE_QUEUE_MAXSIZE", 1000)
        compute_timeout = _env_float("PERSISTENCE_QUEUE_COMPUTE_TIMEOUT_S", 5.0)
        _queue = PersistenceQueue(maxsize=maxsize,
                                  compute_put_timeout_s=compute_timeout)
        logger.info(
            "[persistence-queue] initialised maxsize=%d compute_timeout=%.1fs",
            maxsize, compute_timeout,
        )
    return _queue


def reset_queue_for_tests() -> None:
    """Test-only — drop the singleton so each test starts clean."""
    global _queue
    _queue = None
