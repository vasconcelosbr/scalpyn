"""Fresh candle publication cannot queue behind MTF/Shadow observation."""
from datetime import datetime, timezone

import pytest

from app.tasks.celery_app import (
    QUEUE_INDICATOR_5M, TASK_ROUTES, _configured_task_modules, celery_app,
)


def test_m5_publishers_are_isolated_from_slow_observers():
    publishers = {
        "app.tasks.compute_indicators.compute_5m",
        "app.tasks.compute_indicators.compute_structural_5m",
    }
    assert {task for task, route in TASK_ROUTES.items()
            if route["queue"] == QUEUE_INDICATOR_5M} == publishers
    for task in publishers:
        routed = celery_app.amqp.router.route({}, task)
        assert routed["queue"].name == QUEUE_INDICATOR_5M
    for task in ("app.tasks.mtf_chart.observe", "app.tasks.shadow_l3_continuation.run",
                 "app.tasks.shadow_trade_monitor.run"):
        assert TASK_ROUTES[task]["queue"] != QUEUE_INDICATOR_5M


def test_m5_worker_rejects_shared_consumers(monkeypatch):
    monkeypatch.setenv("WORKER_QUEUES", QUEUE_INDICATOR_5M)
    assert _configured_task_modules() == ("app.tasks.compute_indicators",)
    monkeypatch.setenv("WORKER_QUEUES", f"{QUEUE_INDICATOR_5M},structural_compute")
    with pytest.raises(ValueError, match="dedicated worker"):
        _configured_task_modules()


def test_capture_poll_is_aligned_independently_of_beat_startup():
    schedule = celery_app.conf.beat_schedule["collect_state_5m_every_60s"]["schedule"]
    # A process starting at :20 must next poll at :00, not carry a permanent
    # twenty-second delay past the capture contract's finalization boundary.
    last = datetime(2026, 11, 17, 12, 0, 20, tzinfo=timezone.utc)
    assert last + schedule.remaining_delta(last)[1] == datetime(
        2026, 11, 17, 12, 1, tzinfo=timezone.utc)
