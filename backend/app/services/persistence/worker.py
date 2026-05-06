"""PersistenceWorker pool — fixed N consumers of the PersistenceQueue.

Each worker:
  1. Awaits ``queue.get()`` for a message.
  2. Looks up the dispatch handler in ``repositories.DISPATCH``.
  3. Runs the handler inside ``uow.run_uow`` (one short transaction).
  4. Records latency / outcome metrics; calls ``queue.task_done()``.

Workers MUST never perform external I/O.  If a message expects to fan out
side effects (e.g. ReconciledTradeUpsert), the side effects are described
in the message itself as data, not as live calls.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional

from .queue import PersistenceQueue, get_queue
from .repositories import DISPATCH
from .uow import run_uow
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


class PersistenceWorker:
    def __init__(self, name: str, queue: PersistenceQueue) -> None:
        self.name = name
        self._queue = queue
        self._task: Optional[asyncio.Task] = None
        self._stop = False

    def start(self) -> asyncio.Task:
        loop = asyncio.get_event_loop()
        self._task = loop.create_task(self._run(), name=self.name)
        return self._task

    async def stop(self, timeout: float = 5.0) -> None:
        self._stop = True
        if self._task is None or self._task.done():
            return
        # Soft stop: let in-flight message finish, then cancel.
        try:
            await asyncio.wait_for(self._task, timeout=timeout)
        except asyncio.TimeoutError:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    async def _run(self) -> None:
        logger.info("[persistence-worker] %s started", self.name)
        while not self._stop:
            try:
                msg = await self._queue.get()
            except asyncio.CancelledError:
                break
            try:
                await self._process(msg)
            finally:
                self._queue.task_done()
        logger.info("[persistence-worker] %s exiting", self.name)

    async def _process(self, msg) -> None:
        category = getattr(msg, "category", "compute")
        kind = getattr(msg, "kind", type(msg).__name__)
        enqueued_at = float(getattr(msg, "enqueued_at", time.monotonic()))
        queue_wait = max(0.0, time.monotonic() - enqueued_at)

        handler = DISPATCH.get(type(msg))
        if handler is None:
            logger.error("[persistence-worker] %s: no handler for %s",
                         self.name, kind)
            _m.record_dequeue(category, kind, "failed", queue_wait)
            _m.record_error(kind, "NoHandler")
            return

        _m.inc_workers_busy(1)
        commit_start = time.monotonic()
        retries = 0

        def _on_retry(exc: BaseException, attempt: int) -> None:
            nonlocal retries
            retries = attempt
            _m.record_retry(kind, type(exc).__name__)

        try:
            await run_uow(lambda db: handler(db, msg), on_retry=_on_retry)
            outcome = "ok" if retries == 0 else "retry"
            _m.record_dequeue(category, kind, outcome, queue_wait)
            _m.record_commit_latency(kind, time.monotonic() - commit_start)
        except Exception as exc:
            _m.record_dequeue(category, kind, "failed", queue_wait)
            _m.record_error(kind, type(exc).__name__)
            logger.error(
                "[persistence-worker] %s permanent failure kind=%s queue_wait=%.3fs err=%s",
                self.name, kind, queue_wait, exc,
                exc_info=True,
            )
        finally:
            _m.inc_workers_busy(-1)


_workers: list[PersistenceWorker] = []


def start_workers() -> list[asyncio.Task]:
    """Start the configured number of workers (idempotent)."""
    global _workers
    if _workers:
        return [w._task for w in _workers if w._task is not None]
    n = _env_int("PERSISTENCE_WORKERS", 4)
    queue = get_queue()
    tasks: list[asyncio.Task] = []
    for i in range(n):
        worker = PersistenceWorker(name=f"persistence-worker-{i}", queue=queue)
        tasks.append(worker.start())
        _workers.append(worker)
    logger.info("[persistence-worker] started %d workers", n)
    return tasks


async def stop_workers(timeout: float = 10.0) -> None:
    """Drain the queue (best-effort) and stop all workers."""
    global _workers
    if not _workers:
        return
    queue = get_queue()
    queue.close()
    # Give in-flight processing a chance to finish.
    try:
        await asyncio.wait_for(queue.join(), timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning(
            "[persistence-worker] drain timeout after %.1fs — qsize=%d",
            timeout, queue.qsize(),
        )
    await asyncio.gather(
        *[w.stop(timeout=2.0) for w in _workers],
        return_exceptions=True,
    )
    _workers = []
    logger.info("[persistence-worker] all workers stopped")


def workers_alive() -> int:
    return sum(1 for w in _workers
               if w._task is not None and not w._task.done())
