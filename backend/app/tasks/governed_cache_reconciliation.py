"""Durable cache reconciliation for human-confirmed configuration writes."""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from .celery_app import QUEUE_AI_ORCHESTRATION, celery_app
from .task_dispatch import enqueue
from ..database import run_db_task
from ..services.governed_change_service import (
    cache_reconciliation_outcome,
    claim_due_cache_reconciliations,
    reconcile_cache_outbox_attempt,
)


logger = logging.getLogger(__name__)
_DB_RETRY_BACKOFF_SECONDS = (15, 30, 60)


@celery_app.task(
    name="app.tasks.governed_cache_reconciliation.reconcile",
    bind=True,
    max_retries=3,
)
def reconcile_governed_cache(
    self,
    user_id: str,
    plan_id: str,
    kind: str,
) -> dict:
    """Execute one outbox attempt; Redis failures remain durable DB state.

    Redis failure is not raised here: the service records the failed attempt,
    next due time and bounded attempt count atomically.  Celery retries are
    reserved for transient failures before that durable state can be written.
    """
    try:
        result = asyncio.run(run_db_task(
            lambda db: reconcile_cache_outbox_attempt(
                db,
                UUID(user_id),
                UUID(plan_id),
                kind=kind,
            ),
            celery=True,
        ))
    except Exception as exc:
        retry_index = min(
            int(getattr(self.request, "retries", 0)),
            len(_DB_RETRY_BACKOFF_SECONDS) - 1,
        )
        raise self.retry(
            exc=exc,
            countdown=_DB_RETRY_BACKOFF_SECONDS[retry_index],
        )

    outcome = cache_reconciliation_outcome(result, kind)
    return {
        "status": outcome["status"],
        "retry_state": outcome["retry_state"],
        "attempts": outcome["attempts"],
        "max_attempts": outcome["max_attempts"],
        "plan_id": plan_id,
        "kind": kind,
    }


@celery_app.task(
    name="app.tasks.governed_cache_reconciliation.dispatch_pending",
    max_retries=0,
)
def dispatch_pending_governed_cache_reconciliations() -> dict:
    """Claim due JSON outbox entries, then publish after the DB commit."""
    specs = asyncio.run(run_db_task(
        lambda db: claim_due_cache_reconciliations(db),
        celery=True,
    ))
    dispatched = 0
    publish_failed = 0
    for spec in specs:
        try:
            task_id = enqueue(
                "app.tasks.governed_cache_reconciliation.reconcile",
                dedup_key=(
                    "governed-cache-reconcile:"
                    f"{spec['kind']}:{spec['plan_id']}"
                ),
                ttl_seconds=150,
                queue=QUEUE_AI_ORCHESTRATION,
                args=(spec["user_id"], spec["plan_id"], spec["kind"]),
            )
        except Exception as exc:
            publish_failed += 1
            logger.warning(
                "Governed cache reconciliation publish failed for plan %s: %s",
                spec["plan_id"],
                type(exc).__name__,
            )
        else:
            dispatched += task_id is not None
    return {
        "status": "COMPLETED",
        "eligible": len(specs),
        "dispatched": dispatched,
        "publish_failed": publish_failed,
    }
