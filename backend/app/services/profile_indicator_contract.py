"""Structural indicator contract for Strategy Profiles JSON imports."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List


ALL_SECTIONS = frozenset({"filters", "signals", "block_rules", "entry_triggers"})
BREAKOUT_REFERENCE_WINDOWS = frozenset({"5m", "15m", "30m", "1h"})
VALID_TIMEFRAMES = frozenset({"1m", "3m", "5m", "15m", "1h"})
NUMERIC_OPERATORS = frozenset({">", ">=", "<", "<=", "=", "==", "!=", "between"})
BOOLEAN_OPERATORS = frozenset({"is_true", "is_false", "=", "==", "!="})
STRING_OPERATORS = frozenset({"=", "==", "!="})


def _contract(
    kind: str = "number",
    sections: Iterable[str] = ALL_SECTIONS,
    *,
    requires_reference_window: bool = False,
    fixed_period: int | None = None,
) -> Dict[str, Any]:
    return {
        "kind": kind,
        "sections": frozenset(sections),
        "requires_reference_window": requires_reference_window,
        "fixed_period": fixed_period,
    }


_NUMBER_IDS = {
    "volume_24h", "market_cap", "price", "change_24h", "spread_pct",
    "orderbook_depth_usdt", "taker_ratio", "volume_spike", "volume_delta",
    "orderbook_pressure", "bid_ask_imbalance", "funding_rate", "obv",
    "ema5_distance_pct", "ema9_distance_pct", "ema21_distance_pct",
    "ema50_distance_pct", "ema200_distance_pct", "vwap_distance_pct",
    "bb_upper_distance_pct", "bb_middle_distance_pct", "bb_lower_distance_pct",
    "recent_high_5m_distance_pct", "recent_high_15m_distance_pct",
    "recent_high_30m_distance_pct", "recent_high_1h_distance_pct",
    "recent_low_15m_distance_pct", "price_change_1m_pct", "price_change_5m_pct",
    "price_change_15m_pct", "rsi", "macd", "macd_histogram", "stoch_k",
    "stoch_d", "zscore", "adx", "di_plus", "di_minus", "atr", "atr_pct", "atr_percent",
    "bb_width", "ema5", "ema9", "ema21", "ema50", "ema200", "alpha_score",
    "score", "liquidity_score", "momentum_score",
}

PROFILE_INDICATOR_CONTRACT: Dict[str, Dict[str, Any]] = {
    indicator_id: _contract() for indicator_id in _NUMBER_IDS
}
PROFILE_INDICATOR_CONTRACT.update({
    "macd_signal": _contract("string"),
    "psar_trend": _contract("string"),
    "di_trend": _contract("boolean"),
    "ema_full_alignment": _contract("boolean"),
    "ema9_gt_ema21": _contract("boolean"),
    "ema9_gt_ema50": _contract("boolean"),
    "ema50_gt_ema200": _contract("boolean"),
    "adx_acceleration": _contract(sections={"block_rules"}),
    "adx_slope_3": _contract(sections={"block_rules"}),
    "macd_hist_slope_3": _contract(sections={"block_rules", "entry_triggers"}),
    "macd_hist_slope_5": _contract(sections={"entry_triggers"}),
    "rsi_slope_3": _contract(sections={"block_rules"}),
    "entry_exhaustion_score": _contract(sections={"block_rules"}),
    "rsi_6": _contract(sections={"block_rules"}, fixed_period=6),
    "breakout_distance_pct": _contract(requires_reference_window=True),
})


def _issue(code: str, path: str, message: str) -> Dict[str, str]:
    return {"code": code, "path": path, "message": message}


def _validate_indicator(
    indicator_value: Any,
    section: str,
    path: str,
    condition: Dict[str, Any],
) -> List[Dict[str, str]]:
    indicator_id = indicator_value.strip() if isinstance(indicator_value, str) else ""
    if not indicator_id:
        return [_issue("INDICATOR_REQUIRED", path, "indicator is required")]
    contract = PROFILE_INDICATOR_CONTRACT.get(indicator_id)
    if contract is None:
        return [_issue("UNKNOWN_INDICATOR", path, f"unsupported indicator: {indicator_id}")]

    errors: List[Dict[str, str]] = []
    if section not in contract["sections"]:
        errors.append(_issue(
            "INDICATOR_SECTION_NOT_ALLOWED", path,
            f"{indicator_id} is not allowed in {section}",
        ))
    base_path = path.rsplit(".", 1)[0]
    if contract["requires_reference_window"] and condition.get("reference_window") not in BREAKOUT_REFERENCE_WINDOWS:
        errors.append(_issue(
            "REFERENCE_WINDOW_REQUIRED", f"{base_path}.reference_window",
            "breakout_distance_pct requires reference_window: 5m, 15m, 30m or 1h",
        ))
    fixed_period = contract.get("fixed_period")
    if fixed_period is not None and condition.get("period") is not None and condition.get("period") != fixed_period:
        errors.append(_issue(
            "INDICATOR_PERIOD_INVALID", f"{base_path}.period",
            f"{indicator_id} requires period {fixed_period}",
        ))
    return errors


def _operator_set(kind: str):
    if kind == "boolean":
        return BOOLEAN_OPERATORS
    if kind == "string":
        return STRING_OPERATORS
    return NUMERIC_OPERATORS


def _validate_condition(value: Any, section: str, path: str) -> List[Dict[str, str]]:
    if not isinstance(value, dict):
        return [_issue("CONDITION_OBJECT_REQUIRED", path, "condition must be an object")]
    errors: List[Dict[str, str]] = []
    is_comparison = value.get("type") == "comparison" or bool(value.get("left") or value.get("right"))
    operator = value.get("operator") if isinstance(value.get("operator"), str) else ""

    if is_comparison:
        errors.extend(_validate_indicator(value.get("left"), section, f"{path}.left", value))
        if operator != "between":
            errors.extend(_validate_indicator(value.get("right"), section, f"{path}.right", value))
        if operator not in NUMERIC_OPERATORS:
            errors.append(_issue("OPERATOR_INVALID", f"{path}.operator", f"invalid comparison operator: {operator or '<empty>'}"))
    else:
        indicator_key = "field" if "field" in value else "indicator"
        indicator_value = value.get(indicator_key)
        errors.extend(_validate_indicator(indicator_value, section, f"{path}.{indicator_key}", value))
        indicator_id = indicator_value.strip() if isinstance(indicator_value, str) else ""
        kind = PROFILE_INDICATOR_CONTRACT.get(indicator_id, {}).get("kind", "number")
        if operator not in _operator_set(kind):
            errors.append(_issue("OPERATOR_INVALID", f"{path}.operator", f"invalid operator for {kind}: {operator or '<empty>'}"))
        if operator == "between":
            minimum, maximum = value.get("min"), value.get("max")
            if not isinstance(minimum, (int, float)) or isinstance(minimum, bool) or not isinstance(maximum, (int, float)) or isinstance(maximum, bool):
                errors.append(_issue("RANGE_REQUIRED", path, "between requires numeric min and max"))
            elif minimum > maximum:
                errors.append(_issue("RANGE_INVALID", path, "min must be less than or equal to max"))
        elif kind == "number" and (not isinstance(value.get("value"), (int, float)) or isinstance(value.get("value"), bool)):
            errors.append(_issue("VALUE_REQUIRED", f"{path}.value", "numeric value is required"))
        elif kind == "string" and not isinstance(value.get("value"), str):
            errors.append(_issue("VALUE_REQUIRED", f"{path}.value", "string value is required"))

    period = value.get("period")
    if period is not None and (not isinstance(period, int) or isinstance(period, bool) or period <= 0):
        errors.append(_issue("PERIOD_INVALID", f"{path}.period", "period must be a positive integer"))
    timeframe = value.get("timeframe")
    if timeframe is not None and timeframe not in VALID_TIMEFRAMES:
        errors.append(_issue("TIMEFRAME_INVALID", f"{path}.timeframe", f"invalid timeframe: {timeframe}"))
    for flag in ("required", "enabled"):
        if flag in value and not isinstance(value.get(flag), bool):
            errors.append(_issue("BOOLEAN_FIELD_INVALID", f"{path}.{flag}", f"{flag} must be boolean"))
    return errors


def validate_profile_execution_structure(
    profile: Dict[str, Any],
    *,
    path: str = "profile",
    require_sections: bool = False,
) -> List[Dict[str, str]]:
    """Return every structural error with an exact JSON path."""
    errors: List[Dict[str, str]] = []
    for section in ("filters", "signals", "entry_triggers"):
        value = profile.get(section)
        section_path = f"{path}.{section}"
        if value is None and not require_sections:
            continue
        if not isinstance(value, dict):
            errors.append(_issue("SECTION_OBJECT_REQUIRED", section_path, "section must be an object"))
            continue
        conditions = value.get("conditions")
        if not isinstance(conditions, list):
            errors.append(_issue("CONDITIONS_ARRAY_REQUIRED", f"{section_path}.conditions", "conditions must be an array"))
            continue
        for condition_index, condition in enumerate(conditions):
            errors.extend(_validate_condition(condition, section, f"{section_path}.conditions[{condition_index}]"))

    block_rules = profile.get("block_rules")
    block_path = f"{path}.block_rules"
    if block_rules is not None or require_sections:
        if not isinstance(block_rules, dict):
            errors.append(_issue("SECTION_OBJECT_REQUIRED", block_path, "section must be an object"))
        elif not isinstance(block_rules.get("blocks"), list):
            errors.append(_issue("BLOCKS_ARRAY_REQUIRED", f"{block_path}.blocks", "blocks must be an array"))
        else:
            for block_index, block in enumerate(block_rules["blocks"]):
                current_path = f"{block_path}.blocks[{block_index}]"
                if not isinstance(block, dict):
                    errors.append(_issue("BLOCK_OBJECT_REQUIRED", current_path, "block must be an object"))
                    continue
                conditions = block.get("conditions")
                if isinstance(conditions, list):
                    for condition_index, condition in enumerate(conditions):
                        errors.extend(_validate_condition(
                            condition, "block_rules",
                            f"{current_path}.conditions[{condition_index}]",
                        ))
                elif any(key in block for key in ("field", "indicator", "left", "right")):
                    errors.extend(_validate_condition(block, "block_rules", current_path))
                else:
                    errors.append(_issue("CONDITIONS_ARRAY_REQUIRED", f"{current_path}.conditions", "conditions must be an array"))
    return errors
