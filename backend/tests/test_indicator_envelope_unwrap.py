"""Regression tests for the indicator-envelope unwrap fix.

Indicators are persisted in ``indicators_json`` as envelopes:

    {"value": 51.61, "source": "binance", "confidence": 0.95, "status": "VALID"}

Any consumer that read the raw envelope dict instead of unwrapping
``.value`` was treating a perfectly valid 51.61 as an unevaluable
object — surfacing in the UI as ``SEM DADOS / aguardando coleta``.

These tests pin the universal unwrap helper plus the engines that the
trace UI / score breakdown / quarantine guard depend on, so a regression
that re-introduces raw envelope reads fails fast.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from app.services.indicator_validity import (
    RuleStatus,
    SkipReason,
    is_valid,
    unwrap_envelope_value,
)
from app.services.rule_engine import RuleEngine
from app.services.block_engine import BlockEngine
from app.services.score_engine import ScoreEngine


# ── Helper ────────────────────────────────────────────────────────────────────


def test_unwrap_envelope_value_returns_scalar_for_envelope():
    env = {"value": 51.61, "source": "binance", "confidence": 0.95, "status": "VALID"}
    assert unwrap_envelope_value(env) == 51.61


def test_unwrap_envelope_value_passthrough_for_scalars():
    assert unwrap_envelope_value(51.61) == 51.61
    assert unwrap_envelope_value(0) == 0
    assert unwrap_envelope_value(False) is False
    assert unwrap_envelope_value("ok") == "ok"
    assert unwrap_envelope_value(None) is None


def test_unwrap_envelope_value_handles_envelope_with_none_payload():
    env = {"value": None, "status": "INVALID"}
    assert unwrap_envelope_value(env) is None


def test_unwrap_envelope_value_returns_dict_when_no_value_key():
    # Defensive: an unexpected dict shape is preserved so downstream
    # validity checks can still reject it as non-numeric.
    other = {"foo": 1, "bar": 2}
    assert unwrap_envelope_value(other) is other


# ── is_valid (universal safety net) ───────────────────────────────────────────


def test_is_valid_unwraps_envelope_for_rsi():
    env = {"value": 51.61, "status": "VALID"}
    assert is_valid(env, "rsi") == (True, None)


def test_is_valid_unwraps_envelope_for_macd_histogram():
    env = {"value": -0.0023, "status": "VALID"}
    assert is_valid(env, "macd_histogram") == (True, None)


def test_is_valid_envelope_with_none_value_is_not_available():
    env = {"value": None, "status": "INVALID"}
    valid, reason = is_valid(env, "rsi")
    assert valid is False
    assert reason == SkipReason.INDICATOR_NOT_AVAILABLE


def test_is_valid_envelope_with_implausible_value_is_invalid():
    env = {"value": 150.0, "status": "VALID"}  # RSI must be in [0, 100]
    valid, reason = is_valid(env, "rsi")
    assert valid is False
    assert reason == SkipReason.INDICATOR_INVALID_VALUE


# ── RuleEngine._get_nested_value ──────────────────────────────────────────────


def test_rule_engine_unwraps_envelope_at_leaf():
    eng = RuleEngine()
    data = {"rsi": {"value": 51.61, "status": "VALID"}}
    assert eng._get_nested_value(data, "rsi") == 51.61


def test_rule_engine_unwraps_nested_envelope():
    eng = RuleEngine()
    data = {"indicators": {"rsi": {"value": 51.61, "status": "VALID"}}}
    assert eng._get_nested_value(data, "indicators.rsi") == 51.61


def test_rule_engine_evaluate_condition_passes_with_envelope_payload():
    eng = RuleEngine()
    cond = {"indicator": "rsi", "operator": ">", "value": 30}
    asset = {"rsi": {"value": 51.61, "status": "VALID"}}
    status, detail = eng.evaluate_condition_status(cond, asset, field_key="indicator")
    assert status == RuleStatus.PASS
    assert detail["passed"] is True


def test_rule_engine_evaluate_condition_fails_with_envelope_payload():
    eng = RuleEngine()
    cond = {"indicator": "rsi", "operator": ">", "value": 70}
    asset = {"rsi": {"value": 51.61, "status": "VALID"}}
    status, detail = eng.evaluate_condition_status(cond, asset, field_key="indicator")
    assert status == RuleStatus.FAIL
    assert detail["passed"] is False


# ── BlockEngine ───────────────────────────────────────────────────────────────


def _block_cfg(blocks):
    return {"blocks": blocks}


def test_block_engine_threshold_with_envelope_does_not_skip():
    """RSI = 51.61 stored as envelope must be evaluated, never SKIPPED.

    Threshold blocks express a *minimum requirement* — they trigger when
    the condition is NOT met. RSI=51.61 satisfies "RSI > 30", so the
    block does not fire AND is not silently skipped (the bug we fix).
    """
    eng = BlockEngine(_block_cfg([
        {"id": "rsi_min", "name": "RSI > 30", "type": "threshold",
         "indicator": "rsi", "operator": ">", "value": 30},
    ]))
    indicators = {"rsi": {"value": 51.61, "status": "VALID"}}
    result = eng.evaluate(indicators)
    assert result["blocked"] is False
    assert result["skipped_blocks"] == []
    assert result["triggered_blocks"] == []


def test_block_engine_threshold_with_envelope_triggers_when_violated():
    """Same minimum-requirement block; RSI=25 fails it → block triggers."""
    eng = BlockEngine(_block_cfg([
        {"id": "rsi_min", "name": "RSI > 30", "type": "threshold",
         "indicator": "rsi", "operator": ">", "value": 30},
    ]))
    indicators = {"rsi": {"value": 25.0, "status": "VALID"}}
    result = eng.evaluate(indicators)
    assert result["blocked"] is True
    assert "RSI > 30" in result["triggered_blocks"]
    assert result["skipped_blocks"] == []


def test_block_engine_string_condition_unwraps_ema_envelope():
    eng = BlockEngine(_block_cfg([
        {"id": "ema_cross_down", "name": "EMA9<EMA50",
         "type": "condition", "condition": "ema9<ema50"},
    ]))
    # Envelope says cross is DOWN (False) → block fires.
    result = eng.evaluate({"ema9_gt_ema50": {"value": False, "status": "VALID"}})
    assert result["blocked"] is True
    # Envelope says cross is UP (True) → block does NOT fire.
    result = eng.evaluate({"ema9_gt_ema50": {"value": True, "status": "VALID"}})
    assert result["blocked"] is False


def test_block_engine_grouped_block_with_envelope():
    eng = BlockEngine(_block_cfg([
        {"id": "macro_block", "name": "RSI overbought + ADX strong",
         "type": "group", "logic": "AND",
         "conditions": [
             {"indicator": "rsi", "operator": ">", "value": 70},
             {"indicator": "adx", "operator": ">", "value": 25},
         ]},
    ]))
    indicators = {
        "rsi": {"value": 75.0, "status": "VALID"},
        "adx": {"value": 30.0, "status": "VALID"},
    }
    result = eng.evaluate(indicators)
    assert result["blocked"] is True


# ── ScoreEngine ───────────────────────────────────────────────────────────────


def _score_cfg():
    return {
        "scoring_rules": [
            {"id": "rsi_neutral", "indicator": "rsi", "operator": ">",
             "value": 50, "points": 10},
        ],
        "thresholds": {"strong_buy": 80, "buy": 65, "neutral": 40},
    }


def test_score_engine_breakdown_with_envelope_indicators():
    eng = ScoreEngine(_score_cfg())
    indicators = {
        "rsi": {"value": 51.61, "status": "VALID"},
        "close": {"value": 1234.5, "status": "VALID"},
    }
    breakdown = eng.get_full_breakdown(indicators)
    assert len(breakdown) == 1
    row = breakdown[0]
    # actual_value must be the unwrapped scalar, not the envelope dict.
    assert row["actual_value"] == pytest.approx(51.61)
    # 51.61 > 50 → rule passes.
    assert row["passed"] is True


def test_score_engine_breakdown_ema_string_operator_with_envelope():
    eng = ScoreEngine({
        "scoring_rules": [
            {"id": "ema_up", "indicator": "ema9_gt_ema50",
             "operator": "ema9>ema50", "points": 5},
        ],
    })
    indicators = {"ema9_gt_ema50": {"value": True, "status": "VALID"}}
    breakdown = eng.get_full_breakdown(indicators)
    assert breakdown[0]["passed"] is True
    assert breakdown[0]["actual_value"] is True


def test_score_engine_evaluate_rule_di_comparison_with_envelope():
    eng = ScoreEngine({
        "scoring_rules": [
            {"id": "di_bull", "indicator": "di_plus",
             "operator": "di+>di-", "points": 5},
        ],
    })
    indicators = {
        "di_plus": {"value": 28.0, "status": "VALID"},
        "di_minus": {"value": 12.0, "status": "VALID"},
    }
    rule = {"id": "di_bull", "indicator": "di_plus", "operator": "di+>di-"}
    assert eng._evaluate_rule(rule, indicators) is True
