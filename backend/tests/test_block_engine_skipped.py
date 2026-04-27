"""Tests for the SKIPPED-on-missing-data behaviour in BlockEngine.

Covers the four scenarios from the spec for every indicator listed there:
  1. Valid value, rule satisfied → PASS (no block).
  2. Valid value, rule violated  → FAIL (block triggers).
  3. Indicator absent            → SKIPPED, never blocks.
  4. Indicator implausible       → SKIPPED, never blocks.
"""

import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from app.services.block_engine import BlockEngine
from app.services.indicator_validity import RuleStatus, is_valid


# ── Validity helper ──────────────────────────────────────────────────────────


def test_is_valid_accepts_real_numbers():
    assert is_valid(1.5, "rsi") == (True, None)
    assert is_valid(0.0, "macd_histogram") == (True, None)


def test_is_valid_rejects_none_and_nan():
    valid, reason = is_valid(None, "rsi")
    assert valid is False
    assert reason.value == "indicator_not_available"

    valid, reason = is_valid(float("nan"), "adx")
    assert valid is False
    assert reason.value == "indicator_not_available"


def test_is_valid_rejects_implausible_values():
    # Since #82 the canonical scale is buy/(buy+sell) ∈ [0, 1]; 0 itself
    # is now a *valid* signal ("100% sell pressure"), so the implausible
    # case is anything > 1 (or < 0).
    valid, reason = is_valid(1.5, "taker_ratio")
    assert valid is False
    assert reason.value == "indicator_invalid_value"

    valid, reason = is_valid(-1, "volume_spike")
    assert valid is False
    assert reason.value == "indicator_invalid_value"

    valid, reason = is_valid(150, "rsi")
    assert valid is False
    assert reason.value == "indicator_invalid_value"


# ── Per-indicator scenarios for BlockEngine ──────────────────────────────────


# (indicator name, block configuration, "good" value, "bad" value, "implausible" value)
INDICATOR_CASES = [
    # taker_ratio: block when buy/(buy+sell) below 0.55  (#82 scale)
    ("taker_ratio", {
        "id": "tr",
        "name": "Weak Taker Ratio",
        "indicator": "taker_ratio",
        "type": "threshold",
        "operator": ">=",
        "value": 0.55,
    }, 0.70, 0.40, 1.5),
    # volume_spike: block when below 1.5
    ("volume_spike", {
        "id": "vs",
        "name": "No Volume Spike",
        "indicator": "volume_spike",
        "type": "threshold",
        "operator": ">=",
        "value": 1.5,
    }, 2.0, 1.0, 0),
    # adx: block when trend strength below 20
    ("adx", {
        "id": "adx",
        "name": "Weak Trend",
        "indicator": "adx",
        "type": "threshold",
        "operator": ">=",
        "value": 20,
    }, 28, 12, 0),
    # bb_width: block when too tight
    ("bb_width", {
        "id": "bbw",
        "name": "Tight Bands",
        "indicator": "bb_width",
        "type": "threshold",
        "operator": ">=",
        "value": 0.02,
    }, 0.05, 0.005, 0),
    # spread: block when too wide
    ("spread", {
        "id": "sp",
        "name": "Wide Spread",
        "indicator": "spread",
        "type": "threshold",
        "operator": "<=",
        "value": 0.5,
    }, 0.1, 1.0, 0),
    # rsi: block on overbought
    ("rsi", {
        "id": "rsi",
        "name": "Overbought RSI",
        "indicator": "rsi",
        "type": "threshold",
        "operator": "<",
        "value": 70,
    }, 50, 85, 150),
    # macd_histogram: block when negative momentum
    ("macd_histogram", {
        "id": "mh",
        "name": "Negative MACD Hist",
        "indicator": "macd_histogram",
        "type": "threshold",
        "operator": ">",
        "value": 0,
    }, 0.5, -0.5, float("nan")),
]


