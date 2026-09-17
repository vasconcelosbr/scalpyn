"""S3 (2026-09-17 shadow-trade collapse fix): the L3 continuation closure
path never generated a canonical measurement revision, unlike the legacy
price-monitor path (TX3 in ``_record_simulation_one_async``). The doc's
UNI_USDT/b13d595f case had no revision at all -- and the reconciler only
ever retried a *PENDING* one, so a shadow with zero rows was invisible to
it too.

Covered here:
  * ``_record_measurement_one_async`` (the extracted, shared TX3) generates
    a revision for a COMPLETED shadow and is a no-op otherwise.
  * ``_reconcile_pending_measurements_async`` now also selects L3-managed
    shadows with NO revision row at all, not just PENDING ones.
"""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest


class _TxCtx:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *a):
        return False


class _StubDb:
    def __init__(self, shadow):
        self._shadow = shadow

    def begin(self):
        return _TxCtx()

    async def execute(self, _stmt):
        result = SimpleNamespace(scalar_one_or_none=lambda: self._shadow)
        return result


class _SessionCtx:
    def __init__(self, shadow):
        self._shadow = shadow

    async def __aenter__(self):
        return _StubDb(self._shadow)

    async def __aexit__(self, *a):
        return False


def _make_completed_l3_shadow():
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=uuid4(), symbol="UNI_USDT", source="L3", status="COMPLETED",
        entry_price=100.0, exit_price=101.0, entry_timestamp=now, exit_timestamp=now,
        config_snapshot={"shadow_measurement_timeframe_priority": ["1m"], "shadow_entry_max_lag_seconds": 5},
    )


@pytest.mark.asyncio
async def test_record_measurement_one_async_generates_revision_for_completed_shadow():
    from app.tasks import shadow_trade_monitor
    from app import database as app_database

    shadow = _make_completed_l3_shadow()
    calls = []

    async def _fake_build(db, sh, **kwargs):
        calls.append(sh.id)
        return {"fake": "revision"}

    async def _fake_persist(db, values):
        assert values == {"fake": "revision"}
        return True

    with patch.object(app_database, "CeleryAsyncSessionLocal", lambda: _SessionCtx(shadow)), \
         patch("app.services.shadow_trade_measurement_service.build_measurement_revision", new=_fake_build), \
         patch("app.services.shadow_trade_measurement_service.persist_measurement_revision", new=_fake_persist):
        await shadow_trade_monitor._record_measurement_one_async(shadow.id)

    assert calls == [shadow.id]


@pytest.mark.asyncio
async def test_record_measurement_one_async_noop_when_not_completed():
    from app.tasks import shadow_trade_monitor
    from app import database as app_database

    shadow = _make_completed_l3_shadow()
    shadow.status = "RUNNING"
    build = AsyncMock()

    with patch.object(app_database, "CeleryAsyncSessionLocal", lambda: _SessionCtx(shadow)), \
         patch("app.services.shadow_trade_measurement_service.build_measurement_revision", new=build):
        await shadow_trade_monitor._record_measurement_one_async(shadow.id)

    build.assert_not_called()


@pytest.mark.asyncio
async def test_record_measurement_one_async_swallows_build_failure():
    """Best-effort: a failure here must never propagate -- the shadow's
    closure was already committed by the caller before this runs.
    """
    from app.tasks import shadow_trade_monitor
    from app import database as app_database

    shadow = _make_completed_l3_shadow()

    async def _boom(db, sh, **kwargs):
        raise RuntimeError("candle lookup exploded")

    with patch.object(app_database, "CeleryAsyncSessionLocal", lambda: _SessionCtx(shadow)), \
         patch("app.services.shadow_trade_measurement_service.build_measurement_revision", new=_boom):
        await shadow_trade_monitor._record_measurement_one_async(shadow.id)  # must not raise


@pytest.mark.asyncio
async def test_reconcile_pending_measurements_combines_pending_and_never_measured():
    from app.tasks import shadow_trade_monitor
    from app import database as app_database

    pending_id, never_measured_id = uuid4(), uuid4()

    class _SelectResult:
        def __init__(self, ids):
            self._ids = ids

        def scalars(self):
            return self._ids

    class _SelectDb:
        async def execute(self, stmt, *_args):
            sql = str(stmt)
            if "shadow_trade_measurement_revisions" in sql and "shadow_trades" not in sql:
                return _SelectResult([pending_id])
            return _SelectResult([never_measured_id])

    class _SelectSessionCtx:
        async def __aenter__(self):
            return _SelectDb()

        async def __aexit__(self, *a):
            return False

    processed = []

    class _MeasureDb:
        def __init__(self, shadow_id):
            self._shadow_id = shadow_id

        def begin(self):
            return _TxCtx()

        async def execute(self, _stmt):
            processed.append(self._shadow_id)
            shadow = SimpleNamespace(id=self._shadow_id, status="COMPLETED", config_snapshot={})
            return SimpleNamespace(scalar_one_or_none=lambda: shadow)

    class _MeasureSessionCtx:
        def __init__(self, shadow_id):
            self._shadow_id = shadow_id

        async def __aenter__(self):
            return _MeasureDb(self._shadow_id)

        async def __aexit__(self, *a):
            return False

    session_calls = {"n": 0}

    def _session_factory():
        session_calls["n"] += 1
        if session_calls["n"] == 1:
            return _SelectSessionCtx()
        shadow_id = pending_id if session_calls["n"] == 2 else never_measured_id
        return _MeasureSessionCtx(shadow_id)

    async def _fake_build(db, sh, **kwargs):
        return {"fake": "revision"}

    async def _fake_persist(db, values):
        return True

    with patch.object(app_database, "CeleryAsyncSessionLocal", _session_factory), \
         patch("app.services.shadow_trade_measurement_service.build_measurement_revision", new=_fake_build), \
         patch("app.services.shadow_trade_measurement_service.persist_measurement_revision", new=_fake_persist):
        result = await shadow_trade_monitor._reconcile_pending_measurements_async()

    assert result["selected"] == 2
    assert result["never_measured"] == 1
    assert result["inserted"] == 2
    assert set(processed) == {pending_id, never_measured_id}
