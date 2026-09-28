import asyncio
import json
from copy import deepcopy
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from app.services.profile_config_validation import validate_profile_config
from app.services.profile_flow_window import profile_flow_window, validate_profile_flow_window
from app.services import order_flow_service, redis_client
from app.services.config_service import config_service
from app.tasks.pipeline_scan import _inject_live_order_flow


def test_profile_window_validation_keeps_legacy_and_rejects_conflicting_rules():
    assert profile_flow_window({}) is None
    config = {"l3_order_flow_window_seconds": 300, "signals": {"conditions": [
        {"field": "taker_ratio", "operator": ">", "value": 30, "window_seconds": 300},
    ]}}
    assert validate_profile_config(config)["signals"]["conditions"][0]["value"] == 30
    config["signals"]["conditions"][0]["window_seconds"] = 60
    with pytest.raises(ValueError, match="PROFILE_FLOW_WINDOW_MISMATCH"):
        validate_profile_config(config)
    for invalid in (True, 0, -1, "300", 301):
        with pytest.raises(ValueError, match="UNSUPPORTED"):
            profile_flow_window({"l3_order_flow_window_seconds": invalid})


def test_profile_window_rejects_candle_period_on_delta():
    with pytest.raises(ValueError, match="CANDLE_IDENTITY"):
        validate_profile_flow_window({"l3_order_flow_window_seconds": 300, "block_rules": {"blocks": [
            {"conditions": [{"indicator": "volume_delta", "window_seconds": 300, "period": 20}]},
        ]}})


def test_five_minute_aggregation_includes_older_trades_and_excludes_future(monkeypatch):
    now = 1_000_000.0
    monkeypatch.setattr(order_flow_service.time, "time", lambda: now)
    trades = [("buy", 50, 350), ("buy", 9, 240), ("sell", 1, 10), ("sell", 99, -1)]
    entries = [(json.dumps({"s": side, "a": amount, "t": (now-age)*1000}), (now-age)*1000)
               for side, amount, age in trades]
    client = AsyncMock()
    client.get.return_value = None
    client.zrange.return_value = [entries[0]]
    async def read(key, minimum, maximum):
        return [raw for raw, ts in entries if minimum <= ts <= maximum]
    client.zrangebyscore.side_effect = read
    monkeypatch.setattr(redis_client, "get_async_redis", AsyncMock(return_value=client))
    long = asyncio.run(order_flow_service.get_order_flow_data("TEST_USDT", 300, require_full_window=True))
    short = asyncio.run(order_flow_service.get_order_flow_data("TEST_USDT", 60))
    assert (long["taker_ratio"], long["volume_delta"]) == (0.9, 8)
    assert (short["taker_ratio"], short["volume_delta"]) == (0, -1)
    assert long["window_complete"] is True
    assert long["window_end_ms"] - long["window_start_ms"] == 300_000
    # Same recent trades with a capped/startup buffer cannot claim five minutes.
    client.zrange.return_value = [entries[1]]
    partial = asyncio.run(order_flow_service.get_order_flow_data("TEST_USDT", 300, require_full_window=True))
    assert partial["partial_window"] is True
    assert partial["window_complete"] is False
    # A handover inside the five-minute window also invalidates completeness.
    client.zrange.return_value = [entries[0]]
    client.get.return_value = str((now-120)*1000)
    handover = asyncio.run(order_flow_service.get_order_flow_data("TEST_USDT", 300, require_full_window=True))
    assert handover["window_complete"] is False


def test_profile_scoped_flow_injection_preserves_other_profiles(monkeypatch):
    monkeypatch.setattr(config_service, "get_config", AsyncMock(return_value={}))
    live = {"taker_window": "300s", "window_complete": True, "partial_window": False,
            "data_age_seconds": 1, "taker_ratio": 0.9, "volume_delta": 8,
            "taker_source": "gate_trades_ws_spot", "newest_trade_ms": 1_000_000}
    fetch = AsyncMock(return_value=live)
    monkeypatch.setattr(order_flow_service, "get_order_flow_data", fetch)
    args = dict(symbol="TEST_USDT", indicators={"taker_ratio": 0.1}, db=object(), user_id="u", pool_id=None)
    updated, ok = asyncio.run(_inject_live_order_flow(**args, profile_config={"l3_order_flow_window_seconds": 300}))
    assert ok and updated["taker_ratio"] == 0.9
    assert updated["_l3_live_order_flow_snapshot"]["meta"]["window_seconds"] == 300
    fetch.assert_awaited_with(symbol="TEST_USDT", window_seconds=300, require_full_window=True)
    asyncio.run(_inject_live_order_flow(**args, profile_config={}))
    fetch.assert_awaited_with(symbol="TEST_USDT", window_seconds=60)
    for bad in ({"window_complete": False}, {"taker_window": "60s"}, {"partial_window": True},
                {"taker_ratio": None}, {"data_age_seconds": None}):
        fetch.return_value = {**live, **bad}
        _, ok = asyncio.run(_inject_live_order_flow(**args, profile_config={"l3_order_flow_window_seconds": 300}))
        assert ok is False
    fetch.side_effect = RuntimeError("offline")
    assert asyncio.run(_inject_live_order_flow(**args, profile_config={"l3_order_flow_window_seconds": 300}))[1] is False
    assert asyncio.run(_inject_live_order_flow(**args, profile_config={}))[1] is True


def test_ema9_distance_uses_nine_closed_five_minute_bars():
    from app.services.price_position import calculate_price_position
    now = datetime(2026, 9, 28, 12, tzinfo=timezone.utc)
    closes = [100.] * 8 + [110., 999.]
    frame = pd.DataFrame({"time": pd.date_range(end=now, periods=10, freq="5min"),
                          "open": closes, "high": closes, "low": closes,
                          "close": closes, "volume": [1.] * 10})
    result = calculate_price_position(frame, as_of=now)
    # EMA alpha=2/(9+1)=0.2; eight flat closes, then 110 => EMA=102.
    assert result["ema9_distance_pct"] == round((110-102)/102*100, 4)
    changed = deepcopy(frame)
    changed.loc[9, "close"] = 1
    assert calculate_price_position(changed, as_of=now)["ema9_distance_pct"] == result["ema9_distance_pct"]
