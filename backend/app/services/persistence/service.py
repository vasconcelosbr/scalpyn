from __future__ import annotations

import asyncio
import logging
import os
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ...database import AsyncSessionLocal, CeleryAsyncSessionLocal, get_pool_snapshot
from .jobs import PersistenceJob
from .repositories import PersistenceRepository
from .unit_of_work import UnitOfWork

logger = logging.getLogger(__name__)

_DEFAULT_QUEUE_SIZE = 1000
_DEFAULT_WORKERS = 3
_service: "PersistenceService | None" = None


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return max(int(raw), 1)
    except (TypeError, ValueError):
        logger.warning("[PERSIST] invalid int for %s=%r; using %d", name, raw, default)
        return default


@dataclass
class _DomainState:
    processed: int = 0
    failed: int = 0
    last_success_at: str | None = None
    last_error: str | None = None


@dataclass
class _PersistenceState:
    service_name: str
    started_at: str | None = None
    total_enqueued: int = 0
    total_processed: int = 0
    total_failed: int = 0
    total_retries: int = 0
    rollback_count: int = 0
    acquire_latency_ms_last: float = 0.0
    transaction_time_ms_last: float = 0.0
    workers_alive: set[int] = field(default_factory=set)
    worker_heartbeat: dict[int, str] = field(default_factory=dict)
    domains: dict[str, _DomainState] = field(default_factory=lambda: defaultdict(_DomainState))

    def increment_enqueued(self) -> None:
        self.total_enqueued += 1

    def increment_processed(self, domain: str) -> None:
        self.total_processed += 1
        state = self.domains[domain]
        state.processed += 1
        state.last_success_at = datetime.now(timezone.utc).isoformat()

    def mark_success(self, domain: str) -> None:
        self.increment_processed(domain)

    def mark_failure(self, domain: str, key: str, exc: Exception) -> None:
        self.total_failed += 1
        state = self.domains[domain]
        state.failed += 1
        state.last_error = f"{key}: {type(exc).__name__}"

    def increment_rollbacks(self) -> None:
        self.rollback_count += 1

    def increment_retries(self) -> None:
        self.total_retries += 1

    def observe_acquire_latency(self, value_ms: float) -> None:
        self.acquire_latency_ms_last = round(value_ms, 2)

    def observe_transaction_time(self, value_ms: float) -> None:
        self.transaction_time_ms_last = round(value_ms, 2)


