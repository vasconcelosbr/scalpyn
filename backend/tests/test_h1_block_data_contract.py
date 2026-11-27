from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.pipeline_rejections import build_asset_evaluation_trace
from app.utils.indicator_merge import merge_indicator_rows
from app.tasks.compute_mtf_indicators import _governed_envelopes, required_warmup_candles
from app.services.feature_engine import FeatureEngine
from app.services.seed_service import DEFAULT_INDICATORS
from scripts.run_observation_beat import observation_schedule

NOW = datetime(2026, 11, 27, 14, 50, tzinfo=timezone.utc)


def rule(indicator="psar_trend", **extra):
    return {"indicator": indicator, "type": "threshold", "operator": "==", "value": "bearish",
            "source": "ohlcv", "timeframe": "1h", "source_provider": "gate.io",
            "provider_policy_id": "spot_gate_closed_ohlcv_v1", "max_age_seconds": 4320, **extra}


def trace(candidates, conditions=None):
    return build_asset_evaluation_trace({"symbol": "LEO_USDT", "psar_trend": "bearish",
        "_block_ohlcv_candidates": candidates}, profile_config={"block_rules": {"blocks": [
        {"name": "structural", "logic": "AND", "conditions": conditions or [rule()]}]}})[0]


def candidate(**extra):
    return {"indicator": "psar_trend", "actual": "bullish", "timeframe": "1h",
            "source_provider": "gate.io", "provider_policy_id": "spot_gate_closed_ohlcv_v1",
            "age_seconds": 100, "source_age_seconds": 200, "candle_closed": True, **extra}


def test_live_legacy_h1_fixture_explains_identity_mismatch_and_missing_ema():
    old = [candidate(source_provider="gate_candles", provider_policy_id=None),
           candidate(indicator="adx", actual=21.76, source_provider="gate_candles", provider_policy_id=None)]
    result = trace(old, [rule(), rule("adx", period=14, operator=">=", value=25),
                         rule("ema200", period=200, operator=">", value=0)])
    assert result["outcome"] == "SKIPPED"
    inputs = [condition["inputs"][0] for condition in result["conditions"]]
    assert [x["reason"] for x in inputs] == ["indicator_identity_mismatch", "indicator_identity_mismatch", "indicator_not_available"]
    assert inputs[0]["observed_value"] == "bullish"
    assert inputs[0]["value"] is None


@pytest.mark.parametrize("extra,reason", [({"age_seconds": 4321}, "indicator_stale"),
    ({"source_age_seconds": 4321}, "indicator_stale"),
    ({"candle_closed": False}, "indicator_candle_not_closed")])
def test_rejected_input_has_specific_reason_and_never_uses_m5(extra, reason):
    result = trace([candidate(**extra), candidate(timeframe="5m", actual="bearish")])
    assert result["outcome"] == "SKIPPED"
    assert result["conditions"][0]["inputs"][0]["reason"] == reason


def test_governed_h1_is_fresh_until_next_candle_but_expires_if_collection_stops():
    computed = NOW - timedelta(minutes=45)
    source = NOW.replace(hour=13, minute=0)
    payload = _governed_envelopes({"psar_trend": "bullish"}, timeframe="1h",
        source_timestamp=source, source_provider="gate.io", config=DEFAULT_INDICATORS,
        config_identity={}, computed_at=computed, capture_contract_version="spot_mtf_closed_ohlcv_v2",
        source_ingested_at=computed)
    merged = merge_indicator_rows([("structural", computed, "1h", payload)], now=NOW)
    assert merged.as_flat_dict()["psar_trend"] == "bullish"
    assert not merged.candidates[0]["stale"]
    assert trace(merged.candidates)["outcome"] == "OK"
    expired = merge_indicator_rows([("structural", computed, "1h", payload)], now=NOW+timedelta(hours=2))
    assert expired.candidates[0]["stale"]
    assert trace(expired.candidates)["outcome"] == "SKIPPED"


