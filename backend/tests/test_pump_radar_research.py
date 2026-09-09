from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.services.pump_radar_research import (
    closed_history, load_user_universe, reconstruct_indicators,
)
from app.api.pump_radar import get_chart
from app.tasks.pump_radar import _anchor_features, _range_samples, _snapshots


def candle(at, value=10):
    return SimpleNamespace(open_time=at, close_time=at + timedelta(minutes=5),
        is_closed=True, open=value, high=value + 1, low=value - 1, close=value,
        volume_base=100, volume_quote=1000)


def test_1235_never_consumes_1235_to_1240_candle():
    at = datetime(2026, 9, 8, 12, 35, tzinfo=timezone.utc)
    rows = [candle(at - timedelta(minutes=5 * i)) for i in range(30, -2, -1)]
    first, history = reconstruct_indicators(rows, at, "5m", {"ema": {"enabled": True, "periods": [9, 21]}})
    assert history[-1].open_time == at - timedelta(minutes=5)
    rows[-1].close = 99999
    rows[-2].close = 99999
    second, _ = reconstruct_indicators(rows, at, "5m", {"ema": {"enabled": True, "periods": [9, 21]}})
    assert first == second
    assert first["ema9"] == 10


def test_gaps_break_warmup_and_unclosed_candles_are_excluded():
    at = datetime(2026, 9, 8, 12, 35, tzinfo=timezone.utc)
    rows = [candle(at - timedelta(minutes=m)) for m in (25, 20, 10, 5, 0)]
    assert [r.open_time for r in closed_history(rows, at, "5m")] == [rows[2].open_time, rows[3].open_time]
    rows[3].is_closed = False
    assert closed_history(rows, at, "5m") == []


def test_reconstruction_never_emits_flow_book_or_nonfinite_values():
    at = datetime(2026, 9, 8, 12, 35, tzinfo=timezone.utc)
    rows = [candle(at - timedelta(minutes=m)) for m in (10, 5)]
    config = {"taker_ratio": {"enabled": True}}
    with patch("app.services.pump_radar_research.FeatureEngine") as engine:
        engine.return_value.calculate.return_value = {"rsi": 55, "cvd": 100, "taker_ratio": 0.6, "volume_delta": 9, "spread_pct": 1, "adx": float("nan")}
        result, _ = reconstruct_indicators(rows, at, "5m", config)
        assert result == {"rsi": 55}
        assert engine.call_args.args[0]["taker_ratio"]["enabled"] is False
        assert config["taker_ratio"]["enabled"] is True
    assert reconstruct_indicators(rows, at, "5m", None)[0] == {}


@pytest.mark.asyncio
async def test_universe_preserves_memberships_and_scopes_both_queries_by_user():
    user, pool, watchlist = uuid4(), uuid4(), uuid4()
    db = SimpleNamespace(execute=AsyncMock(side_effect=[
        SimpleNamespace(all=lambda: [("BTC/USDT", pool)]),
        SimpleNamespace(all=lambda: [("BTC_USDT", watchlist, "L3"), ("ETH_USDT", watchlist, "L2")]),
    ]))
    members = await load_user_universe(db, user)
    assert [m["symbol"] for m in members] == ["BTC_USDT", "ETH_USDT"]
    assert [m["level"] for m in members[0]["memberships"]] == ["POOL", "L3"]
    for call in db.execute.call_args_list:
        query = call.args[0].compile()
        assert user in query.params.values()
        assert "spot" in query.params.values()


