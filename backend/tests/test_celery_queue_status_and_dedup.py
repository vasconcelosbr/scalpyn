"""Task #216 (operator spec parts 5, 7, 8) — behavioural tests.

Three groups of tests, each enforcing a Step-8 contract from the task plan:

    1. Dedup wrapper claim/release. Two concurrent enqueues sharing a
       ``dedup_key`` produce one ``send_task`` call + one DEDUP_SKIP;
       the postrun signal handler releases the lock so the next cycle
       can claim it again.

    2. ``/api/system/celery-status`` payload shape. The endpoint must
       expose the legacy ``queue_depth`` scalar (back-compat for
       Task #186 dashboards), the per-queue ``queues`` mapping, and the
       flat ``queue_depth_by_queue`` / ``oldest_task_age_seconds_by_queue``
       mirrors required by the operator spec.

    3. Hysteresis cycle. Depth crosses ``QUEUE_ALERT_HIGH`` →
       exactly one CRITICAL is logged + one ``BackofficeAlert`` row
       is persisted. Depth dipping to 9k then back to 10001 must NOT
       re-fire (latch is armed). Only after depth drops below
       ``QUEUE_ALERT_LOW`` does the next crossing fire again.
"""

from __future__ import annotations

import importlib
import logging
from typing import Any
from unittest.mock import MagicMock

import pytest


# ── Group 1: dedup wrapper claim/release ───────────────────────────────────

class _FakeAsyncResult:
    id = "fake-async-id"


class _FakeCelery:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict, dict]] = []
        self.options: list[dict] = []

    def send_task(
        self,
        name,
        *_,
        args=(),
        kwargs=None,
        queue=None,
        headers=None,
        expires=None,
        **__,
    ):
        self.sent.append((name, dict(headers or {}), dict(kwargs or {})))
        self.options.append({"queue": queue, "expires": expires})
        return _FakeAsyncResult()


def _patch_redis(monkeypatch, store: dict[str, Any]) -> None:
    """Wire ``task_dispatch._redis_client`` to a tiny in-memory fake."""
    from app.tasks import task_dispatch as td

    class _MiniRedis:
        def set(self, key, value, nx=False, ex=None):
            if nx and key in store:
                return False
            store[key] = value
            return True

        def delete(self, key):
            store.pop(key, None)

        def eval(self, _script, _numkeys, key, expected):
            if store.get(key) != expected:
                return 0
            store.pop(key, None)
            return 1

        def get(self, key):
            return store.get(key)

    monkeypatch.setattr(td, "_redis_client", lambda: _MiniRedis())


def test_dedup_second_enqueue_is_dropped(monkeypatch, caplog):
    """Two enqueues with the same ``dedup_key`` → one send_task call,
    second logs ``DEDUP_SKIP`` and returns ``None``."""
    from app.tasks import task_dispatch as td
    from app.tasks import celery_app as celery_mod

    store: dict[str, Any] = {}
    _patch_redis(monkeypatch, store)
    fake = _FakeCelery()
    monkeypatch.setattr(celery_mod, "celery_app", fake)

    a = td.enqueue("app.tasks.compute_indicators.compute_5m",
                   dedup_key="compute_5m:BTC", ttl_seconds=60)
    with caplog.at_level(logging.INFO, logger="app.tasks.task_dispatch"):
        b = td.enqueue("app.tasks.compute_indicators.compute_5m",
                       dedup_key="compute_5m:BTC", ttl_seconds=60)

    assert a == "fake-async-id"
    assert b is None
    assert len(fake.sent) == 1
    assert any("DEDUP_SKIP" in r.message for r in caplog.records)


def test_enqueue_forwards_message_expiry(monkeypatch):
    from app.tasks import celery_app as celery_mod
    from app.tasks import task_dispatch as td

    store: dict[str, Any] = {}
    _patch_redis(monkeypatch, store)
    fake = _FakeCelery()
    monkeypatch.setattr(celery_mod, "celery_app", fake)

    td.enqueue(
        "app.tasks.pipeline_scan.scan",
        dedup_key="pipeline_scan",
        ttl_seconds=660,
        expires_seconds=600,
    )

    assert fake.options == [{"queue": None, "expires": 600}]


def test_dedup_postrun_signal_releases_lock(monkeypatch):
    """After postrun signal fires, a new enqueue with the same key
    can claim the lock again (lifecycle is correctly closed)."""
    from app.tasks import task_dispatch as td
    from app.tasks import celery_app as celery_mod

    store: dict[str, Any] = {}
    _patch_redis(monkeypatch, store)
    fake = _FakeCelery()
    monkeypatch.setattr(celery_mod, "celery_app", fake)

    td.enqueue("app.tasks.compute_indicators.compute_5m",
               dedup_key="compute_5m:ETH", ttl_seconds=60)
    assert any(k.endswith("compute_5m:ETH") for k in store)

    # Build a fake Celery task object whose request.headers carries
    # the dispatch header — same shape Celery passes to task_postrun.
    headers = fake.sent[0][1]
    fake_task = MagicMock()
    fake_task.request.headers = headers
    td._on_task_postrun(task=fake_task)

    assert not any(k.endswith("compute_5m:ETH") for k in store)

    second = td.enqueue("app.tasks.compute_indicators.compute_5m",
                        dedup_key="compute_5m:ETH", ttl_seconds=60)
    assert second == "fake-async-id"
    assert len(fake.sent) == 2


