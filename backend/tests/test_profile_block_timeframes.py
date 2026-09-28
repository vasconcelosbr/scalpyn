from copy import deepcopy
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock

import pandas as pd
import pytest

from app.services.block_engine import BlockEngine
from app.services.profile_engine import ProfileEngine
from app.services.profile_indicator_contract import validate_profile_execution_structure
from app.services.pipeline_rejections import evaluate_rejections
from app.services.block_condition_timeframe import prepare_block_candle_inputs
from app.services.l3_authorization_contract_v3 import build_authorization_contract, build_feature_registry
from app.tasks.compute_mtf_indicators import _governed_envelopes

NOW = datetime(2026, 9, 28, tzinfo=timezone.utc)


def condition(indicator="rsi_6", timeframe="1m", **kw):
    return {"indicator": indicator, "type": "threshold", "operator": ">", "value": 60,
            "source": "ohlcv", "timeframe": timeframe, "period": 6,
            "source_provider": "gate.io", "provider_policy_id": "spot_gate_closed_ohlcv_v1",
            "candle_policy": "CLOSED_ONLY", "max_age_seconds": 360, **kw}


def profile(conditions):
    return {"default_timeframe": "5m", "scoring": {"enabled": False},
            "filters": {"conditions": []}, "signals": {"conditions": []}, "entry_triggers": {"conditions": []},
            "block_rules": {"blocks": [{"id": "test", "name": "test", "logic": "AND", "conditions": conditions}]}}


def candidate(indicator, timeframe, actual, **kw):
    return {"indicator": indicator, "timeframe": timeframe, "actual": actual,
            "source": "ohlcv", "source_provider": "gate.io", "provider_policy_id": "spot_gate_closed_ohlcv_v1",
            "candle_policy": "CLOSED_ONLY", "candle_closed": True, "period": 6,
            "computed_at": NOW, "source_timestamp": NOW, "available_at": NOW,
            "market_scope": {"exchange": "gate_io", "market_type": "spot", "normalized_symbol": "TEST_USDT"},
            "age_seconds": 0, "stale": False, **kw}


def test_block_timeframes_choose_exact_series_at_prefilter_engine_and_final_contract():
    config = profile([condition()])
    asset = {"symbol": "TEST_USDT", "rsi_6": 10, "indicators": {"rsi_6": 10},
             "_merged_indicators": SimpleNamespace(candidates=[candidate("rsi_6", "5m", 10), candidate("rsi_6", "1m", 80)])}
    passed, rejected = evaluate_rejections([asset], profile_config=config, stage="L3", profile_id="p")
    assert not passed and rejected
    assert ProfileEngine(config).evaluate_asset(asset)["blocked"] is True
    result = build_authorization_contract(asset=asset, profile_config=config, legacy_decision="BLOCK", evaluated_at=NOW,
                                          profile_id="fixture", profile_name="fixture", profile_version=NOW)
    assert result["authorization_status"] == "STRATEGY_BLOCK"
    assert result["block_rules_audit"]["blocked"] is True
    assert result["profile_lineage"]["rules_snapshot"]["block_rules"]["blocks"][0]["conditions"][0]["timeframe"] == "1m"


def test_same_indicator_in_multiple_blocks_does_not_share_the_last_timeframe():
    blocks = [{"id": tf, "name": tf, "conditions": [condition(timeframe=tf)]} for tf in ("1m", "5m")]
    asset = {"symbol": "TEST_USDT", "rsi_6": 80, "_indicators_by_tf": {"1m": {"rsi_6": 80}, "5m": {"rsi_6": 10}}}
    result = BlockEngine({"blocks": blocks}).evaluate(asset)
    assert result["triggered_blocks"] == ["1m"]


@pytest.mark.parametrize("value", [True, False, None])
def test_higher_highs_boolean_choices_preserve_missing_and_m15(value):
    for wanted in (True, False):
        c = condition("higher_highs_5", "15m", period=None, type="boolean", operator="is_true" if wanted else "is_false", value=wanted)
        config = profile([c])
        assert validate_profile_execution_structure(config) == []
        asset = {"higher_highs_5": not wanted, "_indicators_by_tf": {"15m": {"higher_highs_5": value}}}
        result = BlockEngine(config["block_rules"]).evaluate(asset)
        assert result["blocked"] is (value is wanted)
        assert bool(result["skipped_blocks"]) is (value is None)