@pytest.mark.asyncio
async def test_chart_reconstructs_late_capture_but_hides_future_markers():
    at = datetime(2026, 9, 8, 12, 35, tzinfo=timezone.utc)
    event = SimpleNamespace(id=uuid4(), symbol="BTC_USDT", start_at=at,
        end_at=at + timedelta(hours=1), confirmed_market_at=at + timedelta(minutes=10),
        peak_at=at + timedelta(minutes=30), start_price=10, peak_price=12, end_price=11)
    row = candle(at - timedelta(minutes=5))
    row.available_at = at + timedelta(days=1)
    row.quality_status = "VALID"
    row.contract_version = "test"
    row.provenance = {"historical_availability_proven": False}
    db = SimpleNamespace(execute=AsyncMock(side_effect=[
        SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [row])),
        SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [row])),
        SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [row])),
        SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [])),
    ]))
    with patch("app.api.pump_radar._owned_event", AsyncMock(return_value=event)):
        response = await get_chart(uuid4(), event.id, selected_at=None, hide_future=True, db=db, user_id=uuid4())
    assert response["data"]["selected_at"] == at.isoformat()
    assert response["data"]["reconstruction_status"] == "RECONSTRUCTED"
    assert [m["kind"] for m in response["data"]["markers"]] == ["START"]
    for call in db.execute.call_args_list[:3]:
        query = call.args[0].compile()
        assert "pump_radar_ohlcv.close_time <=" in str(query)
        assert at in query.params.values()
        assert "available_at IS NULL" not in str(query)


@pytest.mark.asyncio
async def test_event_without_shadow_still_persists_reconstructed_snapshots():
    at = datetime(2026, 9, 8, 12, 35, tzinfo=timezone.utc)
    event = SimpleNamespace(id=uuid4(), start_at=at)
    run = SimpleNamespace(id=uuid4(), user_id=uuid4(), date_from=at - timedelta(hours=1),
        date_to=at + timedelta(hours=1), config_snapshot={},
        provenance={"indicators_config": {"ema": {"enabled": True, "periods": [9]}}})
    rows = [candle(at - timedelta(minutes=5 * i)) for i in range(50, 0, -1)]
    inserted = []

    async def execute(stmt):
        table = getattr(getattr(stmt, "table", None), "name", "")
        if table == "pump_radar_indicator_snapshots":
            inserted.append(table)
            return SimpleNamespace(scalar_one_or_none=lambda: uuid4())
        if table == "pump_radar_indicator_values":
            inserted.append(table)
        sql = str(stmt)
        if "JOIN pump_radar_event_links" in sql:
            return SimpleNamespace(all=lambda: [])
        data = rows if "FROM pump_radar_ohlcv" in sql else [event]
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: data))

    db = SimpleNamespace(get=AsyncMock(return_value=run), execute=AsyncMock(side_effect=execute), commit=AsyncMock())
    session = AsyncMock()
    session.__aenter__.return_value = db
    with patch("app.database.CeleryAsyncSessionLocal", return_value=session), \
         patch("app.tasks.pump_radar._cancel_asset_if_requested", AsyncMock(return_value=False)), \
         patch("app.tasks.pump_radar._refresh_run", AsyncMock()), \
         patch("app.tasks.pump_radar.enqueue"):
        result = await _snapshots(run.id, "BTC_USDT")
    assert result["snapshots"] == 5
    assert inserted.count("pump_radar_indicator_snapshots") == 5
    assert inserted.count("pump_radar_indicator_values") == 5


def test_control_features_do_not_use_anchor_candle_or_unclosed_btc():
    at = datetime(2026, 9, 8, 12, 35, tzinfo=timezone.utc)
    rows = [candle(at - timedelta(minutes=5 * i)) for i in range(300, -1, -1)]
    btc = {r.close_time: r for r in rows}
    first = _anchor_features(rows, 300, btc)
    assert first is not None
    rows[-1].high = 9999
    rows[-1].close = 9999
    assert _anchor_features(rows, 300, btc) == first


def test_statistics_count_distinct_events_and_controls_and_keep_split_separate():
    at = datetime(2026, 9, 8, 12, tzinfo=timezone.utc)
    event = SimpleNamespace(id=uuid4(), start_at=at)
    controls = [SimpleNamespace(symbol="BTC_USDT", anchor_at=at - timedelta(hours=i),
        match_features={"event": {"rsi": 50}, "control": {"rsi": 40}},
        outcome={"followup_minutes": 180}) for i in (4, 5, 6)]
    future_control = SimpleNamespace(symbol="BTC_USDT", anchor_at=at + timedelta(days=1),
        match_features={"event": {"rsi": 50}, "control": {"rsi": 99}}, outcome={"followup_minutes": 180})
    pairs = [(control, event) for control in controls + [controls[0], future_control]]
    pumps, matched = _range_samples(pairs, "rsi", (at + timedelta(days=1)).date(), False)
    assert pumps == [50]
    assert matched == [40, 40, 40]
