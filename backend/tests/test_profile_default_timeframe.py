from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from app.services.profile_candles_4h import closed_4h_candidates
from app.services.profile_engine import ProfileEngine, _collect_required_timeframes
from app.services.profile_config_validation import validate_profile_config
from app.services.profile_indicator_contract import validate_profile_execution_structure


def profile():
    return {"default_timeframe": "4h", "scoring": {"enabled": False},
            "filters": {"conditions": [{"field": "rsi", "source": "ohlcv", "timeframe": "4h", "period": 14, "operator": ">", "value": 50},
                                       {"field": "market_cap", "operator": ">", "value": 1},
                                       {"field": "price", "operator": ">", "value": 10}]}}


def test_h4_profile_roundtrip_uses_exact_rsi_and_live_metadata():
    config = validate_profile_config(profile())
    assert validate_profile_execution_structure(config) == []
    assert "4h" in _collect_required_timeframes(config)
    engine = ProfileEngine(config)
    asset = {"symbol": "TEST_USDT", "price": 20, "market_cap": 2,
             "indicators": {"rsi": 99}, "_indicators_by_tf": {"4h": {"rsi": 30, "price": 1}, "5m": {"rsi": 99}}}
    engine._populate_cache_for_asset(asset)
    assert engine._apply_filters([asset], strict_indicators=True) == []
    asset["_indicators_by_tf"]["4h"]["rsi"] = 60
    engine._populate_cache_for_asset(asset)
    assert engine._apply_filters([asset], strict_indicators=True) == [asset]
    asset["_indicators_by_tf"]["4h"] = {}
    engine._populate_cache_for_asset(asset)
    assert engine._apply_filters([asset], strict_indicators=True) == []


@pytest.mark.asyncio
async def test_all_sections_load_h4_before_scoring_and_preserve_live_data(monkeypatch):
    from app.services import indicators_provider, profile_candles_4h
    from app.services.block_condition_timeframe import prepare_block_candle_inputs
    monkeypatch.setattr(indicators_provider, "get_timeframe_indicators", AsyncMock(return_value={}))
    fetch = AsyncMock(return_value={"TEST_USDT": [{"indicator": "rsi", "actual": 30, "timeframe": "4h"},
                                                {"indicator": "price", "actual": 1, "timeframe": "4h"}]})
    monkeypatch.setattr(profile_candles_4h, "get_closed_profile_4h", fetch)
    asset = {"symbol": "TEST_USDT", "price": 20, "market_cap": 2, "volume_delta": 123, "indicators": {"rsi": 99, "price": 20}}
    before = deepcopy(asset)
    for section in ("filters", "signals", "entry_triggers"):
        config = {"default_timeframe": "4h", section: profile()["filters"]}
        prepared = await prepare_block_candle_inputs(object(), [asset], config)
        assert prepared[0]["indicators"]["rsi"] == 30
        assert prepared[0]["price"] == 20
        assert prepared[0]["market_cap"] == 2
        assert prepared[0]["volume_delta"] == 123
        assert asset == before


def candles():
    now = datetime(2026, 11, 16, 0, 1, tzinfo=timezone.utc)
    rows = [{"time": now.replace(minute=0) - timedelta(hours=4 * (40 - i)),
             "open": 100 + i, "high": 105 + i, "low": 95 + i, "close": 101 + i + (-3 if i % 3 else 3),
             "volume": 100, "quote_volume": 10000, "is_closed": True} for i in range(41)]
    rows[-1]["is_closed"] = False
    df = pd.DataFrame(rows)
    df.attrs["exchange"] = "gate.io"
    return df, now


