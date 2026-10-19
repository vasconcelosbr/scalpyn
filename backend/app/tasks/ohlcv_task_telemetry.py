"""Lifecycle timestamps without logging arguments, credentials or price payloads."""
from datetime import datetime, timezone
import json
import logging
from celery.signals import before_task_publish, after_task_publish, task_prerun, task_postrun, task_revoked

logger = logging.getLogger(__name__)
PREFIX = "app.tasks.collect_research_ohlcv."


def emit(event, **fields):
    logger.info("[OHLCV-TASK] %s", json.dumps({"event": event, **fields}, default=str))


@before_task_publish.connect
def published(sender=None, headers=None, routing_key=None, **kwargs):
    if not (sender or "").startswith(PREFIX) or headers is None:
        return
    now = datetime.now(timezone.utc).isoformat()
    headers["ohlcv_published_at"] = now
    emit("publish_attempt", task=sender, task_id=headers.get("id"), publish_started_at=now,
         scheduled_eta=headers.get("eta"), expires_at=headers.get("expires"), queue=routing_key)


@after_task_publish.connect
def publish_confirmed(sender=None, headers=None, routing_key=None, **kwargs):
    if (sender or "").startswith(PREFIX) and headers is not None:
        emit("published", task=sender, task_id=headers.get("id"),
             published_at=datetime.now(timezone.utc), queue=routing_key)


@task_prerun.connect
def started(sender=None, task_id=None, **kwargs):
    if not getattr(sender, "name", "").startswith(PREFIX):
        return
    request = sender.request
    now = datetime.now(timezone.utc)
    request.ohlcv_started_at = now
    raw = (request.headers or {}).get("ohlcv_published_at")
    try:
        wait = (now - datetime.fromisoformat(raw)).total_seconds() if raw else None
    except (ValueError, TypeError):
        wait = None
    emit("started", task=sender.name, task_id=task_id, started_at=now,
         published_at=raw, queue_wait_seconds=wait)


@task_postrun.connect
def finished(sender=None, task_id=None, state=None, **kwargs):
    if not getattr(sender, "name", "").startswith(PREFIX):
        return
    emit("finished", task=sender.name, task_id=task_id, state=state,
         started_at=getattr(sender.request, "ohlcv_started_at", None),
         finished_at=datetime.now(timezone.utc))


@task_revoked.connect
def revoked(request=None, expired=None, terminated=None, **kwargs):
    if not getattr(request, "name", "").startswith(PREFIX):
        return
    emit("discarded", task=request.name, task_id=request.id, expired=expired,
         terminated=terminated, observed_at=datetime.now(timezone.utc))