@pytest.mark.parametrize("indicator,block,good,bad,implausible", INDICATOR_CASES)
def test_block_engine_handles_four_validity_scenarios(indicator, block, good, bad, implausible):
    engine = BlockEngine({"blocks": [block]})
    name = block["name"]

    # 1. Valid + satisfies rule → no block, no skip.
    result = engine.evaluate({indicator: good})
    assert result["blocked"] is False, f"good {indicator}={good} should not block"
    assert name not in result["triggered_blocks"]
    assert name not in result["skipped_blocks"]

    # 2. Valid + violates rule → block triggers.
    result = engine.evaluate({indicator: bad})
    assert result["blocked"] is True, f"bad {indicator}={bad} should block"
    assert name in result["triggered_blocks"]

    # 3. Missing indicator → SKIPPED, never blocks.
    result = engine.evaluate({"some_other_indicator": 1.0})
    assert result["blocked"] is False, f"missing {indicator} must not block"
    assert name in result["skipped_blocks"]
    assert result["skipped_details"][block["id"]] == "indicator_not_available"

    # 4. Invalid value (0 / NaN / out-of-range) → SKIPPED, never blocks.
    result = engine.evaluate({indicator: implausible})
    assert result["blocked"] is False, (
        f"implausible {indicator}={implausible!r} must not block"
    )
    assert name in result["skipped_blocks"]
    assert result["skipped_details"][block["id"]] in {
        "indicator_invalid_value",
        "indicator_not_available",
    }


# ── Block group (AND/OR) tristate semantics ──────────────────────────────────


def test_block_group_and_with_missing_data_is_skipped():
    engine = BlockEngine(
        {
            "blocks": [
                {
                    "id": "grp",
                    "name": "Combo",
                    "logic": "AND",
                    "conditions": [
                        {"indicator": "rsi", "operator": ">", "value": 70},
                        {"indicator": "adx", "operator": ">", "value": 20},
                    ],
                }
            ]
        }
    )

    # adx missing → AND group cannot decide → SKIPPED, no block.
    result = engine.evaluate({"rsi": 80})
    assert result["blocked"] is False
    assert "Combo" in result["skipped_blocks"]
    assert "Combo" not in result["triggered_blocks"]


def test_block_group_or_with_partial_missing_data_decides_on_remaining():
    engine = BlockEngine(
        {
            "blocks": [
                {
                    "id": "grp",
                    "name": "Either",
                    "logic": "OR",
                    "conditions": [
                        {"indicator": "rsi", "operator": ">", "value": 70},
                        {"indicator": "adx", "operator": ">", "value": 20},
                    ],
                }
            ]
        }
    )

    # rsi triggers, adx missing → OR ignores SKIPPED → block triggers.
    result = engine.evaluate({"rsi": 85})
    assert result["blocked"] is True
    assert "Either" in result["triggered_blocks"]

    # both missing → SKIPPED, never blocks.
    result = engine.evaluate({"unrelated": 1})
    assert result["blocked"] is False
    assert "Either" in result["skipped_blocks"]


def test_empty_indicator_payload_does_not_block():
    """Missing indicator data must NEVER block trades — every block is SKIPPED."""
    engine = BlockEngine(
        {
            "blocks": [
                {
                    "id": "tr",
                    "name": "Weak Taker Ratio",
                    "indicator": "taker_ratio",
                    "type": "threshold",
                    "operator": ">=",
                    "value": 1.05,
                },
                {
                    "id": "rsi",
                    "name": "Overbought RSI",
                    "indicator": "rsi",
                    "type": "threshold",
                    "operator": "<",
                    "value": 70,
                },
            ]
        }
    )
    for payload in ({}, None):
        result = engine.evaluate(payload)
        assert result["blocked"] is False, payload
        # No spurious "no_data" pseudo-block in the triggered list.
        assert result["triggered_blocks"] == []
        assert set(result["skipped_blocks"]) == {"Weak Taker Ratio", "Overbought RSI"}
        assert result["skipped_details"]["tr"] == "indicator_not_available"
        assert result["skipped_details"]["rsi"] == "indicator_not_available"


def test_block_group_skip_reason_preserves_invalid_value():
    """Grouped block must preserve indicator_invalid_value, not collapse to not_available."""
    engine = BlockEngine(
        {
            "blocks": [
                {
                    "id": "grp",
                    "name": "Combo",
                    "logic": "AND",
                    "conditions": [
                        # rsi present and valid
                        {"indicator": "rsi", "operator": ">", "value": 70},
                        # taker_ratio present but implausible (out of [0, 1])
                        {"indicator": "taker_ratio", "operator": ">=", "value": 0.55},
                    ],
                }
            ]
        }
    )
    result = engine.evaluate({"rsi": 80, "taker_ratio": 1.5})
    assert result["blocked"] is False
    assert "Combo" in result["skipped_blocks"]
    assert result["skipped_details"]["grp"] == "indicator_invalid_value"


