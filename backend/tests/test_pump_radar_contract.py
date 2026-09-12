from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.api.pump_radar import router
from app.models.pump_radar import (
    PumpRadarControl,
    PumpRadarEvent,
    PumpRadarEventLink,
    PumpRadarHypothesis,
    PumpRadarIndicatorSnapshot,
    PumpRadarIndicatorValue,
    PumpRadarOHLCV,
    PumpRadarRangeResult,
    PumpRadarRun,
    PumpRadarRunAsset,
)
from app.schemas.pump_radar import PumpRadarConfig
from app.tasks.celery_app import QUEUE_PUMP_RADAR, TASK_ROUTES
from app.tasks.pump_radar import (
    GATE_MAX_CANDLES_BACK,
    STALE_ASSET_TIMEOUT_MINUTES,
    TIMEFRAME_SECONDS,
    _clamp_capture_start,
    _earliest_fetchable,
    _reap_stale_assets,
    _refresh_run,
    _select_universe,
)


def test_config_is_observation_only_and_digest_is_stable() -> None:
    first = PumpRadarConfig()
    second = PumpRadarConfig.model_validate(first.model_dump())
    assert first.digest() == second.digest()
    assert first.operational_profile_mutation_enabled is False
    with pytest.raises(ValidationError):
        PumpRadarConfig.model_validate({"operational_profile_mutation_enabled": True})


def test_config_rejects_noncanonical_percentiles() -> None:
    with pytest.raises(ValidationError):
        PumpRadarConfig.model_validate({"range_percentiles": [10, 50, 90]})


def test_universe_is_bounded_by_quote_volume_with_stable_ties() -> None:
    tickers = [
        {"currency_pair": "CCC_USDT", "quote_volume": "10"},
        {"currency_pair": "BBB_USDT", "quote_volume": "20"},
        {"currency_pair": "AAA_USDT", "quote_volume": "20"},
        {"currency_pair": "DDD_USDT", "quote_volume": None},
    ]
    members = [{"symbol": item["currency_pair"]} for item in tickers]
    selected = _select_universe(tickers, PumpRadarConfig().universe_max_assets, members)
    assert [item["currency_pair"] for item in selected] == [
        "AAA_USDT", "BBB_USDT", "CCC_USDT", "DDD_USDT"
    ]
    assert [item["currency_pair"] for item in _select_universe(tickers, 2, members)] == [
        "AAA_USDT", "BBB_USDT"
    ]


def test_exchange_outsiders_never_enter_user_universe():
    tickers = [{"currency_pair": "OUT_USDT", "quote_volume": "999999"},
               {"currency_pair": "IN_USDT", "quote_volume": "1"}]
    assert _select_universe(tickers, 100, [{"symbol": "IN/USDT"}]) == [tickers[1]]
    assert _select_universe(tickers, 100, []) == []


def test_config_requires_all_native_context_timeframes():
    with pytest.raises(ValidationError):
        PumpRadarConfig(capture_timeframes=["5m"])


def test_all_tables_are_additive_and_isolated_from_operational_ohlcv() -> None:
    tables = {
        model.__tablename__
        for model in (
            PumpRadarOHLCV, PumpRadarRun, PumpRadarRunAsset, PumpRadarEvent,
            PumpRadarEventLink, PumpRadarIndicatorSnapshot, PumpRadarIndicatorValue,
            PumpRadarControl, PumpRadarRangeResult, PumpRadarHypothesis,
        )
    }
    assert len(tables) == 10
    assert all(name.startswith("pump_radar_") for name in tables)
    assert "ohlcv" not in tables


def test_public_contract_has_no_profile_apply_or_promote_endpoint() -> None:
    methods_by_path = {
        route.path: set(route.methods or set())
        for route in router.routes
    }
    required = {
        "/api/pump-radar/capabilities",
        "/api/pump-radar/config/schema",
        "/api/pump-radar/runs",
        "/api/pump-radar/runs/{run_id}/cancel",
        "/api/pump-radar/runs/{run_id}/assets",
        "/api/pump-radar/runs/{run_id}/events/{event_id}/chart",
        "/api/pump-radar/runs/{run_id}/events/{event_id}/snapshots",
        "/api/pump-radar/runs/{run_id}/comparisons",
        "/api/pump-radar/runs/{run_id}/ranges",
        "/api/pump-radar/runs/{run_id}/profiles",
        "/api/pump-radar/runs/{run_id}/hypotheses",
        "/api/pump-radar/runs/{run_id}/export",
    }
    assert required <= methods_by_path.keys()
    assert all("apply" not in path and "promote" not in path and "activate" not in path for path in methods_by_path)


