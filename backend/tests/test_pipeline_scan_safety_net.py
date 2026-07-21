from unittest.mock import patch

from app.tasks import pipeline_scan
from app.tasks.celery_app import celery_app


def test_beat_uses_lightweight_pipeline_safety_net():
    entry = celery_app.conf.beat_schedule["pipeline_scan_safety_net"]
    assert entry["task"] == "app.tasks.pipeline_scan.safety_net"
    assert not any(
        item["task"] == "app.tasks.pipeline_scan.scan"
        for item in celery_app.conf.beat_schedule.values()
    )


def test_safety_net_skips_when_scan_succeeded_recently():
    with (
        patch.object(pipeline_scan, "_last_success_age_seconds", return_value=30.0),
        patch("app.tasks.task_dispatch.enqueue") as enqueue,
    ):
        result = pipeline_scan.safety_net.run()

    assert result == {"status": "skipped", "last_success_age_seconds": 30.0}
    enqueue.assert_not_called()


def test_safety_net_enqueues_stale_scan_with_full_budget_lock():
    with (
        patch.object(pipeline_scan, "_last_success_age_seconds", return_value=900.0),
        patch("app.tasks.task_dispatch.enqueue", return_value="task-123") as enqueue,
    ):
        result = pipeline_scan.safety_net.run()

    assert result == {
        "status": "enqueued",
        "last_success_age_seconds": 900.0,
        "task_id": "task-123",
    }
    enqueue.assert_called_once_with(
        "app.tasks.pipeline_scan.scan",
        dedup_key="pipeline_scan",
        ttl_seconds=660,
        expires_seconds=600,
    )


def test_scan_coalesces_stale_duplicate_after_recent_success():
    with (
        patch.object(pipeline_scan, "_last_success_age_seconds", return_value=30.0),
        patch.object(pipeline_scan, "_run_async") as run_async,
    ):
        result = pipeline_scan.scan.run()

    assert result == {
        "status": "skipped_recent_success",
        "last_success_age_seconds": 30.0,
    }
    run_async.assert_not_called()