def test_block_group_skip_reason_falls_back_to_not_available():
    engine = BlockEngine(
        {
            "blocks": [
                {
                    "id": "grp",
                    "name": "Combo",
                    "logic": "AND",
                    "conditions": [
                        {"indicator": "rsi", "operator": ">", "value": 70},
                        {"indicator": "adx", "operator": ">", "value": 20},  # missing
                    ],
                }
            ]
        }
    )
    result = engine.evaluate({"rsi": 80})
    assert "Combo" in result["skipped_blocks"]
    assert result["skipped_details"]["grp"] == "indicator_not_available"


def test_pipeline_block_rule_emits_skipped_with_reason():
    """pipeline_rejections._evaluate_block_rule must surface SKIPPED + reason."""
    from app.services.pipeline_rejections import _evaluate_block_rule
    from app.services.rule_engine import RuleEngine

    rule_engine = RuleEngine()

    # Implausible indicator → SKIPPED with indicator_invalid_value.
    # Threshold rescaled for #82: buy/(buy+sell) ∈ [0, 1].
    block = {
        "id": "tr",
        "name": "Weak Taker Ratio",
        "logic": "AND",
        "conditions": [
            {"indicator": "taker_ratio", "operator": ">=", "value": 0.55},
        ],
    }
    payload = _evaluate_block_rule(rule_engine, {"taker_ratio": 1.5}, block)
    assert payload["status"] == "SKIPPED"
    assert payload["triggered"] is False
    assert payload["reason"] == "indicator_invalid_value"

    # Missing indicator → SKIPPED with indicator_not_available.
    payload = _evaluate_block_rule(rule_engine, {"rsi": 50}, block)
    assert payload["status"] == "SKIPPED"
    assert payload["triggered"] is False
    assert payload["reason"] == "indicator_not_available"

    # In `_evaluate_block_rule`, a block triggers when its conditions
    # evaluate True (the rule is the "danger" pattern). Here the
    # condition is `taker_ratio >= 0.55`, so:
    #   taker_ratio=0.8 → condition True → block triggers → status FAIL.
    payload = _evaluate_block_rule(rule_engine, {"taker_ratio": 0.8}, block)
    assert payload["status"] == "FAIL"
    assert payload["triggered"] is True
    assert "reason" not in payload

    #   taker_ratio=0.4 → condition False → block does not trigger → PASS.
    payload = _evaluate_block_rule(rule_engine, {"taker_ratio": 0.4}, block)
    assert payload["status"] == "PASS"
    assert payload["triggered"] is False
    assert "reason" not in payload


def test_pipeline_entry_trigger_emits_skipped_with_reason():
    from app.services.pipeline_rejections import _evaluate_entry_trigger
    from app.services.rule_engine import RuleEngine

    rule_engine = RuleEngine()
    cond = {"indicator": "taker_ratio", "operator": ">=", "value": 0.55}

    payload = _evaluate_entry_trigger(rule_engine, {"taker_ratio": 1.5}, cond)
    assert payload["status"] == "SKIPPED"
    assert payload["reason"] == "indicator_invalid_value"

    payload = _evaluate_entry_trigger(rule_engine, {}, cond)
    assert payload["status"] == "SKIPPED"
    assert payload["reason"] == "indicator_not_available"


def test_legacy_condition_block_still_triggers_when_data_exists():
    """Regression: type='condition' blocks must NOT be auto-skipped just
    because they don't carry a single named indicator. The DSL evaluator
    handles its own operands; the SKIPPED gate only applies to
    threshold/range/grouped blocks.
    """
    engine = BlockEngine(
        {
            "blocks": [
                {
                    "id": "trend_down",
                    "name": "Downtrend",
                    "type": "condition",
                    "condition": "ema9<ema50",
                }
            ]
        }
    )

    # Bearish (ema9 NOT > ema50) → block triggers.
    result = engine.evaluate({"ema9_gt_ema50": False})
    assert result["blocked"] is True, "Condition block must still fire"
    assert "Downtrend" in result["triggered_blocks"]
    assert "Downtrend" not in result["skipped_blocks"]

    # Bullish (ema9 > ema50) → block does NOT trigger and is NOT skipped.
    result = engine.evaluate({"ema9_gt_ema50": True})
    assert result["blocked"] is False
    assert "Downtrend" not in result["triggered_blocks"]
    assert "Downtrend" not in result["skipped_blocks"]


