"""Regression for a rate-held Shadow consuming the only MTF prefetch slot."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

from celery.worker.consumer.consumer import Consumer
from celery.app.task import Context

from app.tasks.celery_app import celery_app, TASK_ANNOTATIONS
from app.tasks import mtf_task_telemetry as telemetry

MONITOR = "app.tasks.shadow_trade_monitor.run"
OBSERVER = "app.tasks.mtf_chart.observe"


def test_monitor_cannot_hold_prefetch_in_a_rate_bucket():
    consumer = object.__new__(Consumer)
    consumer.timer = Mock()
    consumer._limit_order = 0
    consumer._limit_move_to_pool = Mock()
    legacy_bucket = consumer.bucket_for_task(SimpleNamespace(rate_limit="12/h"))
    assert legacy_bucket.can_consume(1)  # The preceding monitor spent its token.
    request = Mock()
    consumer._limit_task(request, legacy_bucket, 1)
    consumer._limit_move_to_pool.assert_not_called()
    assert list(legacy_bucket.contents) == [(request, 1)]
    assert consumer.timer.call_after.call_args.args[0] > 240

    # Effective annotation, not just the decorator: no bucket can retain
    # the message before pool execution/early acknowledgement.
    from app.tasks.shadow_trade_monitor import run
    assert run.rate_limit is None
    assert consumer.bucket_for_task(run) is None
    assert TASK_ANNOTATIONS[MONITOR]["acks_late"] is False
    assert celery_app.conf.worker_prefetch_multiplier == 1
    assert celery_app.conf.beat_schedule["shadow_trade_monitor"]["schedule"] > 0
    assert celery_app.conf.beat_schedule["mtf_chart_after_close"]["options"]["expires"] == 240


def test_mtf_lifecycle_distinguishes_wait_execution_and_expiry(monkeypatch):
    events = []
    monkeypatch.setattr(telemetry, "emit", lambda event, **fields: events.append((event, fields)))
    headers = {"id": "observation", "expires": "2026-11-26T12:04:00+00:00"}
    telemetry.published(sender=OBSERVER, headers=headers, routing_key="structural_compute")
    request = SimpleNamespace(name=OBSERVER, id="observation", _request_dict=headers,
                              expires=headers["expires"])
    telemetry.received(request=request)
    sender = SimpleNamespace(name=OBSERVER, request=Context(headers))
    telemetry.started(sender=sender, task_id=request.id)
    telemetry.finished(sender=sender, task_id=request.id, state="SUCCESS")
    telemetry.discarded(request=request, expired=True, terminated=False)
    assert [e for e, _ in events] == ["publish_attempt", "received", "started", "finished", "discarded"]
    assert events[1][1]["queue_wait_seconds"] >= 0
    assert events[2][1]["receive_to_start_seconds"] >= 0
    assert events[2][1]["publish_to_start_seconds"] >= 0
    assert events[3][1]["execution_seconds"] >= 0
    assert events[-1][1]["expired"] is True
    assert events[-1][1]["expires_at"] == headers["expires"]
    assert not any("args" in fields or "kwargs" in fields for _, fields in events)


def test_telemetry_handles_old_messages_and_unrelated_tasks(monkeypatch):
    events = []
    monkeypatch.setattr(telemetry, "emit", lambda *a, **kw: events.append(kw))
    headers = {}
    telemetry.published(sender="other.task", headers=headers)
    telemetry.started(sender=SimpleNamespace(name="other.task"))
    assert not headers and not events
    now = datetime.now(timezone.utc)
    assert telemetry.elapsed(None, now) is None
    assert telemetry.elapsed("malformed", now) is None
    assert telemetry.elapsed("2026-11-26T12:00:00", now) is None