def test_stale_postrun_cannot_release_newer_lock(monkeypatch):
    from app.tasks import celery_app as celery_mod
    from app.tasks import task_dispatch as td

    store: dict[str, Any] = {}
    _patch_redis(monkeypatch, store)
    fake = _FakeCelery()
    monkeypatch.setattr(celery_mod, "celery_app", fake)

    td.enqueue("task.a", dedup_key="pipeline_scan", ttl_seconds=60)
    stale_headers = fake.sent[0][1]
    lock_key = next(key for key in store if key.endswith("pipeline_scan"))

    store.pop(lock_key)
    td.enqueue("task.b", dedup_key="pipeline_scan", ttl_seconds=60)
    newer_token = store[lock_key]

    stale_task = MagicMock()
    stale_task.request.headers = stale_headers
    td._on_task_postrun(task=stale_task)

    assert store[lock_key] == newer_token


def test_dedup_redis_unreachable_fails_open(monkeypatch, caplog):
    """If the Redis client cannot be built, the wrapper logs a WARNING
    and still dispatches the task — losing work is more dangerous than
    losing dedup."""
    from app.tasks import task_dispatch as td
    from app.tasks import celery_app as celery_mod

    monkeypatch.setattr(td, "_redis_client", lambda: None)
    fake = _FakeCelery()
    monkeypatch.setattr(celery_mod, "celery_app", fake)

    result = td.enqueue("app.tasks.compute_indicators.compute_5m",
                        dedup_key="any", ttl_seconds=60)
    assert result == "fake-async-id"
    assert len(fake.sent) == 1


def test_symbol_health_audit_has_backlog_recovery_capacity():
    from app.tasks.celery_app import celery_app

    annotation = celery_app.conf.task_annotations[
        "app.tasks.symbol_health_audit.monitor_only"
    ]
    assert annotation["rate_limit"] == "3/m"


# ── Group 2: status endpoint payload shape ─────────────────────────────────

class _MiniSyncRedis:
    """In-memory stand-in matching the small subset of ``redis.Redis`` that
    ``/api/system/celery-status`` exercises."""
    def __init__(self) -> None:
        self.lists: dict[str, list[str]] = {}
        self.kv: dict[str, str] = {}

    def llen(self, key):
        return len(self.lists.get(key, []))

    def lrange(self, key, start, end):
        items = self.lists.get(key, [])
        return items[start:end + 1] if end >= 0 else items[start:]

    def get(self, key):
        return self.kv.get(key)

    def set(self, key, value, ex=None):
        self.kv[key] = value
        return True


@pytest.mark.asyncio
async def test_celery_status_payload_includes_legacy_and_per_queue_keys(monkeypatch):
    """Endpoint payload must contain (a) legacy ``queue_depth`` scalar,
    (b) per-queue ``queues`` mapping with depth/oldest_age/alert_state,
    (c) flat ``queue_depth_by_queue`` and
    ``oldest_task_age_seconds_by_queue`` mirrors."""
    from app.api import system as system_mod
    from app.tasks.celery_app import ALL_QUEUES

    fake = _MiniSyncRedis()
    fake.lists["microstructure"] = ["msg"] * 3
    fake.lists["structural"] = ["msg"] * 7
    fake.lists["execution"] = []

    class _FakeRedisModule:
        @staticmethod
        def from_url(*_, **__):
            return fake

    monkeypatch.setitem(__import__("sys").modules, "redis", _FakeRedisModule)
    # Inspect probe should not make a real network call.
    monkeypatch.setattr(
        system_mod,
        "_peek_oldest_age_seconds",
        lambda _r, _q: 12.5,
    )
    # Suppress the BackofficeAlert side-effect for this shape test.
    async def _noop(*_a, **_kw): return None
    monkeypatch.setattr(system_mod, "_emit_backoffice_alert", _noop)

    class _FakeInspect:
        def ping(self): return None
        def active(self): return None
        def registered(self): return None

    class _FakeControl:
        def inspect(self, timeout=3): return _FakeInspect()

    class _FakeCeleryApp:
        control = _FakeControl()

    monkeypatch.setattr(
        "app.tasks.celery_app.celery_app",
        _FakeCeleryApp(),
        raising=False,
    )

    payload = await system_mod.get_celery_status()

    # Legacy back-compat scalar = sum of per-queue depths
    assert payload["queue_depth"] == 10

    # Spec'd flat mirrors
    assert set(payload["queue_depth_by_queue"]) == set(ALL_QUEUES)
    assert payload["queue_depth_by_queue"]["microstructure"] == 3
    assert payload["queue_depth_by_queue"]["structural"] == 7
    assert payload["queue_depth_by_queue"]["execution"] == 0
    assert set(payload["oldest_task_age_seconds_by_queue"]) == set(ALL_QUEUES)
    # Empty queue → no oldest age
    assert payload["oldest_task_age_seconds_by_queue"]["execution"] is None

    # Structured per-queue mapping
    for q in ALL_QUEUES:
        block = payload["queues"][q]
        assert "depth" in block and "oldest_age_s" in block and "alert_state" in block


