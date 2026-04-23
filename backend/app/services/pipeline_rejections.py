from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .rule_engine import RuleEngine

FIELD_LABELS: Dict[str, str] = {
    "price": "Price",
    "change_24h": "24h%",
    "price_change_24h": "24h%",
    "volume_24h": "Volume 24h",
    "market_cap": "Market Cap",
    "spread_pct": "Spread %",
    "orderbook_depth_usdt": "Orderbook Depth",
    "rsi": "RSI",
    "adx": "ADX",
    "di_plus": "DI+",
    "di_minus": "DI-",
    "di_trend": "DI+ > DI-",
    "macd": "MACD",
    "macd_signal": "MACD Signal",
    "macd_histogram": "MACD Histogram",
    "stoch_k": "Stoch %K",
    "stoch_d": "Stoch %D",
    "zscore": "Z-Score",
    "bb_width": "BB Width",
    "atr": "ATR",
    "atr_pct": "ATR %",
    "atr_percent": "ATR %",
    "volume_spike": "Volume Spike",
    "taker_ratio": "Taker Ratio",
    "ema9": "EMA 9",
    "ema21": "EMA 21",
    "ema50": "EMA 50",
    "ema200": "EMA 200",
    "ema9_gt_ema50": "EMA 9 > EMA 50",
    "ema50_gt_ema200": "EMA 50 > EMA 200",
    "ema_full_alignment": "EMA Full Alignment",
    "score": "Alpha Score",
    "alpha_score": "Alpha Score",
    "liquidity_score": "Liquidity Score",
    "market_structure_score": "Market Structure Score",
    "momentum_score": "Momentum Score",
    "signal_score": "Signal Score",
}


def jsonable_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): jsonable_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [jsonable_value(v) for v in value]
    if isinstance(value, tuple):
        return [jsonable_value(v) for v in value]
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def field_label(name: Optional[str]) -> str:
    key = (name or "").strip()
    if not key:
        return "Unknown"
    return FIELD_LABELS.get(key, key.replace("_", " ").upper())


def format_expected(condition: Dict[str, Any]) -> Optional[str]:
    operator = condition.get("operator", "==")
    if condition.get("type") == "comparison":
        return field_label(condition.get("right"))
    if operator == "between":
        return f"{condition.get('min')} → {condition.get('max')}"
    if operator == "is_true":
        return "true"
    if operator == "is_false":
        return "false"
    value = condition.get("value")
    return None if value is None else str(value)


def format_condition_text(condition: Dict[str, Any], *, field_key: str = "field") -> str:
    operator = condition.get("operator", "==")
    if condition.get("type") == "comparison":
        left = field_label(condition.get("left"))
        right = field_label(condition.get("right"))
        return f"{left} {operator} {right}"
    field = field_label(condition.get(field_key) or condition.get("field"))
    if operator == "between":
        return f"{field} between {condition.get('min')} and {condition.get('max')}"
    if operator == "is_true":
        return f"{field} is true"
    if operator == "is_false":
        return f"{field} is false"
    return f"{field} {operator} {condition.get('value')}"


def _condition_indicator(condition: Dict[str, Any], *, field_key: str = "field") -> str:
    if condition.get("type") == "comparison":
        return f"{field_label(condition.get('left'))} vs {field_label(condition.get('right'))}"
    return field_label(condition.get(field_key) or condition.get("field"))


def _detail_actual(detail: Dict[str, Any]) -> Any:
    if detail.get("type") == "comparison":
        return {
            "left": jsonable_value(detail.get("actual")),
            "right": jsonable_value(detail.get("target")),
        }
    return jsonable_value(detail.get("actual"))


def _evaluate_filter(
    rule_engine: RuleEngine,
    asset: Dict[str, Any],
    condition: Dict[str, Any],
) -> Dict[str, Any]:
    passed, detail = rule_engine.evaluate_condition(condition, asset, field_key="field")
    return {
        "type": "filter",
        "indicator": _condition_indicator(condition, field_key="field"),
        "condition": format_condition_text(condition, field_key="field"),
        "expected": format_expected(condition),
        "current_value": _detail_actual(detail),
        "status": "PASS" if passed else "FAIL",
    }


