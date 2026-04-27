from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .indicator_validity import RuleStatus
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


# ---------------------------------------------------------------------------
# Trace-asset builder
# ---------------------------------------------------------------------------
#
# `build_asset_evaluation_trace` and `_passes_profile_filters` both consume a
# flat dict per symbol that mixes market_metadata fields with
# indicators_json fields. Two independent call-sites in the watchlists API
# used to construct that dict by hand and disagreed on the merge order —
# the older one seeded the dict with `meta.get(...)` first (often `None`)
# and then merged indicators with a `if k not in asset_entry` guard, so
# any meta `None` would shadow a perfectly valid indicator value (the SUI
# "SEM DADOS / aguardando coleta" false-negative reported in task #69).
#
# This helper centralises the contract so the merge order can never drift
# again:
#
#   * `indicators_json` is the SOLE source of truth for indicator values.
#   * `market_metadata` complements `current_price`, `price_change_24h`,
#     `volume_24h`, `market_cap`.
#   * `spread_pct` and `orderbook_depth_usdt` are hybrid — indicators win
#     when present, otherwise meta supplies them.
#   * Common alias spellings (`bollinger_width`, `volume_24h_usdt`, ...)
#     are normalised so a profile rule that references either spelling
#     finds the value.

# Required meta-source fields. Always present in the returned dict (None
# when not yet collected) so legacy DB-write paths that look up these
# keys directly do not raise KeyError.
_META_REQUIRED_FIELDS = (
    "current_price",
    "price_change_24h",
    "volume_24h",
    "market_cap",
)

# Hybrid fields. Indicator value wins; meta value used as fallback.
_HYBRID_FIELDS = ("spread_pct", "orderbook_depth_usdt")

# Indicator-payload string keys allowed to propagate to the trace asset.
# Other strings are dropped because the rule engine treats them as
# unevaluable scalars.
_PROFILE_STRING_INDICATORS = frozenset(
    {"macd_signal", "psar_trend", "ema_align_label"}
)

# Variant aliases. When a profile rule references one of these field
# names, we want the trace to find the canonical value (and vice versa)
# so that legacy DB drift is not falsely reported as "no data".
_INDICATOR_FIELD_ALIASES: Dict[str, tuple] = {
    "bb_width":             ("bollinger_width", "bollinger_band_width"),
    "volume_24h":           ("volume_24h_usdt", "vol_24h"),
    "volume_spike":         ("vol_spike",),
    "price_change_24h":     ("price_change_24h_pct",),
    "spread_pct":           ("spread_percent",),
    "atr_pct":              ("atr_percent",),
    "orderbook_depth_usdt": ("orderbook_depth",),
    "current_price":        ("price",),
}


def build_trace_asset(
    symbol: str,
    *,
    indicators: Optional[Dict[str, Any]] = None,
    meta: Optional[Dict[str, Any]] = None,
    alpha_score: Optional[float] = None,
) -> Dict[str, Any]:
    """Merge indicators_json + market_metadata into a single dict suitable
    for `build_asset_evaluation_trace` and `_passes_profile_filters`.

    Critical invariant: if an indicator value is non-None it must NEVER
    be shadowed by a None coming from meta. This is the regression
    locked in by `tests/test_build_trace_asset.py`.
    """
    meta = meta or {}
    indicators = indicators or {}
    asset: Dict[str, Any] = {"symbol": symbol}

    # 1. Required meta fields — always present (None when not collected).
    asset["current_price"] = meta.get("current_price", meta.get("price"))
    asset["price_change_24h"] = meta.get("price_change_24h")
    asset["volume_24h"] = meta.get("volume_24h")
    asset["market_cap"] = meta.get("market_cap")

    # 2. Indicator payload — wins for any field where the value is usable.
    #    Non-scalar entries are dropped to keep the dict JSON-safe.
    for key, value in indicators.items():
        if value is None:
            continue
        if isinstance(value, (int, float, bool)) or (
            isinstance(value, str) and key in _PROFILE_STRING_INDICATORS
        ):
            asset[key] = value

    # 3. Hybrid fields: prefer indicators (already merged), else meta.
    for key in _HYBRID_FIELDS:
        if asset.get(key) is None:
            value = meta.get(key)
            if value is not None:
                asset[key] = value

    # 4. Alias map — populate canonical from variant and vice versa so
    #    legacy field naming does not cause a false 'data not available'.
    for canonical, variants in _INDICATOR_FIELD_ALIASES.items():
        canon_value = asset.get(canonical)
        for variant in variants:
            variant_value = asset.get(variant)
            if canon_value is None and variant_value is not None:
                asset[canonical] = variant_value
                canon_value = variant_value
        if canon_value is not None:
            for variant in variants:
                if asset.get(variant) is None:
                    asset[variant] = canon_value

    # 5. price/current_price compatibility (legacy rules query 'price').
    if asset.get("price") is None and asset.get("current_price") is not None:
        asset["price"] = asset["current_price"]

    # 6. change_24h alias kept for legacy profile filters.
    if asset.get("change_24h") is None and asset.get("price_change_24h") is not None:
        asset["change_24h"] = asset["price_change_24h"]

    # 7. Alpha score — always present; None propagates as SKIPPED in the
    #    rule engine via indicator_validity.
    asset["alpha_score"] = alpha_score
    if "score" not in asset:
        asset["score"] = alpha_score

    return asset


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
    status, detail = rule_engine.evaluate_condition_status(condition, asset, field_key="field")
    payload: Dict[str, Any] = {
        "type": "filter",
        "indicator": _condition_indicator(condition, field_key="field"),
        "condition": format_condition_text(condition, field_key="field"),
        "expected": format_expected(condition),
        "current_value": _detail_actual(detail),
        "status": status.value,
    }
    if status == RuleStatus.SKIPPED and isinstance(detail, dict) and detail.get("reason"):
        payload["reason"] = detail["reason"]
    return payload


