"""Provenance-aware L3 authorization contract v3.

The current L3 decision remains operational while ``mode=SHADOW``. This
module builds an independent fail-closed envelope whose feature identity is
source and market scoped; a candidate can never satisfy another source,
timeframe, window, provider policy, or candle policy by flat-key fallback.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterable, Mapping, Optional


CONTRACT_VERSION = "l3_authorization_contract_v3"
CONTRACT_MODE = "SHADOW"
CANONICALIZATION_VERSION = "feature_identity_v1"

_LIVE_TRADE_FLOW = {
    "taker_ratio", "volume_delta", "buy_pressure",
    "taker_buy_volume", "taker_sell_volume",
}
_LIVE_ORDER_BOOK = {
    "orderbook_pressure", "bid_ask_imbalance",
    "orderbook_depth_usdt", "spread_pct", "spread",
}
_SUPPORTED_CANDLE_POLICIES = {"CLOSED_ONLY", "CURRENT_ALLOWED"}
_SUPPORTED_OPERATORS = {
    "=", "==", "!=", ">", ">=", "<", "<=", "between",
    "is_true", "is_false", "in", "not_in",
}


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        value = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return str(value).replace("+00:00", "Z")


def _canonicalize(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _canonicalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonicalize(item) for item in value]
    if isinstance(value, datetime):
        return _iso(value)
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, (int, float, Decimal)):
        number = Decimal(str(value))
        if not number.is_finite():
            return str(value)
        if number == 0:
            return 0
        if number == number.to_integral():
            return int(number)
        return float(number.normalize())
    return str(value)


def canonical_hash(value: Any) -> str:
    raw = json.dumps(
        _canonicalize(value), sort_keys=True, separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def normalize_symbol(symbol: str) -> str:
    return str(symbol or "").strip().upper().replace("-", "_").replace("/", "_")


def _as_utc(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _normalize_source(value: Any) -> Optional[str]:
    raw = str(value or "").strip().lower()
    if raw in {"live_order_flow", "live_trade_flow", "gate_io_trades_ws"}:
        return "live_trade_flow"
    if raw in {"live_order_book", "gate_io_orderbook_ws"}:
        return "live_order_book"
    if raw in {"ohlcv", "gate_candles", "candle_computed"}:
        return "ohlcv"
    return raw or None


def _normalize_candle_policy(value: Any) -> Optional[str]:
    raw = str(value or "").strip().upper()
    aliases = {"CLOSED": "CLOSED_ONLY", "CLOSED_CANDLE": "CLOSED_ONLY"}
    return aliases.get(raw, raw or None)


def _source_for_db_candidate(candidate: dict) -> str:
    normalized = _normalize_source(candidate.get("source"))
    return normalized if normalized in {"live_trade_flow", "live_order_book"} else "ohlcv"


def _condition_parameters(condition: dict) -> dict:
    parameters = deepcopy(condition.get("parameters") or {})
    if condition.get("reference_window") is not None:
        parameters["reference_window"] = deepcopy(condition.get("reference_window"))
    return parameters


def _candidate_parameters(candidate: dict) -> dict:
    parameters = deepcopy(candidate.get("parameters") or {})
    if candidate.get("reference_window") is not None:
        parameters["reference_window"] = deepcopy(candidate.get("reference_window"))
    return parameters


def _feature_identity(value: dict) -> dict:
    return {
        "market_scope": deepcopy(value.get("market_scope")),
        "indicator": value.get("indicator"),
        "source": _normalize_source(value.get("source")),
        "source_provider": value.get("source_provider"),
        "timeframe": value.get("timeframe"),
        "window_seconds": value.get("window_seconds"),
        "snapshot": value.get("snapshot"),
        "period": value.get("period"),
        "parameters": _candidate_parameters(value),
        "candle_policy": _normalize_candle_policy(value.get("candle_policy")),
    }


def _identity_hash(identity: dict, provider_policy_id: Any) -> str:
    return canonical_hash({
        "canonicalization_version": CANONICALIZATION_VERSION,
        "feature_identity": identity,
        "provider_policy_id": provider_policy_id,
    })


def _registry_candidate(raw: dict, *, market_scope: dict, evaluated_at: datetime) -> dict:
    source = _source_for_db_candidate(raw)
    source_timestamp = raw.get("source_timestamp")
    age = raw.get("age_seconds")
    if age is None:
        source_at = _as_utc(source_timestamp)
        if source_at is not None:
            age = max(0.0, (evaluated_at - source_at).total_seconds())
    return {
        "market_scope": deepcopy(raw.get("market_scope") or market_scope),
        "indicator": str(raw.get("indicator") or ""),
        "actual": raw.get("actual"),
        "source": source,
        "source_provider": raw.get("source_provider") or raw.get("source"),
        "provider_policy_id": raw.get("provider_policy_id"),
        "timeframe": raw.get("timeframe"),
        "window_seconds": raw.get("window_seconds"),
        "snapshot": raw.get("snapshot"),
        "period": raw.get("period"),
        "parameters": _candidate_parameters(raw),
        "candle_policy": _normalize_candle_policy(raw.get("candle_policy")),
        "candle_closed": raw.get("candle_closed"),
        "source_timestamp": _iso(source_timestamp),
        "computed_at": _iso(raw.get("computed_at")),
        "available_at": _iso(raw.get("available_at")),
        "evaluated_at": _iso(evaluated_at),
        "age_seconds": age,
        "stale": bool(raw.get("stale", False)),
        "fallback_used": bool(raw.get("fallback_used", False)),
        "partial_window": bool(raw.get("partial_window", False)),
        "coverage_pct": raw.get("coverage_pct"),
    }


def _live_candidates(
    snapshot: Any,
    *,
    source: str,
    indicators: set[str],
    market_scope: dict,
    evaluated_at: datetime,
) -> list[dict]:
    if not isinstance(snapshot, dict):
        return []
    meta = snapshot.get("meta") or {}
    values = snapshot.get("values") or {}
    results = []
    for indicator in sorted(indicators):
        if indicator not in values or values.get(indicator) is None:
            continue
        raw = {
            "market_scope": meta.get("market_scope") or market_scope,
            "indicator": indicator,
            "actual": values.get(indicator),
            "source": source,
            "source_provider": meta.get("source_provider"),
            "provider_policy_id": meta.get("provider_policy_id"),
            "timeframe": None,
            "window_seconds": meta.get("window_seconds"),
            "snapshot": meta.get("snapshot"),
            "period": None,
            "parameters": meta.get("parameters") or {},
            "candle_policy": None,
            "candle_closed": None,
            "source_timestamp": meta.get("source_timestamp"),
            "computed_at": meta.get("computed_at"),
            "available_at": meta.get("available_at"),
            "age_seconds": meta.get("age_seconds"),
            "stale": meta.get("stale", False),
            "fallback_used": meta.get("fallback_used", False),
            "partial_window": meta.get("partial_window", False),
            "coverage_pct": meta.get("coverage_pct"),
        }
        candidate = _registry_candidate(
            raw, market_scope=market_scope, evaluated_at=evaluated_at
        )
        candidate["source"] = source
        results.append(candidate)
    return results


def build_feature_registry(asset: dict, *, evaluated_at: datetime) -> list[dict]:
    """Return immutable candidates without consulting the legacy flat map."""
    registry: list[dict] = []
    market_scope = deepcopy(asset.get("_l3_market_scope") or {
        "exchange": "gate_io",
        "market_type": "futures" if asset.get("is_futures") else "spot",
        "normalized_symbol": normalize_symbol(asset.get("symbol")),
    })
    merged = asset.get("_merged_indicators")
    raw_candidates = getattr(merged, "candidates", []) if merged is not None else []
    for raw in raw_candidates:
        if not raw.get("indicator"):
            continue
        registry.append(_registry_candidate(
            raw, market_scope=market_scope, evaluated_at=evaluated_at
        ))
    registry.extend(_live_candidates(
        asset.get("_l3_live_order_flow_snapshot"),
        source="live_trade_flow",
        indicators=_LIVE_TRADE_FLOW,
        market_scope=market_scope,
        evaluated_at=evaluated_at,
    ))
    registry.extend(_live_candidates(
        asset.get("_l3_live_order_book_snapshot"),
        source="live_order_book",
        indicators=_LIVE_ORDER_BOOK,
        market_scope=market_scope,
        evaluated_at=evaluated_at,
    ))
    registry.sort(key=lambda item: json.dumps(
        _canonicalize(_feature_identity(item)), sort_keys=True, separators=(",", ":")
    ))
    return registry


def _required(condition: dict, *, default: bool = False) -> bool:
    if "required_for_evaluation" in condition:
        return bool(condition.get("required_for_evaluation"))
    if "required" in condition:
        return bool(condition.get("required"))
    return default


def validate_condition_contract(
    condition: dict,
    *,
    default_timeframe: Optional[str] = None,
    required_default: bool = False,
) -> list[str]:
    """Validate the same feature contract used by ingress and runtime."""
    del default_timeframe  # timeframe inheritance is forbidden in v3.
    indicator = condition.get("indicator") or condition.get("field")
    if not indicator:
        return ["INDICATOR_REQUIRED"]
    source = _normalize_source(condition.get("source"))
    required = _required(condition, default=required_default)
    errors: list[str] = []
    if not source:
        return ["SOURCE_REQUIRED"]
    if source not in {"ohlcv", "live_trade_flow", "live_order_book", "decision_context"}:
        return ["SOURCE_UNSUPPORTED"]
    if not condition.get("source_provider"):
        errors.append("SOURCE_PROVIDER_REQUIRED")
    if not condition.get("provider_policy_id"):
        errors.append("PROVIDER_POLICY_REQUIRED")
    if required and condition.get("max_age_seconds") is None:
        errors.append("FRESHNESS_POLICY_REQUIRED")
    elif condition.get("max_age_seconds") is not None:
        try:
            if float(condition.get("max_age_seconds")) <= 0:
                errors.append("MAX_AGE_SECONDS_INVALID")
        except (TypeError, ValueError):
            errors.append("MAX_AGE_SECONDS_INVALID")
    operator = condition.get("operator")
    if operator not in _SUPPORTED_OPERATORS:
        errors.append("OPERATOR_UNSUPPORTED")
    if operator == "between" and not all(key in condition for key in ("min", "max")):
        errors.append("BETWEEN_BOUNDS_REQUIRED")
    if operator not in {"between", "is_true", "is_false"} and operator in _SUPPORTED_OPERATORS:
        if "value" not in condition:
            errors.append("OPERATOR_VALUE_REQUIRED")
    if indicator == "breakout_distance_pct" and not condition.get("reference_window"):
        errors.append("REFERENCE_WINDOW_REQUIRED")
    if source == "live_trade_flow":
        if condition.get("window_seconds") is None:
            errors.append("WINDOW_SECONDS_REQUIRED")
        if condition.get("timeframe") is not None:
            errors.append("TIMEFRAME_NOT_ALLOWED_FOR_LIVE_TRADE_FLOW")
        if condition.get("snapshot") is not None:
            errors.append("SNAPSHOT_NOT_ALLOWED_FOR_LIVE_TRADE_FLOW")
    elif source == "live_order_book":
        if condition.get("timeframe") is not None:
            errors.append("TIMEFRAME_NOT_ALLOWED_FOR_LIVE_ORDER_BOOK")
        if condition.get("snapshot") is not True and condition.get("window_seconds") is None:
            errors.append("SNAPSHOT_OR_WINDOW_REQUIRED")
    elif source == "ohlcv":
        if not condition.get("timeframe"):
            errors.append("TIMEFRAME_REQUIRED")
        if condition.get("window_seconds") is not None:
            errors.append("WINDOW_SECONDS_NOT_ALLOWED_FOR_OHLCV")
        candle_policy = _normalize_candle_policy(condition.get("candle_policy"))
        if candle_policy not in _SUPPORTED_CANDLE_POLICIES:
            errors.append("CANDLE_POLICY_REQUIRED_OR_UNSUPPORTED")
    return errors


def validate_profile_contract(profile_config: dict) -> list[dict]:
    errors: list[dict] = []
    sections: list[tuple[str, Iterable[dict], bool]] = []
    for section in ("filters", "signals", "entry_triggers"):
        conditions = ((profile_config or {}).get(section) or {}).get("conditions") or []
        sections.append((section, conditions, False))
    blocks = ((profile_config or {}).get("block_rules") or {}).get("blocks") or []
    for block_index, block in enumerate(blocks):
        conditions = block.get("conditions") if isinstance(block, dict) else None
        if conditions is None and isinstance(block, dict):
            conditions = [block]
        sections.append((f"block_rules.blocks[{block_index}]", conditions or [], True))
    for section, conditions, required_default in sections:
        for index, condition in enumerate(conditions):
            for code in validate_condition_contract(
                condition, required_default=required_default
            ):
                errors.append({"path": f"{section}.conditions[{index}]", "code": code})
    return errors


def assert_profile_contract(profile_config: dict) -> dict:
    """Reject an invalid profile atomically while preserving its payload."""
    errors = validate_profile_contract(profile_config)
    if errors:
        raise ValueError(
            "L3_FEATURE_IDENTITY_INVALID:"
            + json.dumps(errors, sort_keys=True, separators=(",", ":"))
        )
    return profile_config


def _expected_identity(condition: dict, market_scope: dict) -> dict:
    return {
        "market_scope": deepcopy(market_scope),
        "indicator": condition.get("indicator") or condition.get("field"),
        "source": _normalize_source(condition.get("source")),
        "source_provider": condition.get("source_provider"),
        "timeframe": condition.get("timeframe"),
        "window_seconds": condition.get("window_seconds"),
        "snapshot": condition.get("snapshot"),
        "period": condition.get("period"),
        "parameters": _condition_parameters(condition),
        "candle_policy": _normalize_candle_policy(condition.get("candle_policy")),
    }


def _matches_identity(candidate: dict, expected: dict, provider_policy_id: Any) -> bool:
    return (
        _feature_identity(candidate) == expected
        and candidate.get("provider_policy_id") == provider_policy_id
    )


def _identity_mismatch_reasons(
    expected: dict, provider_policy_id: Any, registry: list[dict]
) -> list[str]:
    same_indicator = [
        c for c in registry if c.get("indicator") == expected.get("indicator")
    ]
    if not same_indicator:
        return ["FEATURE_IDENTITY_NOT_AVAILABLE"]
    scoped = [c for c in same_indicator if c.get("market_scope") == expected.get("market_scope")]
    if not scoped:
        return ["MARKET_SCOPE_MISMATCH"]
    sourced = [c for c in scoped if c.get("source") == expected.get("source")]
    if not sourced:
        return ["SOURCE_MISMATCH"]
    provided = [c for c in sourced if c.get("source_provider") == expected.get("source_provider")]
    if not provided:
        return ["PROVIDER_MISMATCH"]
    policy = [c for c in provided if c.get("provider_policy_id") == provider_policy_id]
    if not policy:
        return ["PROVIDER_POLICY_MISMATCH"]
    checks = (
        ("timeframe", "TIMEFRAME_MISMATCH"),
        ("window_seconds", "WINDOW_MISMATCH"),
        ("snapshot", "SNAPSHOT_MISMATCH"),
        ("period", "PERIOD_MISMATCH"),
        ("candle_policy", "CANDLE_POLICY_MISMATCH"),
    )
    for key, reason in checks:
        matches = [c for c in policy if c.get(key) == expected.get(key)]
        if not matches:
            return [reason]
        policy = matches
    if not any(_candidate_parameters(c) == expected.get("parameters") for c in policy):
        return ["PARAMETERS_MISMATCH"]
    return ["FEATURE_IDENTITY_NOT_AVAILABLE"]


def _apply_operator(condition: dict, actual: Any) -> bool:
    operator = condition.get("operator")
    if operator == "between":
        return float(condition["min"]) <= float(actual) <= float(condition["max"])
    if operator in {"=", "=="}:
        return actual == condition.get("value")
    if operator == "!=":
        return actual != condition.get("value")
    if operator == ">":
        return float(actual) > float(condition.get("value"))
    if operator == ">=":
        return float(actual) >= float(condition.get("value"))
    if operator == "<":
        return float(actual) < float(condition.get("value"))
    if operator == "<=":
        return float(actual) <= float(condition.get("value"))
    if operator == "is_true":
        return actual is True
    if operator == "is_false":
        return actual is False
    if operator == "in":
        return actual in (condition.get("value") or [])
    if operator == "not_in":
        return actual not in (condition.get("value") or [])
    raise ValueError(f"UNSUPPORTED_OPERATOR:{operator}")


def _freshness_reasons(condition: dict, candidate: dict) -> list[str]:
    reasons: list[str] = []
    if candidate.get("fallback_used"):
        reasons.append("FALLBACK_FORBIDDEN")
    if candidate.get("partial_window"):
        reasons.append("PARTIAL_WINDOW")
    if not candidate.get("source_timestamp"):
        reasons.append("SOURCE_TIMESTAMP_MISSING")
    if candidate.get("source") == "ohlcv":
        if not candidate.get("computed_at"):
            reasons.append("COMPUTED_AT_MISSING")
        if not candidate.get("available_at"):
            reasons.append("AVAILABLE_AT_MISSING")
        policy = _normalize_candle_policy(condition.get("candle_policy"))
        if candidate.get("candle_closed") is None:
            reasons.append("CANDLE_CLOSED_STATE_MISSING")
        elif policy == "CLOSED_ONLY" and candidate.get("candle_closed") is not True:
            reasons.append("CANDLE_OPEN_FORBIDDEN")
    age = candidate.get("age_seconds")
    max_age = condition.get("max_age_seconds")
    if age is None:
        reasons.append("FEATURE_AGE_UNKNOWN")
    elif max_age is not None and float(age) > float(max_age):
        reasons.append("FEATURE_TTL_EXPIRED")
    if candidate.get("stale"):
        reasons.append("FEATURE_STALE")
    return list(dict.fromkeys(reasons))


def _condition_base(
    condition: dict,
    *,
    section: str,
    rule_id: Any,
    rule_logic: str,
    market_scope: dict,
    required_default: bool,
) -> dict:
    expected = _expected_identity(condition, market_scope)
    provider_policy_id = condition.get("provider_policy_id")
    return {
        "section": section,
        "rule_id": rule_id,
        "condition_id": condition.get("id") or condition.get("condition_id"),
        "rule_logic": rule_logic,
        "indicator": expected.get("indicator"),
        "required": _required(condition, default=required_default),
        "operator": condition.get("operator"),
        "expected": {
            key: condition.get(key) for key in ("value", "min", "max") if key in condition
        },
        "max_age_seconds": condition.get("max_age_seconds"),
        "provider_policy_id": provider_policy_id,
        "feature_identity": expected,
        "condition_contract_hash": _identity_hash(expected, provider_policy_id),
    }


def _evaluate_condition(
    condition: dict,
    registry: list[dict],
    market_scope: dict,
    *,
    section: str,
    rule_id: Any,
    rule_logic: str,
    required_default: bool,
) -> dict:
    base = _condition_base(
        condition, section=section, rule_id=rule_id, rule_logic=rule_logic,
        market_scope=market_scope, required_default=required_default,
    )
    errors = validate_condition_contract(
        condition, required_default=required_default
    )
    if errors:
        return {
            **base, "status": "CONTRACT_REJECT", "result": "CONTRACT_REJECT",
            "reason_codes": errors, "actual": None, "resolved_feature": None,
            "resolved_feature_hash": None,
        }
    expected = base["feature_identity"]
    provider_policy_id = condition.get("provider_policy_id")
    candidates = [
        candidate for candidate in registry
        if _matches_identity(candidate, expected, provider_policy_id)
    ]
    if not candidates:
        status = "CONTRACT_REJECT" if base["required"] else "SKIPPED_OPTIONAL"
        return {
            **base, "status": status, "result": status,
            "reason_codes": _identity_mismatch_reasons(
                expected, provider_policy_id, registry
            ),
            "actual": None, "resolved_feature": None,
            "resolved_feature_hash": None,
        }
    candidate = max(candidates, key=lambda item: item.get("source_timestamp") or "")
    resolved_hash = _identity_hash(_feature_identity(candidate), provider_policy_id)
    if resolved_hash != base["condition_contract_hash"]:
        return {
            **base, "status": "CONTRACT_REJECT", "result": "CONTRACT_REJECT",
            "reason_codes": ["FEATURE_IDENTITY_HASH_MISMATCH"],
            "actual": candidate.get("actual"), "resolved_feature": candidate,
            "resolved_feature_hash": resolved_hash,
        }
    invalid = _freshness_reasons(condition, candidate)
    if invalid:
        status = "CONTRACT_REJECT" if base["required"] else "SKIPPED_OPTIONAL"
        return {
            **base, "status": status, "result": status,
            "reason_codes": invalid, "actual": candidate.get("actual"),
            "resolved_feature": candidate, "resolved_feature_hash": resolved_hash,
        }
    try:
        passed = _apply_operator(condition, candidate.get("actual"))
    except Exception as exc:
        return {
            **base, "status": "CONTRACT_REJECT", "result": "CONTRACT_REJECT",
            "reason_codes": [f"EVALUATION_ERROR:{type(exc).__name__}"],
            "actual": candidate.get("actual"), "resolved_feature": candidate,
            "resolved_feature_hash": resolved_hash,
        }
    status = "PASS" if passed else "FAIL"
    return {
        **base, "status": status, "result": status, "reason_codes": [],
        "actual": candidate.get("actual"), "resolved_feature": candidate,
        "resolved_feature_hash": resolved_hash,
    }


def _not_needed(
    condition: dict,
    *,
    section: str,
    rule_id: Any,
    rule_logic: str,
    market_scope: dict,
    required_default: bool,
) -> dict:
    base = _condition_base(
        condition, section=section, rule_id=rule_id, rule_logic=rule_logic,
        market_scope=market_scope, required_default=required_default,
    )
    return {
        **base,
        "status": "NOT_NEEDED_FOR_BOOLEAN_RESULT",
        "result": "NOT_NEEDED_FOR_BOOLEAN_RESULT",
        "reason_codes": [],
        "actual": None,
        "resolved_feature": None,
        "resolved_feature_hash": None,
    }


def _evaluate_conditions(
    conditions: list[dict],
    logic: str,
    registry: list[dict],
    market_scope: dict,
    *,
    section: str,
    rule_id: Any = None,
    required_default: bool = False,
) -> dict:
    logic = str(logic or "AND").upper()
    if logic not in {"AND", "OR"}:
        return {
            "logic": logic, "passed": False, "contract_reject": True,
            "reason_codes": ["RULE_LOGIC_UNSUPPORTED"], "conditions": [],
        }
    results: list[dict] = []
    decisive: Optional[bool] = None
    contract_reject = False
    for condition in conditions:
        if decisive is not None:
            results.append(_not_needed(
                condition, section=section, rule_id=rule_id, rule_logic=logic,
                market_scope=market_scope, required_default=required_default,
            ))
            continue
        result = _evaluate_condition(
            condition, registry, market_scope, section=section, rule_id=rule_id,
            rule_logic=logic, required_default=required_default,
        )
        results.append(result)
        if result["status"] == "CONTRACT_REJECT":
            contract_reject = True
            if logic == "AND":
                decisive = False
        elif result["status"] == "FAIL" and logic == "AND":
            decisive = False
        elif result["status"] == "PASS" and logic == "OR":
            decisive = True
    evaluated = [
        result for result in results
        if result["status"] not in {
            "NOT_NEEDED_FOR_BOOLEAN_RESULT", "SKIPPED_OPTIONAL"
        }
    ]
    if not conditions:
        passed = True
    elif logic == "OR":
        passed = any(result["status"] == "PASS" for result in evaluated)
    else:
        passed = all(result["status"] == "PASS" for result in evaluated)
    return {
        "logic": logic,
        "passed": passed and not contract_reject,
        "contract_reject": contract_reject,
        "reason_codes": [],
        "conditions": results,
    }


def _evaluate_blocks(
    profile_config: dict, registry: list[dict], market_scope: dict
) -> dict:
    raw_section = (profile_config or {}).get("block_rules")
    if not isinstance(raw_section, dict):
        return {
            "blocked": False, "contract_reject": True,
            "reason_codes": ["BLOCK_RULES_SECTION_MISSING"], "blocks": [],
        }
    results: list[dict] = []
    matched = False
    contract_reject = False
    for block in raw_section.get("blocks") or []:
        block_id = block.get("id") or block.get("rule_id") or block.get("name")
        if matched:
            results.append({
                "id": block_id, "status": "NOT_NEEDED_FOR_BOOLEAN_RESULT",
                "matched": False, "contract_reject": False, "conditions": [],
            })
            continue
        conditions = block.get("conditions") if isinstance(block, dict) else None
        if conditions is None:
            conditions = [block]
        evaluated = _evaluate_conditions(
            conditions or [], block.get("logic", "AND"), registry, market_scope,
            section="block_rules", rule_id=block_id, required_default=True,
        )
        is_match = evaluated["passed"] and bool(conditions)
        matched = matched or is_match
        contract_reject = contract_reject or evaluated["contract_reject"]
        results.append({
            "id": block_id,
            "matched": is_match,
            "status": "CONTRACT_REJECT" if evaluated["contract_reject"] else (
                "MATCHED" if is_match else "NOT_MATCHED"
            ),
            **evaluated,
        })
    return {
        "blocked": matched,
        "contract_reject": contract_reject,
        "reason_codes": [],
        "blocks": results,
    }


def _contract_reasons(sections: dict, blocks: dict) -> list[str]:
    reasons: list[str] = []
    for section in sections.values():
        reasons.extend(section.get("reason_codes") or [])
        for condition in section.get("conditions") or []:
            if condition.get("status") == "CONTRACT_REJECT":
                reasons.extend(condition.get("reason_codes") or [])
    reasons.extend(blocks.get("reason_codes") or [])
    for block in blocks.get("blocks") or []:
        reasons.extend(block.get("reason_codes") or [])
        for condition in block.get("conditions") or []:
            if condition.get("status") == "CONTRACT_REJECT":
                reasons.extend(condition.get("reason_codes") or [])
    return list(dict.fromkeys(reasons))


def build_authorization_contract(
    *,
    asset: dict,
    profile_config: dict,
    legacy_decision: str,
    evaluated_at: datetime,
    profile_id: Any = None,
    profile_name: Optional[str] = None,
    profile_version: Any = None,
    watchlist_id: Any = None,
    watchlist_name: Optional[str] = None,
    watchlist_level: Optional[str] = None,
    source_watchlist_id: Any = None,
    market_type: str = "spot",
) -> dict:
    market_scope = {
        "exchange": "gate_io",
        "market_type": str(market_type or "spot").lower(),
        "normalized_symbol": normalize_symbol(asset.get("symbol")),
    }
    scoped_asset = dict(asset)
    scoped_asset["_l3_market_scope"] = market_scope
    registry = build_feature_registry(scoped_asset, evaluated_at=evaluated_at)
    sections: dict[str, dict] = {}
    for name in ("filters", "signals", "entry_triggers"):
        config = (profile_config or {}).get(name)
        if not isinstance(config, dict):
            sections[name] = {
                "logic": None, "passed": False, "contract_reject": True,
                "reason_codes": [f"{name.upper()}_SECTION_MISSING"],
                "conditions": [],
            }
        else:
            sections[name] = _evaluate_conditions(
                config.get("conditions") or [], config.get("logic", "AND"),
                registry, market_scope, section=name,
            )
    blocks = _evaluate_blocks(profile_config or {}, registry, market_scope)
    runtime_validation_errors = validate_profile_contract(profile_config or {})
    lineage_reasons = []
    if not profile_id:
        lineage_reasons.append("PROFILE_ID_MISSING")
    if not profile_name:
        lineage_reasons.append("PROFILE_NAME_MISSING")
    if profile_version is None:
        lineage_reasons.append("PROFILE_VERSION_MISSING")
    if not profile_config:
        lineage_reasons.append("RULES_SNAPSHOT_MISSING")
    evaluation_reasons = _contract_reasons(sections, blocks)
    validation_reasons = [
        f"PROFILE_VALIDATION:{item['code']}" for item in runtime_validation_errors
    ]
    contract_reject = bool(
        lineage_reasons or runtime_validation_errors or blocks["contract_reject"]
        or any(section["contract_reject"] for section in sections.values())
    )
    strategy_block = blocks["blocked"] or any(
        not section["passed"] for section in sections.values()
    )
    if contract_reject:
        authorization_status = "CONTRACT_REJECT"
        contract_decision = "BLOCK"
    elif strategy_block:
        authorization_status = "STRATEGY_BLOCK"
        contract_decision = "BLOCK"
    else:
        authorization_status = "ALLOW"
        contract_decision = "ALLOW"
    watchlist_status = "RESOLVED" if watchlist_id else "NOT_APPLICABLE"
    body = {
        "contract_version": CONTRACT_VERSION,
        "canonicalization_version": CANONICALIZATION_VERSION,
        "mode": CONTRACT_MODE,
        "valid": not contract_reject,
        "operational_effect": False,
        "market_scope": market_scope,
        "profile_lineage": {
            "profile_id": str(profile_id) if profile_id else None,
            "profile_name": profile_name,
            "profile_version": _iso(profile_version),
            "profile_config_hash": canonical_hash(profile_config or {}),
            "rules_snapshot": deepcopy(profile_config or {}),
        },
        "watchlist_lineage": {
            "required": bool(watchlist_id),
            "status": watchlist_status,
            "watchlist_id": str(watchlist_id) if watchlist_id else None,
            "watchlist_name": watchlist_name,
            "watchlist_level": watchlist_level,
            "source_watchlist_id": (
                str(source_watchlist_id) if source_watchlist_id else None
            ),
        },
        # Backward-compatible projection consumed by the outbox during rollout.
        "lineage": {
            "profile_id": str(profile_id) if profile_id else None,
            "profile_name": profile_name,
            "profile_version": _iso(profile_version),
            "watchlist_id": str(watchlist_id) if watchlist_id else None,
            "watchlist_name": watchlist_name,
            "watchlist_level": watchlist_level,
            "source_watchlist_id": (
                str(source_watchlist_id) if source_watchlist_id else None
            ),
            "watchlist_status": watchlist_status,
            "profile_config_hash": canonical_hash(profile_config or {}),
            "rules_snapshot": deepcopy(profile_config or {}),
        },
        "evaluated_at": _iso(evaluated_at),
        "feature_registry": registry,
        "feature_evaluations": [
            condition
            for section in sections.values()
            for condition in section.get("conditions") or []
        ] + [
            condition
            for block in blocks.get("blocks") or []
            for condition in block.get("conditions") or []
        ],
        "sections": {**sections, "block_rules": blocks},
        "filters_audit": sections["filters"],
        "signals_audit": sections["signals"],
        "entry_triggers_audit": sections["entry_triggers"],
        "block_rules_audit": blocks,
        "score_audit": deepcopy((profile_config or {}).get("scoring") or {}),
        "deterministic_gate": {
            "authorization_status": authorization_status,
            "contract_technical_decision": contract_decision,
        },
        "authorization_status": authorization_status,
        "legacy_decision": legacy_decision,
        "legacy_technical_decision": legacy_decision,
        "technical_decision": legacy_decision,
        "contract_technical_decision": contract_decision,
        "final_decision": legacy_decision,
        "decision_drift": legacy_decision != contract_decision,
        "runtime_validation_errors": runtime_validation_errors,
        "reason_codes": list(dict.fromkeys(
            lineage_reasons + validation_reasons + evaluation_reasons
            + ([authorization_status] if authorization_status != "ALLOW" else [])
            + (["DECISION_DRIFT"] if legacy_decision != contract_decision else [])
        )),
        "ml_advisory": {
            "ml_status": "NOT_APPLIED",
            "ml_reason_code": "NO_ELIGIBLE_MODEL_FOR_LANE",
            "ml_operational_effect": False,
        },
    }
    evaluation_hash = canonical_hash(body)
    body["evaluation_envelope_hash"] = evaluation_hash
    section_hashes = {}
    for section_name in ("filters", "signals", "entry_triggers", "block_rules"):
        body["sections"][section_name]["evaluation_envelope_hash"] = evaluation_hash
        section_hashes[section_name] = canonical_hash(body["sections"][section_name])
    body["hashes"] = {
        "evaluation_envelope_hash": evaluation_hash,
        "profile_config_hash": body["profile_lineage"]["profile_config_hash"],
        "section_hashes": section_hashes,
    }
    body["authorization_contract_hash"] = canonical_hash(body)
    return body


def attach_ml_advisory(contract: dict, advisory: dict) -> dict:
    updated = deepcopy(contract)
    updated["ml_advisory"] = deepcopy(advisory)
    updated["technical_decision"] = updated.get("legacy_technical_decision")
    updated["final_decision"] = updated.get("technical_decision")
    updated.pop("authorization_contract_hash", None)
    updated["authorization_contract_hash"] = canonical_hash(updated)
    return updated