def _evaluate_block_rule(
    rule_engine: RuleEngine,
    asset: Dict[str, Any],
    block: Dict[str, Any],
) -> Dict[str, Any]:
    conditions = block.get("conditions", []) or []
    logic = str(block.get("logic", "AND")).upper()
    details: List[Dict[str, Any]] = []

    for condition in conditions:
        passed, detail = rule_engine.evaluate_condition(condition, asset, field_key="indicator")
        details.append(
            {
                "passed": passed,
                "condition": condition,
                "detail": detail,
            }
        )

    if not details:
        return {
            "type": "block_rule",
            "indicator": block.get("name") or "Unnamed Block",
            "condition": block.get("name") or "Unnamed Block",
            "expected": block.get("reason") or None,
            "current_value": None,
            "status": "PASS",
            "triggered": False,
        }

    triggered = any(item["passed"] for item in details) if logic == "OR" else all(item["passed"] for item in details)
    indicator = block.get("name") or _condition_indicator(details[0]["condition"], field_key="indicator")
    actual_payload = {
        _condition_indicator(item["condition"], field_key="indicator"): _detail_actual(item["detail"])
        for item in details
    }
    condition_text = f" {logic} ".join(
        format_condition_text(item["condition"], field_key="indicator")
        for item in details
    )
    return {
        "type": "block_rule",
        "indicator": indicator,
        "condition": condition_text,
        "expected": block.get("reason") or condition_text,
        "current_value": actual_payload if len(actual_payload) > 1 else next(iter(actual_payload.values()), None),
        "status": "FAIL" if triggered else "PASS",
        "triggered": triggered,
    }