def _evaluate_entry_trigger(
    rule_engine: RuleEngine,
    asset: Dict[str, Any],
    condition: Dict[str, Any],
) -> Dict[str, Any]:
    status, detail = rule_engine.evaluate_condition_status(condition, asset, field_key="indicator")
    payload: Dict[str, Any] = {
        "type": "entry_trigger",
        "indicator": _condition_indicator(condition, field_key="indicator"),
        "condition": format_condition_text(condition, field_key="indicator"),
        "expected": format_expected(condition),
        "current_value": _detail_actual(detail),
        "status": status.value,
    }
    if status == RuleStatus.SKIPPED and isinstance(detail, dict) and detail.get("reason"):
        payload["reason"] = detail["reason"]
    return payload


def _evaluate_signal_condition(
    rule_engine: RuleEngine,
    asset: Dict[str, Any],
    condition: Dict[str, Any],
) -> Dict[str, Any]:
    field_key = "indicator" if condition.get("indicator") else "field"
    status, detail = rule_engine.evaluate_condition_status(condition, asset, field_key=field_key)
    payload: Dict[str, Any] = {
        "type": "signal",
        "indicator": _condition_indicator(condition, field_key=field_key),
        "condition": format_condition_text(condition, field_key=field_key),
        "expected": format_expected(condition),
        "current_value": _detail_actual(detail),
        "status": status.value,
    }
    if status == RuleStatus.SKIPPED and isinstance(detail, dict) and detail.get("reason"):
        payload["reason"] = detail["reason"]
    return payload


