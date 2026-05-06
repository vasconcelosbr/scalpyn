<<<<<<< HEAD
from .jobs import (
    IndicatorWrite,
    MarketMetadataWrite,
    OhlcvCandle,
    PersistenceJob,
)
from .service import (
    PersistenceService,
    get_persistence_service,
    get_persistence_snapshot,
    run_persistence_batch,
    start_persistence_service,
    stop_persistence_service,
)

__all__ = [
    "IndicatorWrite",
    "MarketMetadataWrite",
    "OhlcvCandle",
    "PersistenceJob",
    "PersistenceService",
    "get_persistence_service",
    "get_persistence_snapshot",
    "run_persistence_batch",
    "start_persistence_service",
    "stop_persistence_service",
]
=======
"""Persistence layer — UnitOfWork + Repositories + bounded queue + workers.

Public surface (the only symbols modules outside ``services/persistence``
should import):

    from app.services.persistence import (
        is_enabled,
        enqueue,
        start_workers,
        stop_workers,
        get_queue_snapshot,
        OhlcvBatch,
        OhlcvCandle,
        MarketMetadataUpsert,
        IndicatorsUpsert,
        ReconciledTradeUpsert,
    )

The feature flag ``USE_PERSISTENCE_QUEUE`` (env var, default ``0``) toggles
the new pathway.  When ``0``, ``is_enabled()`` returns False and producers
must keep using the legacy direct-write path.  When ``1``, workers are
spun up by the FastAPI lifespan and producers should call ``enqueue(msg)``.
"""

from __future__ import annotations

import os
import time
from typing import Any

from .messages import (
    IndicatorsUpsert,
    MarketMetadataUpsert,
    OhlcvBatch,
    OhlcvCandle,
    ReconciledTradeUpsert,
)
from .queue import get_queue
from .worker import start_workers, stop_workers, workers_alive

__all__ = [
    "is_enabled",
    "enqueue",
    "start_workers",
    "stop_workers",
    "workers_alive",
    "get_queue_snapshot",
    "now_monotonic",
    "OhlcvBatch",
    "OhlcvCandle",
    "MarketMetadataUpsert",
    "IndicatorsUpsert",
    "ReconciledTradeUpsert",
]


def is_enabled() -> bool:
    """Return True iff the persistence queue is the active write path."""
    return os.environ.get("USE_PERSISTENCE_QUEUE", "0") == "1"


def now_monotonic() -> float:
    """Convenience for producers — assign to ``Message.enqueued_at``."""
    return time.monotonic()


async def enqueue(msg: Any) -> bool:
    """Enqueue *msg* on the singleton queue.

    Returns ``True`` when accepted, ``False`` when dropped (only possible
    for ``ingest`` and ``compute`` categories — see ``queue.PersistenceQueue.put``).
    """
    return await get_queue().put(msg)


def get_queue_snapshot() -> dict[str, Any]:
    """Return a JSON-serialisable snapshot for the healthcheck endpoint."""
    q = get_queue()
    return {
        "enabled": is_enabled(),
        "maxsize": q.maxsize,
        "depth_total": q.qsize(),
        "depth_by_category": q.depth_by_category(),
        "workers_alive": workers_alive(),
    }
>>>>>>> f0bcd5b (Task #226: Persistence Architecture Refactor — foundation + scheduler migration)