def _build_decision_details(trace: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    normalized_trace = [jsonable_value(item) for item in trace]
    indicators: List[str] = []
    conditions: List[str] = []
    current_values: Dict[str, Any] = {}
    expected_values: Dict[str, Any] = {}

    for item in normalized_trace:
        indicator = str(item.get("indicator") or "Unknown")
        if indicator not in indicators:
            indicators.append(indicator)

        condition = str(item.get("condition") or "")
        if condition and condition not in conditions:
            conditions.append(condition)

        current_values[indicator] = item.get("current_value")
        expected_values[indicator] = item.get("expected")

    return {
        "filters": [item for item in normalized_trace if item.get("type") == "filter"],
        "indicators": indicators,
        "conditions": conditions,
        "current_values": current_values,
        "expected_values": expected_values,
        "evaluation_trace": normalized_trace,
    }


def build_analysis_snapshot(
    *,
    symbol: str,
    stage: str,
    profile_id: Optional[str],
    status: str,
    trace: Sequence[Dict[str, Any]],
    timestamp: str,
) -> Dict[str, Any]:
    details = _build_decision_details(trace)
    failed_indicators = [
        str(item.get("indicator") or "Unknown")
        for item in details["evaluation_trace"]
        if item.get("status") == "FAIL"
    ]
    return {
        "symbol": symbol,
        "stage": stage,
        "profile_id": profile_id,
        "status": status,
        "failed_indicators": failed_indicators,
        "conditions": details["conditions"],
        "current_values": details["current_values"],
        "expected_values": details["expected_values"],
        "details": details,
        "timestamp": timestamp,
    }


def evaluate_rejections(
    assets: Sequence[Dict[str, Any]],
    *,
    profile_config: Optional[Dict[str, Any]],
    stage: str,
    profile_id: Optional[str],
    selected_filter_conditions: Optional[Sequence[Dict[str, Any]]] = None,
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Return (approved_assets, rejection_logs) for the profile block/filter gates."""
    if not assets:
        return [], []

    profile_config = profile_config or {}
    rule_engine = RuleEngine()
    filters_all = list((profile_config.get("filters") or {}).get("conditions", []) or [])
    selected_filters = list(selected_filter_conditions) if selected_filter_conditions is not None else list(filters_all)
    filter_logic = str((profile_config.get("filters") or {}).get("logic", "AND")).upper()
    filter_logic_keys = {id(item) for item in selected_filters}
    block_rules = [
        block
        for block in ((profile_config.get("block_rules") or {}).get("blocks", []) or [])
        if block.get("enabled", True)
    ]

    approved: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    for asset in assets:
        symbol = asset.get("symbol")
        trace: List[Dict[str, Any]] = []
        failed_trace: Optional[Dict[str, Any]] = None
        stopped = False

        for block in block_rules:
            if stopped:
                trace.append(
                    {
                        "type": "block_rule",
                        "indicator": block.get("name") or "Unnamed Block",
                        "condition": block.get("reason") or block.get("name") or "Skipped",
                        "expected": block.get("reason") or None,
                        "current_value": None,
                        "status": "SKIPPED",
                    }
                )
                continue
            block_trace = _evaluate_block_rule(rule_engine, asset, block)
            trace.append(block_trace)
            if block_trace.get("triggered"):
                failed_trace = block_trace
                stopped = True

        filter_results: List[Dict[str, Any]] = []
        for condition in filters_all:
            if stopped:
                trace.append(
                    {
                        "type": "filter",
                        "indicator": _condition_indicator(condition, field_key="field"),
                        "condition": format_condition_text(condition, field_key="field"),
                        "expected": format_expected(condition),
                        "current_value": None,
                        "status": "SKIPPED",
                    }
                )
                continue

            if id(condition) not in filter_logic_keys:
                trace.append(
                    {
                        "type": "filter",
                        "indicator": _condition_indicator(condition, field_key="field"),
                        "condition": format_condition_text(condition, field_key="field"),
                        "expected": format_expected(condition),
                        "current_value": None,
                        "status": "SKIPPED",
                    }
                )
                continue

            filter_trace = _evaluate_filter(rule_engine, asset, condition)
            trace.append(filter_trace)
            filter_results.append(filter_trace)
            if filter_logic != "OR" and filter_trace["status"] == "FAIL":
                failed_trace = filter_trace
                stopped = True

        if failed_trace is None and filter_logic == "OR" and filter_results and not any(
            item["status"] == "PASS" for item in filter_results
        ):
            failed_trace = next((item for item in filter_results if item["status"] == "FAIL"), None)

        if failed_trace is None:
            approved.append(
                {
                    **asset,
                    "analysis_snapshot": build_analysis_snapshot(
                        symbol=str(symbol or ""),
                        stage=stage,
                        profile_id=profile_id,
                        status="approved",
                        trace=trace,
                        timestamp=timestamp,
                    ),
                }
            )
            continue

        analysis_snapshot = build_analysis_snapshot(
            symbol=str(symbol or ""),
            stage=stage,
            profile_id=profile_id,
            status="rejected",
            trace=trace,
            timestamp=timestamp,
        )
        rejected.append(
            {
                "symbol": symbol,
                "stage": stage,
                "profile_id": profile_id,
                "failed_type": failed_trace["type"],
                "failed_indicator": failed_trace["indicator"],
                "condition": failed_trace["condition"],
                "current_value": jsonable_value(failed_trace.get("current_value")),
                "expected": failed_trace.get("expected"),
                "timestamp": timestamp,
                "evaluation_trace": jsonable_value(trace),
                "status": analysis_snapshot["status"],
                "details": analysis_snapshot["details"],
                "failed_indicators": analysis_snapshot["failed_indicators"],
                "conditions": analysis_snapshot["conditions"],
                "current_values": analysis_snapshot["current_values"],
                "expected_values": analysis_snapshot["expected_values"],
                "analysis_snapshot": analysis_snapshot,
            }
        )

    return approved, rejected


def rejection_metrics(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    items = list(rows)
    total = len(items)
    by_indicator: Dict[str, int] = {}
    block_count = 0

    for item in items:
        indicator = item.get("failed_indicator") or "Unknown"
        by_indicator[indicator] = by_indicator.get(indicator, 0) + 1
        if item.get("failed_type") == "block_rule":
            block_count += 1

    ranked = sorted(by_indicator.items(), key=lambda entry: (-entry[1], entry[0]))
    return {
        "total_rejected": total,
        "block_rule_count": block_count,
        "filter_count": total - block_count,
        "block_rule_rate": round((block_count / total) * 100, 1) if total else 0.0,
        "top_indicator": ranked[0][0] if ranked else None,
        "by_indicator": [
            {
                "indicator": indicator,
                "count": count,
                "percentage": round((count / total) * 100, 1) if total else 0.0,
            }
            for indicator, count in ranked
        ],
    }
