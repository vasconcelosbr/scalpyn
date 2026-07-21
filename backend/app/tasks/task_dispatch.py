"""Centralized Celery dispatch wrapper with mandatory dedup.

Architectural invariant #3 (operator spec, Task #216):
    Every Celery enqueue from inside ``app/tasks/`` MUST go through
    ``enqueue()`` here. Direct ``celery_app.send_task()`` and
    ``<task>.apply_async()`` calls are forbidden in ``app/tasks/``.

    The lint test ``backend/tests/test_celery_routing_invariants.py``
    walks every ``app/tasks/**/*.py`` AST and fails the build if it
    finds either pattern outside the allowlist (this module).

Dedup contract:
    * Caller passes ``dedup_key`` (e.g. ``"compute"``, ``"score"``).
    * We attempt ``SET NX EX <ttl_seconds>`` against Redis. If the
      key is already held, we INFO-log ``DEDUP_SKIP`` and return
      ``None`` — the duplicate is dropped silently and the queue
      cannot pile up.
    * If we get the lock, we send the task with the dedup key and ownership
      token in headers; ``task_postrun`` atomically releases only its own lock.
    * Redis-unreachable: fail-open. We log a WARNING and enqueue the
      task anyway — the operator runbook explicitly requires that we
      never silently drop work when the dedup store is the failure mode.
      Operators see this in ``/api/system/celery-status`` (``error``).

External callers (``app/api/system.py``, ``app/api/simulations.py``)
are intentionally **not** routed through this wrapper:
    * The diagnostics endpoint must use the task's own ``apply_async()``
      so traceback + state visibility match the SRE runbook
      (collect_all is named explicitly in the spec).
    * Simulations API uses ``apply_async`` to capture the AsyncResult
      and return a task id to the caller. Both are documented exceptions.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)

_DEDUP_PREFIX = "celery:dedup:"
_DEDUP_HEADER = "x-scalpyn-dedup-key"
_DEDUP_TOKEN_HEADER = "x-scalpyn-dedup-token"
_COMPARE_AND_DELETE = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""
# Operator-spec acceptance criterion C: ``oldest_task_age_seconds`` in
# ``/api/system/celery-status`` MUST be non-null when the queue has
# pending work. Celery's stock envelope is best-effort about
# ``properties.timestamp`` (varies by serializer), so every dispatch
# stamps a controlled ISO-8601 UTC timestamp that the status endpoint
# falls back on. Header name is namespaced so we never collide with a
# Celery-internal key.
_ENQUEUED_AT_HEADER = "x-scalpyn-enqueued-at"


def _redis_client():
    """Build a short-timeout Redis client. Returns ``None`` on failure."""
    try:
        import redis
        from ..config import settings
        return redis.from_url(
            settings.REDIS_URL,
            socket_connect_timeout=2,
            socket_timeout=2,
            decode_responses=True,
        )
    except Exception as exc:
        logger.warning("[dispatch] Redis unavailable: %s", exc)
        return None


def enqueue(
    task_name: str,
    *,
    dedup_key: str,
    ttl_seconds: int,
    expires_seconds: Optional[int] = None,
    queue: Optional[str] = None,
    args: tuple = (),
    kwargs: Optional[dict] = None,
) -> Optional[str]:
    """Enqueue a Celery task only if the dedup lock can be claimed.

    Args:
        task_name: Fully-qualified Celery task name
            (e.g. ``app.tasks.compute_scores.score``).
        dedup_key: Logical lock name. Two enqueues sharing this key
            cannot both run; the second is dropped until the first
            finishes (postrun signal releases the lock) or the TTL
            expires.
        ttl_seconds: Safety upper bound — the task's expected wall-clock
            plus a small margin. Prevents a crashed worker from
            blocking the queue forever.
        expires_seconds: Optional Celery message expiry for cadence-bound work.
        queue: Explicit queue override. Normally left ``None`` —
            ``celery_app.conf.task_routes`` resolves the queue from
            the task name.
        args / kwargs: Forwarded to the task.

    Returns:
        AsyncResult task id when the task was enqueued, ``None`` when
        the duplicate was suppressed.
    """
    full_key = f"{_DEDUP_PREFIX}{dedup_key}"
    lock_token = uuid4().hex
    safe_ttl = int(max(ttl_seconds, 5))
    safe_expires = (
        int(max(expires_seconds, 5)) if expires_seconds is not None else None
    )

    redis_client = _redis_client()
    if redis_client is not None:
        try:
            acquired = redis_client.set(
                full_key, lock_token, nx=True, ex=safe_ttl
            )
            if not acquired:
                logger.info(
                    "[dispatch] DEDUP_SKIP task=%s key=%s ttl=%ss",
                    task_name, dedup_key, safe_ttl,
                )
                return None
        except Exception as exc:
            logger.warning(
                "[dispatch] Redis SET NX failed for %s (%s) — failing open",
                task_name, exc,
            )

    from .celery_app import celery_app
    headers = {
        _DEDUP_HEADER: full_key,
        _DEDUP_TOKEN_HEADER: lock_token,
        # Stamped at dispatch time (UTC, ISO-8601) so the status
        # endpoint can compute oldest_task_age_seconds even when the
        # underlying Celery serializer omits ``properties.timestamp``.
        _ENQUEUED_AT_HEADER: datetime.now(timezone.utc).isoformat(),
    }
    async_result = celery_app.send_task(
        task_name,
        args=args,
        kwargs=kwargs or {},
        queue=queue,
        headers=headers,
        expires=safe_expires,
    )
    return async_result.id


def _release_dedup_lock(headers: Optional[dict]) -> None:
    """Best-effort DEL of the dedup lock. Safe to call repeatedly."""
    if not headers:
        return
    full_key = headers.get(_DEDUP_HEADER)
    lock_token = headers.get(_DEDUP_TOKEN_HEADER)
    if not full_key or not lock_token:
        return
    redis_client = _redis_client()
    if redis_client is None:
        return
    try:
        redis_client.eval(
            _COMPARE_AND_DELETE,
            1,
            full_key,
            lock_token,
        )
    except Exception as exc:
        logger.debug("[dispatch] DEL %s failed: %s", full_key, exc)


def _on_task_postrun(
    sender=None,
    task_id=None,
    task=None,
    args=None,
    kwargs=None,
    retval=None,
    state=None,
    **_kw: Any,
) -> None:
    """Signal handler: release the dedup lock when the task completes."""
    if task is None:
        return
    headers: Optional[dict] = None
    try:
        request = getattr(task, "request", None)
        if request is not None:
            headers = getattr(request, "headers", None) or {}
    except Exception:
        return
    _release_dedup_lock(headers)


def _on_task_prerun(
    sender=None,
    task_id=None,
    task=None,
    args=None,
    kwargs=None,
    **_kw: Any,
) -> None:
    """Signal handler: log queue wait time (enqueue → start).

    P0.2 — reads the ``x-scalpyn-enqueued-at`` header stamped by
    :func:`enqueue` at dispatch time and emits a structured log line::

        [QUEUE_WAIT] task=<name> task_id=<id> queue_wait_s=<float>

    Use this to diagnose queue congestion: high ``queue_wait_s`` on
    ``compute_structural_5m`` or ``compute_30m`` indicates that the
    structural queue is saturated.
    """
    if task is None:
        return
    try:
        request = getattr(task, "request", None)
        if request is None:
            return
        headers = getattr(request, "headers", None) or {}
        enqueued_at_str = headers.get(_ENQUEUED_AT_HEADER)
        if not enqueued_at_str:
            return
        enqueued_at = datetime.fromisoformat(enqueued_at_str)
        queue_wait_s = (datetime.now(timezone.utc) - enqueued_at).total_seconds()
        task_name = getattr(task, "name", "unknown")
        logger.info(
            "[QUEUE_WAIT] task=%s task_id=%s queue_wait_s=%.2f",
            task_name, task_id, queue_wait_s,
        )
    except Exception as exc:
        logger.debug("[dispatch] task_prerun queue_wait logging failed: %s", exc)


def install_signal_handlers() -> None:
    """Wire Celery signals for dedup lock release and queue-wait logging.

    Idempotent — Celery's signal connect deduplicates by ``(receiver, sender)``
    when ``weak=False`` is paired with the same callable identity.
    """
    from celery.signals import task_postrun, task_prerun
    task_postrun.connect(_on_task_postrun, weak=False)
    task_prerun.connect(_on_task_prerun, weak=False)