def test_every_pump_radar_task_uses_isolated_queue() -> None:
    routes = {name: route for name, route in TASK_ROUTES.items() if name.startswith("app.tasks.pump_radar.")}
    assert {name.rsplit(".", 1)[-1] for name in routes} == {
        "inventory", "backfill_asset", "detect_asset", "associate_asset",
        "snapshots", "build_controls", "statistics", "reap_stale_assets",
    }
    assert all(route["queue"] == QUEUE_PUMP_RADAR for route in routes.values())


def test_earliest_fetchable_matches_gate_documented_history_limit() -> None:
    """Confirmed live against Gate.io (2026-09-10): requesting 5m candles
    further back than ~34.7 days returns INVALID_PARAM_VALUE "Candlestick
    too long ago. Maximum 10000 points ago are allowed" -- on the very
    first page, regardless of how the request is paginated. The clamp must
    track that limit per-interval (it scales linearly with candle width),
    with a safety margin under the documented 10000, never over it.
    """
    for timeframe, seconds in TIMEFRAME_SECONDS.items():
        before = datetime.now(timezone.utc)
        boundary = _earliest_fetchable(timeframe)
        after = datetime.now(timezone.utc)
        max_age = timedelta(seconds=seconds * GATE_MAX_CANDLES_BACK)
        assert before - max_age <= boundary <= after - max_age
        # Must stay strictly inside Gate's own 10000-point ceiling, not merely
        # equal to it -- computing the clamp takes nonzero time.
        assert boundary > datetime.now(timezone.utc) - timedelta(seconds=seconds * 10000)


def test_clamp_capture_start_rejects_the_180_day_default_that_broke_every_asset() -> None:
    """Reproduces the 2026-09-10 production incident: a backfill run with
    the schema's default backfill_days=180 requested 5m candles from ~180
    days ago and every one of 65 assets failed with the same Gate.io 400 on
    its first HTTP call. The clamp must pull that request inside the
    fetchable window instead of forwarding it verbatim.
    """
    naive_start_180_days_ago = datetime.now(timezone.utc) - timedelta(days=180)
    clamped = _clamp_capture_start(naive_start_180_days_ago, "5m")
    assert clamped > naive_start_180_days_ago
    assert abs((clamped - _earliest_fetchable("5m")).total_seconds()) < 1


def test_clamp_capture_start_leaves_recent_requests_untouched() -> None:
    recent_start = datetime.now(timezone.utc) - timedelta(days=1)
    assert _clamp_capture_start(recent_start, "5m") == recent_start


def test_clamp_scales_with_interval_width() -> None:
    """1h candles can reach ~416 days back; 5m only ~34.7. A backfill window
    that's within range for 1h context must not be silently truncated to
    the much tighter 5m limit, and vice versa.
    """
    ninety_days_ago = datetime.now(timezone.utc) - timedelta(days=90)
    assert _clamp_capture_start(ninety_days_ago, "5m") > ninety_days_ago
    assert _clamp_capture_start(ninety_days_ago, "1h") == ninety_days_ago


class _FakeRunRefreshResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeRunRefreshDB:
    """Records every statement _refresh_run issues, in order, and answers
    the group-by-status count query with a fixed asset status distribution.
    """

    def __init__(self, status_counts: dict[str, int]):
        self.status_counts = status_counts
        self.statements: list[str] = []

    async def execute(self, stmt):
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        self.statements.append(compiled)
        if "FOR UPDATE" in compiled.upper():
            return _FakeRunRefreshResult([])
        if compiled.strip().upper().startswith("SELECT"):
            return _FakeRunRefreshResult(list(self.status_counts.items()))
        return _FakeRunRefreshResult([])


@pytest.mark.asyncio
async def test_refresh_run_locks_the_run_row_before_counting_assets() -> None:
    """The fix for the 2026-09-10 stuck-run incident: the row lock must be
    acquired first, so a concurrent caller blocks until the previous one's
    write is committed instead of racing it with a stale count.
    """
    db = _FakeRunRefreshDB({"COMPLETED": 65})
    await _refresh_run(db, uuid4())
    assert len(db.statements) >= 2
    assert "FOR UPDATE" in db.statements[0].upper()