# ── Group 3: hysteresis cycle ──────────────────────────────────────────────

def test_hysteresis_fires_once_per_crossing(monkeypatch, caplog):
    """Operator spec part 8: depth crossing 10k fires CRITICAL exactly
    once; depth dipping to 9k then climbing back to 10001 MUST NOT
    re-fire (the latch is still armed). Only after depth drops below
    8k does the next 10k crossing fire again."""
    from app.api import system as system_mod

    fake = _MiniSyncRedis()
    queue_name = "microstructure"

    with caplog.at_level(logging.CRITICAL, logger="app.api.system"):
        # Crossing 1: depth = 10001 → fires
        state, fired = system_mod._evaluate_queue_alert(fake, queue_name, 10_001)
        assert state == "alerted"
        assert fired is True

        # Re-evaluate at 10500 — still above HIGH but already alerted → no fire
        state, fired = system_mod._evaluate_queue_alert(fake, queue_name, 10_500)
        assert state == "alerted"
        assert fired is False

        # Drop to 9000 — above LOW (8000), latch stays armed, no re-arm
        state, fired = system_mod._evaluate_queue_alert(fake, queue_name, 9_000)
        assert state == "alerted"
        assert fired is False

        # Climb back to 10001 — still latched, MUST NOT re-fire
        state, fired = system_mod._evaluate_queue_alert(fake, queue_name, 10_001)
        assert state == "alerted"
        assert fired is False

        # Drop below LOW → re-arm
        state, fired = system_mod._evaluate_queue_alert(fake, queue_name, 7_000)
        assert state == "ok"
        assert fired is False

        # Crossing 2: now allowed to fire again
        state, fired = system_mod._evaluate_queue_alert(fake, queue_name, 10_001)
        assert state == "alerted"
        assert fired is True

    crit_count = sum(1 for r in caplog.records if r.levelno == logging.CRITICAL)
    assert crit_count == 2, (
        f"Expected exactly 2 CRITICAL log lines (one per real crossing), "
        f"got {crit_count}"
    )


# ── Group 4: oldest-age envelope parsing (acceptance criterion C) ──────────

def test_oldest_age_parses_dispatch_wrapper_header():
    """A realistic Celery Redis envelope, stamped by our
    ``task_dispatch.enqueue()`` wrapper with
    ``headers.x-scalpyn-enqueued-at``, must yield a non-null
    ``oldest_age_s`` so the operator dashboard never silently shows a
    stale ``null`` for a queue that actually has work."""
    import json
    from datetime import datetime, timedelta, timezone
    from app.api import system as system_mod

    fake = _MiniSyncRedis()
    enqueued_at = datetime.now(timezone.utc) - timedelta(seconds=42)
    envelope = {
        "body": "<base64-task-payload>",
        "content-encoding": "utf-8",
        "content-type": "application/json",
        "headers": {
            "id": "task-uuid-1",
            "task": "app.tasks.compute_indicators.compute_5m",
            "x-scalpyn-dedup-key": "celery:dedup:compute_5m:BTC",
            "x-scalpyn-enqueued-at": enqueued_at.isoformat(),
        },
        "properties": {
            "delivery_tag": "tag-1",
            "delivery_mode": 2,
        },
    }
    fake.lists["microstructure"] = [json.dumps(envelope)]

    age = system_mod._peek_oldest_age_seconds(fake, "microstructure")
    assert age is not None, (
        "oldest_age_s must be non-null when our enqueued-at header is "
        "present in the Redis envelope head — acceptance criterion C."
    )
    # Should be roughly 42 seconds; allow a wide margin for jitter so
    # this test is never flaky.
    assert 35 <= age <= 90, f"expected ~42s, got {age:.1f}s"


def test_oldest_age_falls_back_to_celery_properties_timestamp():
    """When a task did not go through our wrapper (legacy or external),
    the parser must still recover an age from Celery's stock
    ``properties.timestamp`` so coverage degrades gracefully rather
    than going dark."""
    import json
    from datetime import datetime, timedelta, timezone
    from app.api import system as system_mod

    fake = _MiniSyncRedis()
    ts = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    envelope = {
        "body": "<...>",
        "headers": {"id": "task-uuid-2", "task": "x.y.z"},
        "properties": {"timestamp": ts, "delivery_mode": 2},
    }
    fake.lists["structural"] = [json.dumps(envelope)]

    age = system_mod._peek_oldest_age_seconds(fake, "structural")
    assert age is not None
    assert 5 <= age <= 30


def test_oldest_age_returns_none_when_envelope_lacks_timestamp():
    """A malformed / timestampless envelope must return None rather
    than crashing the status endpoint."""
    import json
    from app.api import system as system_mod

    fake = _MiniSyncRedis()
    fake.lists["execution"] = [json.dumps({"body": "x", "headers": {}, "properties": {}})]
    assert system_mod._peek_oldest_age_seconds(fake, "execution") is None