def test_h4_calculation_excludes_open_candle_and_rejects_gaps_mixed_source_and_stale():
    config = {"rsi": {"enabled": True, "period": 14}, "ema": {"enabled": True, "periods": [9]},
              "bollinger": {"enabled": True, "period": 20, "deviation": 2.0}}
    df, now = candles()
    values = closed_4h_candidates(df, config, {}, "TEST_USDT", now=now)
    assert {"rsi", "ema9", "bb_width"} <= {c["indicator"] for c in values}
    assert all(c["timeframe"] == "4h" and c["source_timestamp"] == now.replace(minute=0).isoformat() for c in values)
    df.loc[df.index[-1], "close"] = 1000000
    assert closed_4h_candidates(df, config, {}, "TEST_USDT", now=now) == values
    assert closed_4h_candidates(df.drop(df.index[-5]), config, {}, "TEST_USDT", now=now) == []
    assert closed_4h_candidates(df, config, {}, "TEST_USDT", now=now + timedelta(hours=4)) == []
    df.attrs["exchange"] = "binance"
    assert closed_4h_candidates(df, config, {}, "TEST_USDT", now=now) == []


@pytest.mark.asyncio
async def test_h4_configuration_and_cache_are_scoped_to_the_profile_owner(monkeypatch):
    from app.services import profile_candles_4h, redis_client
    from app.services.market_data_service import market_data_service
    rows = [{"id": "config-owner", "config_json": {"rsi": {"enabled": True, "period": 14}}}]
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: rows))))
    redis = SimpleNamespace(get=AsyncMock(return_value=None), set=AsyncMock())
    monkeypatch.setattr(redis_client, "get_async_redis", AsyncMock(return_value=redis))
    monkeypatch.setattr(market_data_service, "fetch_ohlcv", AsyncMock(return_value=None))
    assert await profile_candles_4h.get_closed_profile_4h(db, ["TEST_USDT"], user_id="owner-a") == {"TEST_USDT": []}
    assert db.execute.await_args.args[1] == {"user_id": "owner-a"}
    assert ":owner-a:" in redis.get.await_args.args[0]
    assert await profile_candles_4h.get_closed_profile_4h(db, ["TEST_USDT"], user_id=None) == {}


@pytest.mark.parametrize("actual, expected", [(49, "STRATEGY_BLOCK"), (51, "ALLOW")])
def test_h4_required_trigger_replay_uses_requested_series_without_order(actual, expected):
    from app.services.l3_authorization_contract_v3 import build_authorization_contract
    now = datetime(2026, 11, 16, tzinfo=timezone.utc)
    condition = {"id": "h4-rsi-required", "indicator": "rsi", "source": "ohlcv", "timeframe": "4h",
                 "source_provider": "gate.io", "provider_policy_id": "spot_gate_closed_ohlcv_v1",
                 "candle_policy": "CLOSED_ONLY", "period": 14, "max_age_seconds": 360,
                 "operator": ">", "value": 50, "required": True, "enabled": True}
    config = {"default_timeframe": "4h", "entry_triggers": {"logic": "AND", "conditions": [condition]},
              "filters": {"logic": "AND", "conditions": []}, "signals": {"logic": "AND", "conditions": []},
              "block_rules": {"blocks": []}}
    candidate = {**condition, "actual": actual, "candle_closed": True, "computed_at": now,
                 "source_timestamp": now, "available_at": now, "age_seconds": 0, "stale": False,
                 "market_scope": {"exchange": "gate_io", "market_type": "spot", "normalized_symbol": "TEST_USDT"}}
    result = build_authorization_contract(asset={"symbol": "TEST_USDT", "_block_ohlcv_candidates": [candidate]},
        profile_config=config, legacy_decision="ALLOW", evaluated_at=now,
        profile_id="fixture", profile_name="fixture", profile_version=now)
    assert result["authorization_status"] == expected
    assert result["entry_triggers_audit"]["conditions"][0]["condition_id"] == "h4-rsi-required"
    assert result["entry_triggers_audit"]["conditions"][0]["status"] == ("FAIL" if actual == 49 else "PASS")
    assert result["profile_lineage"]["rules_snapshot"]["entry_triggers"]["conditions"][0]["timeframe"] == "4h"
