from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.shadow_barrier_evaluator import evaluate_closed_candles
from app.services.shadow_trade_service import _resolve_atr_barriers
from app.services.block_engine import BlockEngine


BASE = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)


def candle(minute: int, *, high: float, low: float, close: float = 100.0):
    return {
        "time": BASE + timedelta(minutes=minute),
        "open": 100.0,
        "high": high,
        "low": low,
        "close": close,
    }


def test_pending_tp_before_later_sampled_sl_is_tp_hit():
    result = evaluate_closed_candles(
        [
            candle(1, high=102.1, low=99.5, close=100.2),
            candle(2, high=100.4, low=98.4, close=98.6),
        ],
        entry_price=100.0,
        entry_timestamp=BASE,
        tp_price=102.0,
        sl_price=98.5,
    )
    assert result["outcome"] == "TP_HIT"
    assert result["barrier_touched_at"] == BASE + timedelta(minutes=1)
    assert result["exit_price_nominal"] == 102.0
    assert result["exit_price_observed"] == 102.1


def test_same_candle_uses_sl_first_and_marks_ambiguity():
    result = evaluate_closed_candles(
        [candle(1, high=102.2, low=98.2)],
        entry_price=100.0,
        entry_timestamp=BASE,
        tp_price=102.0,
        sl_price=98.5,
    )
    assert result["outcome"] == "SL_HIT"
    assert result["barrier_touched"] == "BOTH_SAME_CANDLE"
    assert result["reason_code"] == "BOTH_SAME_CANDLE"


def test_touch_in_partial_entry_candle_is_unresolved():
    result = evaluate_closed_candles(
        [candle(0, high=102.2, low=99.8)],
        entry_price=100.0,
        entry_timestamp=BASE + timedelta(seconds=20),
        tp_price=102.0,
        sl_price=98.5,
    )
    assert result["status"] == "UNRESOLVED"
    assert result["outcome"] is None
    assert result["reason_code"] == "BARRIER_PATH_UNRESOLVED"


def test_no_closed_candles_remains_pending_without_price_fallback():
    result = evaluate_closed_candles(
        [],
        entry_price=100.0,
        entry_timestamp=BASE,
        tp_price=102.0,
        sl_price=98.5,
    )
    assert result["status"] == "PENDING"
    assert result["reason_code"] == "NO_CLOSED_CANDLE_AVAILABLE"


@pytest.mark.parametrize("atr", [0.2, 1.0, 4.0])
def test_sl_anchored_ratio_preserves_configured_ratio(atr: float):
    config = {
        "sl_atr_multiplier": 1.4,
        "tp_atr_multiplier": 3.0,
        "sl_min_pct": 1.5,
        "sl_max_pct": 3.0,
        "shadow_barrier_geometry_policy": "SL_ANCHORED_RATIO",
    }
    tp, sl = _resolve_atr_barriers(atr, 1.0, 1.0, config)
    assert tp / sl == pytest.approx(3.0 / 1.4)


def test_legacy_geometry_reproduces_independent_clamp():
    config = {
        "sl_atr_multiplier": 1.4,
        "tp_atr_multiplier": 3.0,
        "sl_min_pct": 1.5,
        "sl_max_pct": 3.0,
        "shadow_barrier_geometry_policy": "LEGACY_INDEPENDENT_CLAMP",
    }
    tp, sl = _resolve_atr_barriers(0.4, 1.0, 1.0, config)
    assert tp == 1.5
    assert sl == 1.5


def test_atr_clamped_before_multiply_preserves_ratio():
    config = {
        "sl_atr_multiplier": 1.4,
        "tp_atr_multiplier": 3.0,
        "sl_min_pct": 1.5,
        "sl_max_pct": 3.0,
        "shadow_barrier_geometry_policy": "ATR_CLAMPED_BEFORE_MULTIPLY",
    }
    tp, sl = _resolve_atr_barriers(0.4, 1.0, 1.0, config)
    assert tp == pytest.approx(4.5)
    assert sl == pytest.approx(2.1)
    assert tp / sl == pytest.approx(3.0 / 1.4)


@pytest.mark.parametrize(
    ("rsi", "blocked"), [(19.0, True), (20.0, False), (80.0, False), (81.0, True)]
)
def test_flat_min_max_compiles_to_inclusive_range_only_when_enabled(
    rsi: float, blocked: bool
):
    config = {
        "blocks": [
            {
                "id": "b1",
                "name": "RSI out of range",
                "enabled": True,
                "indicator": "rsi",
                "min": 20,
                "max": 80,
            }
        ]
    }
    result = BlockEngine(
        config, legacy_range_compiler_enabled=True, zero_is_value=True
    ).evaluate({"rsi": rsi})
    assert result["blocked"] is blocked
    audit = result["rules"][0]
    assert audit["audit_contract_version"] == "block_rule_audit_v2"
    assert audit["normalization"] == "FLAT_MIN_MAX_TO_OUTSIDE_RANGE"
    assert audit["condition_matched"] is blocked
    assert audit["blocked"] is blocked


def test_flat_min_max_compiler_is_inactive_by_default():
    result = BlockEngine(
        {
            "blocks": [
                {
                    "id": "b1",
                    "indicator": "rsi",
                    "min": 20,
                    "max": 80,
                }
            ]
        },
        zero_is_value=True,
    ).evaluate({"rsi": 19.0})
    assert result["blocked"] is False
    assert result["legacy_range_compiler_enabled"] is False