def test_normalized_trace_item_preserves_skipped_reason():
    """The serialized evaluation_trace must keep the SKIPPED reason."""
    from app.services.pipeline_rejections import (
        _evaluate_block_rule,
        _evaluate_entry_trigger,
        _evaluate_signal_condition,
        _normalized_trace_item,
    )
    from app.services.rule_engine import RuleEngine

    rule_engine = RuleEngine()
    cond = {"indicator": "taker_ratio", "operator": ">=", "value": 0.55}

    # Entry trigger SKIPPED → reason survives normalization.
    raw = _evaluate_entry_trigger(rule_engine, {"taker_ratio": 1.5}, cond)
    normalized = _normalized_trace_item(raw)
    assert normalized["status"] == "SKIPPED"
    assert normalized["reason"] == "indicator_invalid_value"

    # Block rule SKIPPED → reason survives normalization.
    block = {
        "id": "tr",
        "name": "Weak Taker Ratio",
        "logic": "AND",
        "conditions": [cond],
    }
    raw = _evaluate_block_rule(rule_engine, {}, block)
    normalized = _normalized_trace_item(raw)
    assert normalized["status"] == "SKIPPED"
    assert normalized["reason"] == "indicator_not_available"

    # Signal condition SKIPPED → reason survives normalization.
    raw = _evaluate_signal_condition(rule_engine, {"taker_ratio": 1.5}, cond)
    normalized = _normalized_trace_item(raw)
    assert normalized["status"] == "SKIPPED"
    assert normalized["reason"] == "indicator_invalid_value"

    # PASS items must NOT acquire a spurious reason field.
    raw = _evaluate_entry_trigger(rule_engine, {"taker_ratio": 0.7}, cond)
    normalized = _normalized_trace_item(raw)
    assert normalized["status"] == "PASS"
    assert "reason" not in normalized


# ── Entry triggers ───────────────────────────────────────────────────────────


def test_required_entry_trigger_with_missing_data_does_not_block_entry():
    engine = BlockEngine(
        {
            "blocks": [],
            "entry_triggers": [
                {
                    "id": "req_taker",
                    "indicator": "taker_ratio",
                    "operator": ">=",
                    "value": 0.55,
                    "required": True,
                    "enabled": True,
                }
            ],
            "entry_logic": "AND",
        }
    )

    # taker_ratio==1.5 is out of [0, 1] → implausible → SKIPPED →
    # entry still allowed (missing/garbage data must never block).
    result = engine.evaluate_entry({"taker_ratio": 1.5})
    assert result["allowed"] is True
    assert result["failed_required"] == []
    assert "req_taker" in result["skipped"]
    assert "req_taker" not in result["matched"]


def test_required_entry_trigger_with_valid_failure_blocks_entry():
    engine = BlockEngine(
        {
            "blocks": [],
            "entry_triggers": [
                {
                    "id": "req_taker",
                    "indicator": "taker_ratio",
                    "operator": ">=",
                    "value": 1.05,
                    "required": True,
                    "enabled": True,
                }
            ],
        }
    )
    # Real data, fails the rule → entry blocked.
    result = engine.evaluate_entry({"taker_ratio": 0.8})
    assert result["allowed"] is False
    assert "req_taker" in result["failed_required"]


def test_optional_entry_triggers_all_skipped_still_allow_entry():
    engine = BlockEngine(
        {
            "blocks": [],
            "entry_triggers": [
                {"id": "opt_rsi", "indicator": "rsi", "operator": "<", "value": 70, "enabled": True},
                {"id": "opt_adx", "indicator": "adx", "operator": ">", "value": 25, "enabled": True},
            ],
            "entry_logic": "AND",
        }
    )

    # No indicator data at all → both SKIPPED → still allowed.
    result = engine.evaluate_entry({"unrelated": 1})
    assert result["allowed"] is True
    assert set(result["skipped"]) == {"opt_rsi", "opt_adx"}


def test_optional_and_entry_triggers_pass_when_decidable_ones_pass():
    engine = BlockEngine(
        {
            "blocks": [],
            "entry_triggers": [
                {"id": "opt_rsi", "indicator": "rsi", "operator": "<", "value": 70, "enabled": True},
                {"id": "opt_adx", "indicator": "adx", "operator": ">", "value": 25, "enabled": True},
            ],
            "entry_logic": "AND",
        }
    )

    # rsi decidable + passes; adx missing → SKIPPED ignored; entry allowed.
    result = engine.evaluate_entry({"rsi": 50})
    assert result["allowed"] is True
    assert "opt_rsi" in result["matched"]
    assert "opt_adx" in result["skipped"]