@pytest.mark.asyncio
async def test_refresh_run_flips_to_completed_only_once_every_asset_is_terminal() -> None:
    # 64 of 65 assets done, the 65th still mid-pipeline: no status transition yet.
    db = _FakeRunRefreshDB({"COMPLETED": 64, "CAPTURING": 1})
    await _refresh_run(db, uuid4())
    update_stmt = db.statements[-1]
    assert "status=" not in update_stmt.replace(" ", "").lower()

    db_done = _FakeRunRefreshDB({"COMPLETED": 65})
    await _refresh_run(db_done, uuid4())
    assert "COMPLETED" in db_done.statements[-1]


@pytest.mark.asyncio
async def test_refresh_run_reports_partial_when_any_asset_failed() -> None:
    db = _FakeRunRefreshDB({"COMPLETED": 60, "FAILED": 5})
    await _refresh_run(db, uuid4())
    assert "PARTIAL" in db.statements[-1]


@pytest.mark.asyncio
async def test_reap_stale_assets_fails_stuck_asset_and_retriggers_build_controls() -> None:
    """Reproduces the 2026-09-12 incident: run 6d8211cc stuck RUNNING for
    ~43h because BEAT_USDT's snapshots task never reached a terminal status
    (a worker SIGKILL bypasses every except-block in pump_radar.py, and
    acks_late=False means Celery never redelivers it). The reaper must fail
    the stuck asset and, since no _snapshots call will ever run again for
    this run to enqueue it, explicitly re-trigger build_controls itself.
    """
    run_id = uuid4()
    update_statements: list[str] = []

    async def execute(stmt):
        sql = str(stmt)
        if sql.strip().upper().startswith("UPDATE"):
            update_statements.append(str(stmt.compile(compile_kwargs={"literal_binds": True})))
            return SimpleNamespace()
        if "JOIN pump_radar_runs" in sql:
            return SimpleNamespace(all=lambda: [(run_id, "BEAT_USDT")])
        if "pump_radar_runs.status" in sql:
            # _refresh_run is mocked out below, so this simulates it having
            # already flipped the run to PARTIAL (1 failed, 64 completed).
            return SimpleNamespace(all=lambda: [(run_id, "PARTIAL")])
        return SimpleNamespace(all=lambda: [])

    db = SimpleNamespace(execute=AsyncMock(side_effect=execute), commit=AsyncMock())
    session = AsyncMock()
    session.__aenter__.return_value = db
    with patch("app.database.CeleryAsyncSessionLocal", return_value=session), \
         patch("app.tasks.pump_radar._refresh_run", AsyncMock()) as refresh_mock, \
         patch("app.tasks.pump_radar.enqueue") as enqueue_mock:
        result = await _reap_stale_assets()

    assert result == {"status": "ok", "reaped": 1, "runs_completed": 1}
    assert any("STALE_TASK_REAPED" in stmt and str(STALE_ASSET_TIMEOUT_MINUTES) in stmt for stmt in update_statements)
    refresh_mock.assert_awaited_once_with(db, run_id)
    enqueue_mock.assert_called_once_with(
        "app.tasks.pump_radar.build_controls",
        dedup_key=f"pump-radar:{run_id}:controls",
        ttl_seconds=900,
        queue=QUEUE_PUMP_RADAR,
        args=(str(run_id),),
    )


@pytest.mark.asyncio
async def test_reap_stale_assets_leaves_run_running_alone_when_nothing_is_stale() -> None:
    async def execute(stmt):
        sql = str(stmt)
        if "JOIN pump_radar_runs" in sql:
            return SimpleNamespace(all=lambda: [])
        return SimpleNamespace(all=lambda: [])

    db = SimpleNamespace(execute=AsyncMock(side_effect=execute), commit=AsyncMock())
    session = AsyncMock()
    session.__aenter__.return_value = db
    with patch("app.database.CeleryAsyncSessionLocal", return_value=session), \
         patch("app.tasks.pump_radar._refresh_run", AsyncMock()) as refresh_mock, \
         patch("app.tasks.pump_radar.enqueue") as enqueue_mock:
        result = await _reap_stale_assets()

    assert result == {"status": "ok", "reaped": 0, "runs_completed": 0}
    refresh_mock.assert_not_awaited()
    enqueue_mock.assert_not_called()