class PersistenceService:
    def __init__(
        self,
        session_factory,
        *,
        workers: int,
        queue_maxsize: int,
        service_name: str,
    ) -> None:
        self._session_factory = session_factory
        self._workers = workers
        self._queue_maxsize = queue_maxsize
        self._service_name = service_name
        self._queue: asyncio.Queue[PersistenceJob | None] = asyncio.Queue(maxsize=queue_maxsize)
        self._repo = PersistenceRepository()
        self._state = _PersistenceState(service_name=service_name)
        self._tasks: list[asyncio.Task] = []
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._state.started_at = datetime.now(timezone.utc).isoformat()
        self._tasks = [
            asyncio.create_task(self._worker_loop(index), name=f"{self._service_name}-worker-{index}")
            for index in range(self._workers)
        ]
        logger.info(
            "[PERSIST] started service=%s workers=%d queue_maxsize=%d",
            self._service_name,
            self._workers,
            self._queue_maxsize,
        )

    async def stop(self) -> None:
        if not self._started:
            return
        for _ in self._tasks:
            await self._queue.put(None)
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._started = False
        logger.info("[PERSIST] stopped service=%s", self._service_name)

    async def enqueue(self, job: PersistenceJob) -> None:
        await self.start()
        self._state.increment_enqueued()
        await self._queue.put(job)

    async def join(self) -> None:
        await self._queue.join()

    def snapshot(self) -> dict:
        queue_size = self._queue.qsize()
        pool = get_pool_snapshot()
        workers_alive = sorted(self._state.workers_alive)
        domains = {
            name: {
                "processed": state.processed,
                "failed": state.failed,
                "last_success_at": state.last_success_at,
                "last_error": state.last_error,
            }
            for name, state in sorted(self._state.domains.items())
        }
        queue_utilization = round(queue_size / self._queue_maxsize, 4) if self._queue_maxsize else 0.0
        saturated = queue_size >= self._queue_maxsize or not workers_alive
        status = "degraded" if saturated or self._state.total_failed else "ok"
        return {
            "status": status,
            "service": self._service_name,
            "started_at": self._state.started_at,
            "queue": {
                "size": queue_size,
                "maxsize": self._queue_maxsize,
                "utilization": queue_utilization,
                "saturated": queue_size >= self._queue_maxsize,
                "total_enqueued": self._state.total_enqueued,
                "total_processed": self._state.total_processed,
                "total_failed": self._state.total_failed,
            },
            "workers": {
                "configured": self._workers,
                "alive": len(workers_alive),
                "worker_ids": workers_alive,
                "heartbeat": self._state.worker_heartbeat,
            },
            "db": {
                "acquire_latency_ms_last": self._state.acquire_latency_ms_last,
                "transaction_time_ms_last": self._state.transaction_time_ms_last,
                "rollback_count": self._state.rollback_count,
                "retry_count": self._state.total_retries,
                "pool": pool,
            },
            "domains": domains,
        }

    async def _worker_loop(self, worker_id: int) -> None:
        uow = UnitOfWork(self._session_factory, self._state)
        self._state.workers_alive.add(worker_id)
        self._state.worker_heartbeat[worker_id] = datetime.now(timezone.utc).isoformat()
        try:
            while True:
                job = await self._queue.get()
                if job is None:
                    self._queue.task_done()
                    break
                try:
                    await uow.execute(
                        lambda session: self._repo.persist_job(session, job),
                        domain=job.domain,
                        key=job.key,
                    )
                except Exception as exc:
                    logger.error("[PERSIST] job failed %s: %s", job.key, exc, exc_info=True)
                finally:
                    self._state.worker_heartbeat[worker_id] = datetime.now(timezone.utc).isoformat()
                    self._queue.task_done()
        finally:
            self._state.workers_alive.discard(worker_id)
            self._state.worker_heartbeat[worker_id] = datetime.now(timezone.utc).isoformat()


def get_persistence_service() -> PersistenceService:
    global _service
    if _service is None:
        _service = PersistenceService(
            AsyncSessionLocal,
            workers=_env_int("PERSISTENCE_WORKERS", _DEFAULT_WORKERS),
            queue_maxsize=_env_int("PERSISTENCE_QUEUE_MAXSIZE", _DEFAULT_QUEUE_SIZE),
            service_name="app-persistence",
        )
    return _service


async def start_persistence_service() -> PersistenceService:
    service = get_persistence_service()
    await service.start()
    return service


async def stop_persistence_service() -> None:
    global _service
    if _service is None:
        return
    await _service.stop()


def get_persistence_snapshot() -> dict:
    return get_persistence_service().snapshot()


async def run_persistence_batch(
    jobs: list[PersistenceJob],
    *,
    celery: bool,
    workers: int | None = None,
    queue_maxsize: int | None = None,
    service_name: str = "ephemeral-persistence",
) -> dict:
    if not jobs:
        return {
            "status": "ok",
            "service": service_name,
            "queue": {"size": 0, "maxsize": queue_maxsize or _DEFAULT_QUEUE_SIZE},
            "workers": {"configured": workers or _DEFAULT_WORKERS, "alive": 0},
            "db": {},
            "domains": {},
        }
    service = PersistenceService(
        CeleryAsyncSessionLocal if celery else AsyncSessionLocal,
        workers=workers or _env_int("PERSISTENCE_WORKERS", _DEFAULT_WORKERS),
        queue_maxsize=queue_maxsize or _env_int("PERSISTENCE_QUEUE_MAXSIZE", _DEFAULT_QUEUE_SIZE),
        service_name=service_name,
    )
    await service.start()
    try:
        for job in jobs:
            await service.enqueue(job)
        await service.join()
        return service.snapshot()
    finally:
        await service.stop()
