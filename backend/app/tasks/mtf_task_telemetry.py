"""Observe queue wait separately from execution; never log task arguments."""
from datetime import datetime, timezone
import json
import logging

from celery.signals import before_task_publish, task_received, task_prerun, task_postrun, task_revoked

logger = logging.getLogger(__name__)
TASKS = frozenset({
    "app.tasks.mtf_chart.observe",
    "app.tasks.mtf_chart.advance_replays",
    "app.tasks.shadow_trade_monitor.run",
})
PUBLISHED_AT = "mtf_published_at"


def emit(event, **fields):
    logger.info("[MTF-TASK] %s", json.dumps({"event": event, **fields}, default=str))


def elapsed(raw, now):
    try:
        stamp = datetime.fromisoformat(raw) if isinstance(raw, str) else raw
        return (now - stamp).total_seconds() if stamp is not None else None
    except (ValueError, TypeError):
        return None


@before_task_publish.connect
def published(sender=None, headers=None, routing_key=None, **kwargs):
    if sender not in TASKS or headers is None:
        return
    now = datetime.now(timezone.utc)
    headers[PUBLISHED_AT] = now.isoformat()
    emit("publish_attempt", task=sender, task_id=headers.get("id"), published_at=now,
         expires_at=headers.get("expires"), queue=routing_key)


@task_received.connect
def received(request=None, **kwargs):
    if getattr(request, "name", None) not in TASKS:
        return
    now = datetime.now(timezone.utc)
    # Request._request_dict is also the context passed into the pool.
    request._request_dict["mtf_received_at"] = now.isoformat()
    emit("received", task=request.name, task_id=request.id, received_at=now,
         queue_wait_seconds=elapsed(request._request_dict.get(PUBLISHED_AT), now),
         expires_at=request.expires)


@task_prerun.connect
def started(sender=None, task_id=None, **kwargs):
    if getattr(sender, "name", None) not in TASKS:
        return
    now = datetime.now(timezone.utc)
    sender.request.mtf_started_at = now
    headers = sender.request.headers or {}
    emit("started", task=sender.name, task_id=task_id, started_at=now,
         publish_to_start_seconds=elapsed(headers.get(PUBLISHED_AT), now),
         receive_to_start_seconds=elapsed(headers.get("mtf_received_at"), now))


@task_postrun.connect
def finished(sender=None, task_id=None, state=None, **kwargs):
    if getattr(sender, "name", None) not in TASKS:
        return
    now = datetime.now(timezone.utc)
    emit("finished", task=sender.name, task_id=task_id, state=state, finished_at=now,
         execution_seconds=elapsed(getattr(sender.request, "mtf_started_at", None), now))


@task_revoked.connect
def discarded(request=None, expired=None, terminated=None, **kwargs):
    if getattr(request, "name", None) not in TASKS:
        return
    now = datetime.now(timezone.utc)
    emit("discarded", task=request.name, task_id=request.id, expired=expired,
         terminated=terminated, observed_at=now, expires_at=request.expires,
         queue_wait_seconds=elapsed(request._request_dict.get(PUBLISHED_AT), now))