def _evaluate_block_rule(
    rule_engine: RuleEngine,
    asset: Dict[str, Any],
    block: Dict[str, Any],
) -> Dict[str, Any]:
    conditions = block.get("conditions", []) or []
    logic = str(block.get("logic", "AND")).upper()
    details: List[Dict[str, Any]] = []

    for condition in conditions:
        status, detail = rule_engine.evaluate_condition_status(
            condition, asset, field_key="indicator"
        )
        details.append(
            {
                "status": status,
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
            "status": RuleStatus.PASS.value,
            "triggered": False,
        }

    # Aggregate per-condition tristate into a block-level tristate using the
    # same semantics as BlockEngine._evaluate_block_group:
    #   - AND with any SKIPPED → block SKIPPED.
    #   - OR with all SKIPPED  → block SKIPPED.
    #   - Otherwise the block is decided by the non-SKIPPED conditions.
    statuses = [item["status"] for item in details]
    if logic == "OR":
        decided = [s for s in statuses if s != RuleStatus.SKIPPED]
        if not decided:
            block_status = RuleStatus.SKIPPED
        else:
            block_status = RuleStatus.PASS if any(s == RuleStatus.PASS for s in decided) else RuleStatus.FAIL
    else:
        if any(s == RuleStatus.SKIPPED for s in statuses):
            block_status = RuleStatus.SKIPPED
        else:
            block_status = RuleStatus.PASS if all(s == RuleStatus.PASS for s in statuses) else RuleStatus.FAIL

    skip_reason: Optional[str] = None
    if block_status == RuleStatus.SKIPPED:
        # Preserve the most informative reason — invalid_value beats
        # not_available so traders can tell when an indicator was present
        # but implausible (e.g. taker_ratio == 0).
        seen_reasons = [
            item["detail"].get("reason")
            for item in details
            if item["status"] == RuleStatus.SKIPPED and isinstance(item.get("detail"), dict)
        ]
        if "indicator_invalid_value" in seen_reasons:
            skip_reason = "indicator_invalid_value"
        else:
            skip_reason = next((r for r in seen_reasons if r), "indicator_not_available")

    triggered = block_status == RuleStatus.PASS
    indicator = block.get("name") or _condition_indicator(details[0]["condition"], field_key="indicator")
    actual_payload = {
        _condition_indicator(item["condition"], field_key="indicator"): _detail_actual(item["detail"])
        for item in details
    }
    condition_text = f" {logic} ".join(
        format_condition_text(item["condition"], field_key="indicator")
        for item in details
    )
    # External callers (build_asset_evaluation_trace) treat status=="FAIL"
    # as "this block triggered → reject the asset". We keep that contract:
    # a SKIPPED block is reported as SKIPPED so the asset is NOT rejected.
    if block_status == RuleStatus.PASS:
        external_status = "FAIL"  # block triggered, asset must be rejected
    elif block_status == RuleStatus.FAIL:
        external_status = "PASS"  # block did not trigger, asset is fine
    else:
        external_status = RuleStatus.SKIPPED.value
    payload: Dict[str, Any] = {
        "type": "block_rule",
        "indicator": indicator,
        "condition": condition_text,
        "expected": block.get("reason") or condition_text,
        "current_value": actual_payload if len(actual_payload) > 1 else next(iter(actual_payload.values()), None),
        "status": external_status,
        "triggered": triggered,
    }
    if external_status == RuleStatus.SKIPPED.value and skip_reason:
        payload["reason"] = skip_reason
    return payload


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


def _normalized_trace_item(item: Dict[str, Any]) -> Dict[str, Any]:
    normalized: Dict[str, Any] = {
        "type": item["type"],
        "name": item.get("indicator") or item.get("name") or "Unknown",
        "indicator": item.get("indicator") or item.get("name") or "Unknown",
        "condition": item.get("condition"),
        "expected": item.get("expected"),
        "current_value": jsonable_value(item.get("current_value")),
        "status": item["status"],
    }
    # Preserve the SKIPPED reason so frontend traces can explain why a
    # rule was undecidable (indicator_not_available vs indicator_invalid_value).
    reason = item.get("reason")
    if reason:
        normalized["reason"] = reason
    return normalized


def _skipped_block_rule(block: Dict[str, Any]) -> Dict[str, Any]:
    name = block.get("name") or "Unnamed Block"
    return _normalized_trace_item(
        {
            "type": "block_rule",
            "indicator": name,
            "condition": block.get("reason") or name,
            "expected": block.get("reason"),
            "current_value": None,
            "status": "SKIPPED",
        }
    )


def _skipped_filter(condition: Dict[str, Any]) -> Dict[str, Any]:
    return _normalized_trace_item(
        {
            "type": "filter",
            "indicator": _condition_indicator(condition, field_key="field"),
            "condition": format_condition_text(condition, field_key="field"),
            "expected": format_expected(condition),
            "current_value": None,
            "status": "SKIPPED",
        }
    )


def _build_asset_evaluation_trace(
    rule_engine: RuleEngine,
    asset: Dict[str, Any],
    *,
    profile_config: Optional[Dict[str, Any]],
    selected_filter_conditions: Optional[Sequence[Dict[str, Any]]] = None,
) -> tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    profile_config = profile_config or {}
    block_rules = [
        block
        for block in ((profile_config.get("block_rules") or {}).get("blocks", []) or [])
        if block.get("enabled", True)
    ]
    filters_config = (profile_config.get("filters") or {})
    filters = (
        list(selected_filter_conditions)
        if selected_filter_conditions is not None
        else list(filters_config.get("conditions", []) or [])
    )
    filter_logic = str(filters_config.get("logic", "AND")).upper()

    entry_triggers = [
        cond
        for cond in ((profile_config.get("entry_triggers") or {}).get("conditions", []) or [])
        if cond.get("enabled", True)
    ]
    signal_conditions = [
        cond
        for cond in ((profile_config.get("signals") or {}).get("conditions", []) or [])
        if cond.get("enabled", True)
    ]

    trace: List[Dict[str, Any]] = []
    failed_trace: Optional[Dict[str, Any]] = None

    for index, block in enumerate(block_rules):
        block_trace = _normalized_trace_item(_evaluate_block_rule(rule_engine, asset, block))
        trace.append(block_trace)
        if block_trace["status"] == "FAIL":
            failed_trace = block_trace
            for remaining_block in block_rules[index + 1:]:
                trace.append(_skipped_block_rule(remaining_block))
            for condition in filters:
                trace.append(_skipped_filter(condition))
            for condition in entry_triggers:
                trace.append(_normalized_trace_item(_evaluate_entry_trigger(rule_engine, asset, condition)))
            for condition in signal_conditions:
                trace.append(_normalized_trace_item(_evaluate_signal_condition(rule_engine, asset, condition)))
            return trace, failed_trace

    filter_results: List[Dict[str, Any]] = []
    for index, condition in enumerate(filters):
        filter_trace = _normalized_trace_item(_evaluate_filter(rule_engine, asset, condition))
        trace.append(filter_trace)
        filter_results.append(filter_trace)
        if filter_logic != "OR" and filter_trace["status"] == "FAIL":
            failed_trace = filter_trace
            for remaining_condition in filters[index + 1:]:
                trace.append(_skipped_filter(remaining_condition))
            for condition in entry_triggers:
                trace.append(_normalized_trace_item(_evaluate_entry_trigger(rule_engine, asset, condition)))
            for condition in signal_conditions:
                trace.append(_normalized_trace_item(_evaluate_signal_condition(rule_engine, asset, condition)))
            return trace, failed_trace

    if filter_logic == "OR" and filter_results and not any(
        item["status"] == "PASS" for item in filter_results
    ):
        failed_trace = next((item for item in filter_results if item["status"] == "FAIL"), None)
        for condition in entry_triggers:
            trace.append(_normalized_trace_item(_evaluate_entry_trigger(rule_engine, asset, condition)))
        for condition in signal_conditions:
            trace.append(_normalized_trace_item(_evaluate_signal_condition(rule_engine, asset, condition)))
        return trace, failed_trace

    for condition in entry_triggers:
        result = _normalized_trace_item(_evaluate_entry_trigger(rule_engine, asset, condition))
        trace.append(result)
        if failed_trace is None and result["status"] == "FAIL":
            failed_trace = result
    for condition in signal_conditions:
        result = _normalized_trace_item(_evaluate_signal_condition(rule_engine, asset, condition))
        trace.append(result)
        if failed_trace is None and result["status"] == "FAIL":
            failed_trace = result

    return trace, failed_trace


def build_asset_evaluation_trace(
    asset: Dict[str, Any],
    *,
    profile_config: Optional[Dict[str, Any]],
    selected_filter_conditions: Optional[Sequence[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    trace, _ = _build_asset_evaluation_trace(
        RuleEngine(),
        asset,
        profile_config=profile_config,
        selected_filter_conditions=selected_filter_conditions,
    )
    return jsonable_value(trace)


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
    approved: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    for asset in assets:
        symbol = str(asset.get("symbol") or "")
        trace, failed_trace = _build_asset_evaluation_trace(
            rule_engine,
            asset,
            profile_config=profile_config,
            selected_filter_conditions=selected_filter_conditions,
        )

        json_trace = jsonable_value(trace)

        if failed_trace is None:
            approved.append(
                {
                    **asset,
                    "symbol": symbol,
                    "evaluation_trace": json_trace,
                    "analysis_snapshot": build_analysis_snapshot(
                        symbol=symbol,
                        stage=stage,
                        profile_id=profile_id,
                        status="approved",
                        trace=json_trace,
                        timestamp=timestamp,
                    ),
                }
            )
            continue

        analysis_snapshot = build_analysis_snapshot(
            symbol=symbol,
            stage=stage,
            profile_id=profile_id,
            status="rejected",
            trace=json_trace,
            timestamp=timestamp,
        )
        rejected.append(
            {
                "symbol": symbol,
                "stage": stage,
                "profile_id": profile_id,
                "failed_type": failed_trace["type"],
                "failed_indicator": failed_trace["indicator"],
                "condition": failed_trace.get("condition"),
                "current_value": failed_trace.get("current_value"),
                "expected": failed_trace.get("expected"),
                "timestamp": timestamp,
                "evaluation_trace": json_trace,
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