def test_missing_requested_timeframe_never_uses_flat_or_other_series():
    result = BlockEngine(profile([condition()])["block_rules"]).evaluate({"rsi_6": 99, "_indicators_by_tf": {"5m": {"rsi_6": 99}}})
    assert not result["blocked"] and result["skipped_blocks"] == ["test"]


@pytest.mark.asyncio
async def test_exact_block_inputs_are_loaded_before_gates_reused_and_isolated(monkeypatch):
    from app.services import indicators_provider
    source = {"symbol": "TEST_USDT", "indicators": {"rsi_6": 10}}
    before = deepcopy(source)
    merged = SimpleNamespace(candidates=[candidate("rsi_6", "1m", 80)], as_flat_dict=lambda: {"rsi_6": 80})
    fetch = AsyncMock(return_value={"TEST_USDT": merged})
    monkeypatch.setattr(indicators_provider, "get_timeframe_indicators", fetch)
    config = profile([condition()])
    prepared = await prepare_block_candle_inputs(object(), [source], config)
    fetch.assert_awaited_once_with(ANY, ["TEST_USDT"], timeframe="1m", market_type="spot")
    assert source == before
    assert ProfileEngine(config).evaluate_asset(prepared[0])["blocked"]
    assert any(c["timeframe"] == "1m" for c in build_feature_registry(prepared[0], evaluated_at=NOW))
    await prepare_block_candle_inputs(object(), prepared, config)
    assert fetch.await_count == 1


def test_mtf_producer_emits_exact_block_calculation_identities_and_bb_distance():
    from app.services.feature_engine import FeatureEngine
    cfg = {"rsi": {"enabled": True, "period": 14, "periods": [6]},
           "adx": {"enabled": True, "period": 10}, "macd": {"enabled": True, "fast": 8, "slow": 21, "signal": 5},
           "bollinger": {"enabled": True, "period": 18, "deviation": 2.5}}
    close = pd.Series([100 + i / 10 + (-1)**i for i in range(100)])
    frame = pd.DataFrame({"close": close, "open": close, "high": close + 2, "low": close - 2, "volume": 1})
    values = FeatureEngine(cfg).calculate(frame, market_data=None)
    upper = close.tail(18).mean() + 2.5 * close.tail(18).std(ddof=0)
    assert values["bb_upper_distance_pct"] == round((close.iloc[-1] - upper) / upper * 100, 4)
    wrapped = _governed_envelopes(values, timeframe="1m", source_timestamp=NOW, source_provider="gate.io",
                                 config=cfg, config_identity={}, computed_at=NOW, capture_contract_version="fixture", source_ingested_at=NOW)
    assert wrapped["rsi_6"]["period"] == 6
    assert wrapped["rsi_6"]["parameters"] == {}
    assert wrapped["adx_slope_3"]["period"] == 10
    assert wrapped["macd_hist_slope_3"]["parameters"] == {"fast": 8, "slow": 21, "signal": 5}
    assert wrapped["higher_highs_5"]["period"] is None
    assert wrapped["bb_upper_distance_pct"]["parameters"] == {"deviation": 2.5}


def test_request_bound_rsi6_uses_closed_contiguous_m1_candles_and_rejects_bad_inputs():
    from app.services.indicators_provider import closed_block_rsi6_candidate
    from app.services.feature_engine import FeatureEngine
    cfg = {"rsi": {"enabled": True, "period": 14, "periods": [6]}}
    rows = [{"time": NOW - timedelta(minutes=30-i), "close": 100 + i + (-1)**i * 3,
             "exchange": "gate.io", "is_closed": True, "ingested_at": NOW,
             "capture_contract_version": "fixture"} for i in range(30)]
    result = closed_block_rsi6_candidate(rows, cfg, {"config_hash": "fixture"}, now=NOW, required=30)
    expected = FeatureEngine(cfg)._calc_rsi(pd.DataFrame({"close": [r["close"] for r in rows]}))
    assert result["actual"] == expected["rsi_6"]
    assert result["actual"] != expected["rsi"]
    assert result["timeframe"] == "1m" and result["period"] == 6
    for key, value in (("is_closed", False), ("time", NOW), ("ingested_at", None), ("capture_contract_version", None)):
        bad = deepcopy(rows)
        bad[-1][key] = value
        assert closed_block_rsi6_candidate(bad, cfg, {}, now=NOW, required=30) is None
    assert closed_block_rsi6_candidate(rows[1:], cfg, {}, now=NOW, required=30) is None