def test_configured_warmup_produces_ema200_and_psar_identity_from_closed_h1():
    import pandas as pd
    count = required_warmup_candles(DEFAULT_INDICATORS, "1h")
    frame = pd.DataFrame([{ "time": NOW.replace(minute=0) - timedelta(hours=count-i),
        "open": 10+i/100, "high": 10.1+i/100, "low": 9.9+i/100,
        "close": 10.02+i/100, "volume": 1000, "quote_volume": 10000 } for i in range(count)])
    result = FeatureEngine(DEFAULT_INDICATORS).calculate(frame, timeframe="1h", as_of=NOW)
    assert result["ema200"] > 0
    payload = _governed_envelopes(result, timeframe="1h", source_timestamp=frame.iloc[-1]["time"],
        source_provider="gate.io", config=DEFAULT_INDICATORS, config_identity={}, computed_at=NOW,
        capture_contract_version="spot_mtf_closed_ohlcv_v2", source_ingested_at=NOW)
    assert payload["ema200"]["period"] == 200
    assert payload["psar_trend"]["parameters"] == DEFAULT_INDICATORS["parabolic_sar"]
    assert payload["ema200"]["candle_closed"] is True


def test_observation_schedule_includes_canonical_h1_without_execution():
    from app.tasks.celery_app import celery_app
    schedule = observation_schedule(celery_app.conf.beat_schedule, {})
    assert schedule["collect_mtf_1h_after_close"]["task"] == "app.tasks.collect_mtf_ohlcv.collect_1h"
    assert "collect_mtf_15m_after_close" in schedule
    assert not {"execute_buy_cycle", "evaluate_signals", "retrain"} & schedule.keys()


@pytest.mark.asyncio
async def test_legacy_scheduler_yields_before_fetch_when_canonical_owner_is_active(monkeypatch):
    from app.services import structural_scheduler_service as scheduler
    from app import database
    db_run = AsyncMock(return_value=True)
    monkeypatch.setattr(database, "run_db_task", db_run)
    refresh = AsyncMock()
    monkeypatch.setattr(scheduler, "_refresh_one_symbol", refresh)
    await scheduler._run_one_cycle(1)
    assert db_run.await_count == 1
    refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_watchlist_table_uses_h1_candles_and_retains_live_price(monkeypatch):
    from app.api.watchlists import _fetch_indicators_map
    from app.utils import indicator_merge
    from app.services import indicators_provider
    monkeypatch.setattr(indicator_merge, "fetch_merged_indicators", AsyncMock(return_value={
        "LEO_USDT": SimpleNamespace(as_flat_dict=lambda: {"adx": 21.9, "ema200": 8.98, "price": 9.01})}))
    monkeypatch.setattr(indicators_provider, "get_timeframe_indicators", AsyncMock(return_value={
        "LEO_USDT": SimpleNamespace(as_flat_dict=lambda: {"adx": 21.76, "price": 8.99})}))
    values = await _fetch_indicators_map(object(), ["LEO_USDT"], profile_config={"default_timeframe": "1h"})
    assert values["LEO_USDT"]["adx"] == 21.76
    assert values["LEO_USDT"]["ema200"] is None
    assert values["LEO_USDT"]["price"] == 9.01


def test_next_closed_candle_cannot_be_suppressed_by_previous_dedup_key(monkeypatch):
    from app.tasks import collect_mtf_ohlcv, task_dispatch
    monkeypatch.setattr(collect_mtf_ohlcv, "collect_timeframe", lambda tf: tf)
    keys = []
    def enqueue(*args, **kwargs):
        keys.append(kwargs["dedup_key"])
        return "task"
    monkeypatch.setattr(task_dispatch, "enqueue", enqueue)
    for cutoff in (100, 101):
        monkeypatch.setattr(collect_mtf_ohlcv, "_run", lambda tf: {"successful_symbols": 1, "candle_cutoff": cutoff})
        assert collect_mtf_ohlcv._collect_and_chain("1h")["compute_enqueued"]
    assert keys == ["compute-mtf-1h-100", "compute-mtf-1h-101"]
