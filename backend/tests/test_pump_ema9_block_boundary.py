"""PUMP3's persisted EMA block must share the legacy and V3 boundary."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services.block_engine import BlockEngine
from app.services.l3_authorization_contract_v3 import (
    build_authorization_contract,
)


@pytest.mark.parametrize("actual, blocked", [(1.01, True), (1.0, False), (0.99, False)])
def test_pump3_ema9_strict_greater_than_boundary(actual, blocked):
    # Shape captured from PUMP3; no independent period belongs to this feature.
    block = {
        "id": "block_1790464436323", "name": "EMA9", "logic": "AND",
        "reason": "", "enabled": True,
        "conditions": [{
            "id": "cond_1790464436323", "type": "threshold", "value": 1,
            "source": "ohlcv", "operator": ">", "indicator": "ema9_distance_pct",
            "timeframe": "5m", "candle_policy": "CLOSED_ONLY",
            "max_age_seconds": 741, "source_provider": "gate.io",
            "provider_policy_id": "spot_gate_closed_ohlcv_v1",
        }],
    }
    now = datetime(2026, 9, 27, 3, tzinfo=timezone.utc)
    legacy = BlockEngine({"blocks": [block]}).evaluate({"ema9_distance_pct": actual})
    result = build_authorization_contract(
        asset={
            "symbol": "TEST_USDT",
            "_merged_indicators": SimpleNamespace(candidates=[{
                "indicator": "ema9_distance_pct", "actual": actual,
                "source": "ohlcv", "source_provider": "gate.io",
                "provider_policy_id": "spot_gate_closed_ohlcv_v1",
                "timeframe": "5m", "period": None,
                "candle_policy": "CLOSED_ONLY", "candle_closed": True,
                "source_timestamp": now, "computed_at": now, "available_at": now,
                "age_seconds": 0, "stale": False,
            }]),
        },
        profile_config={
            "default_timeframe": "5m",
            "filters": {"conditions": []}, "signals": {"conditions": []},
            "entry_triggers": {"conditions": []}, "block_rules": {"blocks": [block]},
        },
        legacy_decision="BLOCK" if legacy["blocked"] else "ALLOW",
        evaluated_at=now, profile_id="pump3-test", profile_name="PUMP3",
        profile_version=now,
    )
    assert legacy["blocked"] is blocked
    assert result["valid"] is True
    assert result["sections"]["block_rules"]["blocked"] is blocked
    condition = result["sections"]["block_rules"]["blocks"][0]["conditions"][0]
    assert condition["status"] == ("PASS" if blocked else "FAIL")
    assert "PERIOD_MISMATCH" not in result["reason_codes"]


@pytest.mark.parametrize("separate_ema_block", [False, True])
def test_ema_above_limit_only_blocks_independently_when_outside_book_and(
    separate_ema_block,
):
    # Reproduce the original grouping mistake with synthetic market values:
    # EMA > 1 cannot make a BOOK AND block true when its book condition is false.
    now = datetime(2026, 9, 27, 3, tzinfo=timezone.utc)
    book_conditions = [{
        "id": indicator, "type": "threshold", "indicator": indicator,
        "source": "live_order_book", "source_provider": "gate",
        "provider_policy_id": "observed_order_book_v1", "snapshot": True,
        "window_seconds": None, "max_age_seconds": 600,
        "operator": "<", "value": -0.75,
    } for indicator in ("orderbook_pressure", "bid_ask_imbalance")]
    ema_condition = {
        "id": "ema-distance", "type": "threshold", "indicator": "ema9_distance_pct",
        "source": "ohlcv", "source_provider": "gate.io",
        "provider_policy_id": "spot_gate_closed_ohlcv_v1", "timeframe": "5m",
        "candle_policy": "CLOSED_ONLY", "max_age_seconds": 741,
        "operator": ">", "value": 1,
    }
    book_block = {
        "id": "book", "name": "BOOK VENDEDOR EXTREMO", "logic": "AND",
        "enabled": True, "conditions": book_conditions,
    }
    if separate_ema_block:
        blocks = [book_block, {
            "id": "ema", "name": "EMA9", "logic": "AND", "enabled": True,
            "conditions": [ema_condition],
        }]
    else:
        book_block["conditions"] = [*book_conditions, ema_condition]
        blocks = [book_block]

    actuals = {
        "orderbook_pressure": -0.5, "bid_ask_imbalance": -0.5,
        "ema9_distance_pct": 1.01,
    }
    # The book names are aliases of one observation, not separate candidates.
    candidates = [{
        **{k: condition[k] for k in (
            "indicator", "source", "source_provider", "provider_policy_id",
        )},
        "actual": actuals[condition["indicator"]], "period": None,
        "source_timestamp": now, "computed_at": now, "available_at": now,
        "age_seconds": 0, "stale": False,
        **({"timeframe": "5m", "candle_policy": "CLOSED_ONLY", "candle_closed": True}
           if condition["source"] == "ohlcv" else {"snapshot": True}),
    } for condition in [book_conditions[0], ema_condition]]
    legacy = BlockEngine({"blocks": blocks}).evaluate(actuals)
    result = build_authorization_contract(
        asset={"symbol": "TEST_USDT", "_merged_indicators": SimpleNamespace(candidates=candidates)},
        profile_config={
            "default_timeframe": "5m",
            "filters": {"conditions": []}, "signals": {"conditions": []},
            "entry_triggers": {"conditions": []}, "block_rules": {"blocks": blocks},
        },
        legacy_decision="BLOCK" if legacy["blocked"] else "ALLOW",
        evaluated_at=now, profile_id="pump3-test", profile_name="PUMP3",
        profile_version=now,
    )
    assert legacy["blocked"] is separate_ema_block
    assert result["valid"] is True, result["reason_codes"]
    assert result["sections"]["block_rules"]["blocked"] is separate_ema_block
    evaluated_blocks = result["sections"]["block_rules"]["blocks"]
    evaluated_ema = (evaluated_blocks[1]["conditions"][0] if separate_ema_block
                     else evaluated_blocks[0]["conditions"][2])
    assert evaluated_ema["status"] == (
        "PASS" if separate_ema_block else "NOT_NEEDED_FOR_BOOLEAN_RESULT"
    )
    assert "PERIOD_MISMATCH" not in result["reason_codes"]
