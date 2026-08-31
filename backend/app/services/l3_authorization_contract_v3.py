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

from .block_rule_compiler import compile_block_rule


CONTRACT_VERSION = "l3_authorization_contract_v3"
CONTRACT_MODE = "SHADOW"
CANONICALIZATION_VERSION = "feature_identity_v1"
PROVENANCE_RESOLVER_VERSION = "l3_v3_provenance_resolver_v1"
CONDITION_NORMALIZATION_POLICY_VERSION = "l3_condition_normalization_v2"
_TRANSIENT_PROFILE_KEYS = frozenset({
    "_execution_contract",
    "_block_rules_lineage",
    "_entry_triggers_lineage",
    "_global_entry_triggers",
    "_l3_gate_runtime_policy",
})

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
_FEATURE_ALIASES = {"orderbook_pressure": "bid_ask_imbalance"}


def contract_authorizes_shadow_capture(
    contract: Mapping[str, Any] | None,
    *,
    legacy_decision: Any,
) -> bool:
    """Return the authority that applies to a Shadow capture.

    ``mode=SHADOW`` is explicitly observational.  It must preserve the
    deterministic L3 decision while retaining an invalid v3 envelope as
    evidence and keeping the resulting row ineligible for training.  Only an
    ``ENFORCE`` contract may make v3 validity operational.
    """

    if not isinstance(contract, Mapping):
        return False
    if not contract.get("authorization_contract_hash"):
        return False
    legacy = str(legacy_decision or "").upper()
    technical = str(contract.get("technical_decision") or "").upper()
    final = str(contract.get("final_decision") or "").upper()
    if legacy != "ALLOW" or technical != legacy or final != legacy:
        return False
    mode = str(contract.get("mode") or "").upper()
    if mode == "SHADOW":
        return contract.get("operational_effect") is False
    if mode == "ENFORCE":
        return bool(
            contract.get("operational_effect") is True
            and contract.get("valid") is True
            and contract.get("authorization_status") == "ALLOW"
            and contract.get("contract_technical_decision") == "ALLOW"
        )
    return False


def _normalize_legacy_inline_block_conditions(
    profile_config: dict, *, legacy_range_enabled: bool = False
) -> dict:
    """Materialize BlockEngine defaults without changing persisted rules.

    The legacy BlockEngine treats an inline block with no explicit ``type`` as
    a threshold and applies ``operator='>'`` plus ``value=0``.  Contract v3
    previously validated the raw JSON instead and reported the absent operator
    as unsupported, even though gate v2 had evaluated the condition normally.
    """

    normalized = deepcopy(profile_config)
    blocks = ((normalized.get("block_rules") or {}).get("blocks") or [])
    for block in blocks:
        if not isinstance(block, dict) or block.get("conditions"):
            continue
        compilation = compile_block_rule(
            block, legacy_range_enabled=legacy_range_enabled
        )
        if compilation["normalization"] == "FLAT_MIN_MAX_TO_OUTSIDE_RANGE":
            block.clear()
            block.update(compilation["compiled"])
            block["_block_rule_compilation"] = {
                key: compilation[key]
                for key in (
                    "contract_version",
                    "normalization_version",
                    "normalization",
                    "operational_effect",
                )
            }
            continue
        block_type = str(block.get("type") or "threshold").lower()
        if block_type != "threshold":
            continue
        block.pop("conditions", None)
        block.setdefault("type", "threshold")
        block.setdefault("operator", ">")
        block.setdefault("value", 0)
    return normalized


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


def _persisted_profile_snapshot(profile_config: dict) -> dict:
    execution_contract = (profile_config or {}).get("_execution_contract") or {}
    projection = execution_contract.get("profile_projection")
    if isinstance(projection, Mapping):
        return deepcopy(dict(projection))
    return {
        key: deepcopy(value)
        for key, value in (profile_config or {}).items()
        if key not in _TRANSIENT_PROFILE_KEYS
    }


def normalize_symbol(symbol: str) -> str:
    return str(symbol or "").strip().upper().replace("-", "_").replace("/", "_")


def _canonical_indicator(value: Any) -> Any:
    return "alpha_score" if value == "score" else value


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
    if raw in {
        "live_order_flow", "live_trade_flow", "gate_io_trades_ws",
        "gate_trades", "gate_trades_ws_spot", "gate_trades_ws_futures",
        "gate_io_trades", "binance_trades",
    }:
        return "live_trade_flow"
    if raw in {
        "live_order_book", "gate_io_orderbook_ws", "gate_orderbook",
        "binance_orderbook",
    }:
        return "live_order_book"
    if raw in {
        "ohlcv", "gate_candles", "binance_candles", "candle_computed",
        "candle_fallback", "gate_io_candles",
    }:
        return "ohlcv"
    return raw or None


def _normalize_candle_policy(value: Any) -> Optional[str]:
    raw = str(value or "").strip().upper()
    aliases = {"CLOSED": "CLOSED_ONLY", "CLOSED_CANDLE": "CLOSED_ONLY"}
    return aliases.get(raw, raw or None)


def _source_for_db_candidate(candidate: dict) -> str:
    normalized = _normalize_source(candidate.get("source"))
    provider_source = _normalize_source(candidate.get("source_provider"))
    # Historical v3 envelopes sometimes serialized Gate trade/order-book
    # producers with the generic source="ohlcv".  The exact provider is the
    # stronger identity signal and lets read-only replay recover the producer
    # without any partial-name or flattened-key fallback.
    if provider_source in {"live_trade_flow", "live_order_book"}:
        return provider_source
    return normalized or provider_source or "unconfigured"


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
    indicator = str(raw.get("indicator") or "")
    alias_of = _FEATURE_ALIASES.get(indicator)
    return {
        "market_scope": deepcopy(raw.get("market_scope") or market_scope),
        "indicator": indicator,
        "alias_of": alias_of,
        "independent": alias_of is None,
        "alias_warning": "DUPLICATE_FEATURE_ALIAS" if alias_of else None,
        "actual": raw.get("actual"),
        "source": source,
        "source_provider": raw.get("source_provider") or raw.get("source"),
        "provider_policy_id": raw.get("provider_policy_id"),
        "timeframe": raw.get("timeframe"),
        "window_seconds": raw.get("window_seconds"),
        "snapshot": (
            raw.get("snapshot")
            if raw.get("snapshot") is not None
            else (True if source == "live_order_book" else None)
        ),
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


def _decision_context_candidates(
    asset: dict, *, market_scope: dict, evaluated_at: datetime
) -> list[dict]:
    """Expose only values frozen into the evaluated decision object."""

    candidates: list[dict] = []
    score_context = asset.get("_score_components") or {}
    component_fields = (
        score_context.get("component_fields") or {}
        if isinstance(score_context, Mapping)
        else {}
    )
    values: dict[str, tuple[Any, Any, str]] = {}
    for field in (
        "price", "change_24h", "market_cap", "volume_24h", "spread_pct",
        "orderbook_depth_usdt",
    ):
        if asset.get(field) is not None:
            values[field] = (
                asset.get(field), asset.get("_price_source_at"), "market_metadata"
            )
    score_value = asset.get("_score", asset.get("alpha_score"))
    if score_value is not None:
        values["alpha_score"] = (score_value, evaluated_at, "robust_score")
    for field, value in component_fields.items():
        if value is not None:
            values[str(field)] = (value, evaluated_at, "robust_score")
    for indicator, (actual, source_at, provider) in values.items():
        candidates.append(_registry_candidate({
            "market_scope": market_scope,
            "indicator": indicator,
            "actual": actual,
            "source": "decision_context",
            "source_provider": provider,
            "provider_policy_id": None,
            "source_timestamp": source_at,
            "computed_at": evaluated_at,
            "available_at": evaluated_at,
            "age_seconds": None,
        }, market_scope=market_scope, evaluated_at=evaluated_at))
        candidates[-1]["source"] = "decision_context"
    return candidates


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
    registry.extend(_decision_context_candidates(
        asset, market_scope=market_scope, evaluated_at=evaluated_at
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


def _condition_trace_id(condition: dict, index: int) -> str:
    return str(
        condition.get("id")
        or condition.get("condition_id")
        or condition.get("field")
        or condition.get("indicator")
        or condition.get("left")
        or f"condition_{index + 1}"
    )


def _gate_trace_index(
    gate_evaluation: Optional[dict],
) -> dict[tuple[str, Optional[str], str], dict]:
    index: dict[tuple[str, Optional[str], str], dict] = {}

    def visit(value: Any, section: str) -> None:
        if isinstance(value, Mapping):
            condition_id = value.get("condition_id")
            if condition_id is not None and "actual" in value:
                index.setdefault((section, None, str(condition_id)), dict(value))
            for child in value.values():
                visit(child, section)
        elif isinstance(value, (list, tuple)):
            for child in value:
                visit(child, section)

    for section in ("filters", "signals", "entry_triggers", "global_entry_triggers"):
        visit((gate_evaluation or {}).get(section), section)
    block_rules = (gate_evaluation or {}).get("block_rules") or {}
    for rule in block_rules.get("evaluated") or block_rules.get("rules") or []:
        if not isinstance(rule, Mapping):
            continue
        rule_id = str(rule.get("id") or rule.get("name") or "") or None
        for condition_index, condition in enumerate(rule.get("conditions") or []):
            if not isinstance(condition, Mapping) or "actual" not in condition:
                continue
            condition_id = str(
                condition.get("condition_id")
                or condition.get("id")
                or condition.get("indicator")
                or condition.get("left")
                or f"condition_{condition_index + 1}"
            )
            index.setdefault(
                ("block_rules", rule_id, condition_id), dict(condition)
            )
    return index


def _same_observed_value(left: Any, right: Any) -> bool:
    return _canonicalize(left) == _canonicalize(right)


def _reference_resolution(
    *,
    indicator: Any,
    condition: dict,
    observed_value: Any,
    registry: list[dict],
    default_timeframe: Optional[str],
    source_policies: Mapping[str, Any],
) -> tuple[Optional[dict], list[str]]:
    canonical_indicator = _canonical_indicator(indicator)
    candidates = [
        candidate for candidate in registry
        if candidate.get("indicator") == canonical_indicator
    ]
    if observed_value is not None:
        candidates = [
            candidate for candidate in candidates
            if _same_observed_value(candidate.get("actual"), observed_value)
        ]
    configured_source = _normalize_source(condition.get("source"))
    if configured_source:
        candidates = [
            candidate for candidate in candidates
            if candidate.get("source") == configured_source
        ]
    configured_provider = condition.get("source_provider")
    if configured_provider:
        candidates = [
            candidate for candidate in candidates
            if candidate.get("source_provider") == configured_provider
        ]
    configured_timeframe = condition.get("timeframe")
    if configured_timeframe:
        candidates = [
            candidate for candidate in candidates
            if candidate.get("source") != "ohlcv"
            or candidate.get("timeframe") == configured_timeframe
        ]
    configured_window = condition.get("window_seconds")
    if configured_window is not None:
        candidates = [
            candidate for candidate in candidates
            if candidate.get("window_seconds") == configured_window
        ]
    configured_period = condition.get("period")
    if configured_period is not None:
        candidates = [
            candidate for candidate in candidates
            if candidate.get("period") == configured_period
        ]
    configured_parameters = _condition_parameters(condition)
    if configured_parameters:
        candidates = [
            candidate for candidate in candidates
            if _candidate_parameters(candidate) == configured_parameters
        ]
    if not candidates:
        return None, ["FEATURE_IDENTITY_NOT_AVAILABLE"]
    if len(candidates) != 1:
        return None, ["FEATURE_PROVENANCE_AMBIGUOUS"]

    candidate = candidates[0]
    source = str(candidate.get("source") or "")
    policy = source_policies.get(source)
    if not isinstance(policy, Mapping):
        return None, [f"PROVENANCE_POLICY_UNCONFIGURED:{source or 'unknown'}"]
    allowed_providers = [str(item) for item in policy.get("allowed_source_providers") or []]
    source_provider = candidate.get("source_provider")
    if not source_provider:
        return None, ["SOURCE_PROVIDER_NOT_AVAILABLE"]
    if not allowed_providers:
        return None, [f"PROVENANCE_POLICY_UNCONFIGURED:{source}:allowed_source_providers"]
    if str(source_provider) not in allowed_providers:
        return None, ["SOURCE_PROVIDER_NOT_ALLOWED"]
    provider_policy_id = condition.get("provider_policy_id") or policy.get(
        "provider_policy_id"
    )
    max_age_seconds = condition.get("max_age_seconds")
    if max_age_seconds is None:
        max_age_seconds = policy.get("max_age_seconds")
    if not provider_policy_id:
        return None, [f"PROVENANCE_POLICY_UNCONFIGURED:{source}:provider_policy_id"]
    if max_age_seconds is None:
        return None, [f"PROVENANCE_POLICY_UNCONFIGURED:{source}:max_age_seconds"]

    resolved = {
        "indicator": canonical_indicator,
        "source": source,
        "source_provider": source_provider,
        "provider_policy_id": provider_policy_id,
        "max_age_seconds": max_age_seconds,
        "timeframe": None,
        "window_seconds": None,
        "snapshot": None,
        "period": candidate.get("period"),
        "parameters": _candidate_parameters(candidate),
        "candle_policy": None,
        "alias_of": candidate.get("alias_of"),
        "independent": candidate.get("independent", True),
        "alias_warning": candidate.get("alias_warning"),
    }
    if source == "ohlcv":
        expected_timeframe = (
            condition.get("timeframe")
            or policy.get("timeframe")
            or default_timeframe
        )
        if not expected_timeframe:
            return None, ["TIMEFRAME_REQUIRED"]
        if candidate.get("timeframe") != expected_timeframe:
            return None, ["TIMEFRAME_MISMATCH"]
        candle_policy = (
            _normalize_candle_policy(condition.get("candle_policy"))
            or _normalize_candle_policy(policy.get("candle_policy"))
        )
        if candle_policy not in _SUPPORTED_CANDLE_POLICIES:
            return None, [f"PROVENANCE_POLICY_UNCONFIGURED:{source}:candle_policy"]
        resolved["timeframe"] = expected_timeframe
        resolved["candle_policy"] = candle_policy
    elif source == "live_trade_flow":
        expected_window = condition.get("window_seconds") or policy.get(
            "window_seconds"
        )
        if expected_window is None:
            return None, [f"PROVENANCE_POLICY_UNCONFIGURED:{source}:window_seconds"]
        if candidate.get("window_seconds") != expected_window:
            return None, ["WINDOW_MISMATCH"]
        resolved["window_seconds"] = expected_window
    elif source == "live_order_book":
        expected_snapshot = condition.get("snapshot")
        if expected_snapshot is None:
            expected_snapshot = policy.get("snapshot")
        expected_window = condition.get("window_seconds") or policy.get(
            "window_seconds"
        )
        if expected_snapshot is not True and expected_window is None:
            return None, [f"PROVENANCE_POLICY_UNCONFIGURED:{source}:snapshot_or_window"]
        if expected_snapshot is not None and candidate.get("snapshot") != expected_snapshot:
            return None, ["SNAPSHOT_MISMATCH"]
        if expected_window is not None and candidate.get("window_seconds") != expected_window:
            return None, ["WINDOW_MISMATCH"]
        resolved["snapshot"] = expected_snapshot
        resolved["window_seconds"] = expected_window

    resolved_candidate = deepcopy(candidate)
    resolved_candidate.update({
        "provider_policy_id": provider_policy_id,
        "timeframe": resolved["timeframe"],
        "window_seconds": resolved["window_seconds"],
        "snapshot": resolved["snapshot"],
        "period": resolved["period"],
        "parameters": deepcopy(resolved["parameters"]),
        "candle_policy": resolved["candle_policy"],
    })
    resolved["_resolved_feature"] = resolved_candidate
    return resolved, []


def _materialize_condition(
    condition: dict,
    *,
    index: int,
    section: str,
    trace_index: Mapping[tuple[str, Optional[str], str], dict],
    registry: list[dict],
    default_timeframe: Optional[str],
    source_policies: Mapping[str, Any],
    rule_id: Any = None,
) -> tuple[dict, list[str]]:
    materialized = deepcopy(condition)
    trace = trace_index.get((
        section,
        str(rule_id) if rule_id is not None else None,
        _condition_trace_id(condition, index),
    )) or {}
    errors: list[str] = []
    if condition.get("type") == "comparison":
        operands: dict[str, dict] = {}
        references = [("left", condition.get("left"), trace.get("actual"))]
        if condition.get("operator") != "between":
            references.append((
                "right",
                condition.get("right"),
                trace.get("target", trace.get("expected")),
            ))
        for side, indicator, observed in references:
            resolved, reference_errors = _reference_resolution(
                indicator=indicator,
                condition=condition,
                observed_value=observed,
                registry=registry,
                default_timeframe=default_timeframe,
                source_policies=source_policies,
            )
            if reference_errors:
                errors.extend(f"{side.upper()}:{code}" for code in reference_errors)
            elif resolved is not None:
                operands[side] = resolved
        materialized["resolved_operands"] = operands
    else:
        indicator = condition.get("indicator") or condition.get("field")
        resolved, errors = _reference_resolution(
            indicator=indicator,
            condition=condition,
            observed_value=trace.get("actual"),
            registry=registry,
            default_timeframe=default_timeframe,
            source_policies=source_policies,
        )
        if resolved is not None:
            materialized.update(resolved)
    if errors:
        materialized["_resolution_errors"] = list(dict.fromkeys(errors))
    return materialized, errors


def materialize_runtime_profile_contract(
    *,
    profile_config: dict,
    profile_id: Any,
    runtime_policy: Optional[dict],
    gate_evaluation: Optional[dict],
    registry: list[dict],
) -> tuple[dict, dict]:
    """Resolve a separate executable snapshot without mutating the profile."""

    range_policy = deepcopy(
        ((runtime_policy or {}).get("l3_global_block_range_compiler") or {})
    )
    range_allowlisted = str(profile_id) in {
        str(item) for item in range_policy.get("profile_allowlist") or []
    }
    range_enabled = bool(range_policy.get("enabled") and range_allowlisted)
    normalized_profile_config = _normalize_legacy_inline_block_conditions(
        profile_config, legacy_range_enabled=range_enabled
    )
    resolver = deepcopy(
        ((runtime_policy or {}).get("l3_v3_provenance_resolver") or {})
    )
    policy_hash = canonical_hash(resolver)
    report = {
        "contract_version": PROVENANCE_RESOLVER_VERSION,
        "policy_version": resolver.get("policy_version"),
        "policy_hash": policy_hash,
        "enabled": bool(resolver.get("enabled")),
        "profile_allowlisted": str(profile_id) in {
            str(item) for item in resolver.get("profile_allowlist") or []
        },
        "status": "DISABLED",
        "resolved_condition_count": 0,
        "errors": [],
    }
    if not report["enabled"]:
        return normalized_profile_config, report
    if not report["profile_allowlisted"]:
        report["status"] = "PROFILE_NOT_ALLOWLISTED"
        return normalized_profile_config, report
    if resolver.get("policy_version") != PROVENANCE_RESOLVER_VERSION:
        report["status"] = "POLICY_INVALID"
        report["errors"] = ["PROVENANCE_POLICY_VERSION_INVALID"]
        return normalized_profile_config, report

    materialized = normalized_profile_config
    trace_index = _gate_trace_index(gate_evaluation)
    source_policies = resolver.get("source_policies") or {}
    default_timeframe = profile_config.get("default_timeframe")
    for config_key, section in (
        ("filters", "filters"),
        ("signals", "signals"),
        ("entry_triggers", "entry_triggers"),
        ("_global_entry_triggers", "global_entry_triggers"),
    ):
        section_config = materialized.get(config_key)
        if not isinstance(section_config, dict):
            continue
        conditions = section_config.get("conditions") or []
        resolved_conditions = []
        for index, condition in enumerate(conditions):
            resolved, errors = _materialize_condition(
                condition,
                index=index,
                section=section,
                trace_index=trace_index,
                registry=registry,
                default_timeframe=default_timeframe,
                source_policies=source_policies,
            )
            resolved_conditions.append(resolved)
            report["resolved_condition_count"] += int(not errors)
            report["errors"].extend(
                {"path": f"{section}.conditions[{index}]", "code": code}
                for code in errors
            )
        section_config["conditions"] = resolved_conditions
    block_section = materialized.get("block_rules")
    if isinstance(block_section, dict):
        for block_index, block in enumerate(block_section.get("blocks") or []):
            if not isinstance(block, dict):
                continue
            conditions = block.get("conditions")
            inline = conditions is None
            source_conditions = [block] if inline else (conditions or [])
            resolved_conditions = []
            for index, condition in enumerate(source_conditions):
                resolved, errors = _materialize_condition(
                    condition,
                    index=index,
                    section="block_rules",
                    trace_index=trace_index,
                    registry=registry,
                    default_timeframe=default_timeframe,
                    source_policies=source_policies,
                    rule_id=(block.get("id") or block.get("name")),
                )
                resolved_conditions.append(resolved)
                report["resolved_condition_count"] += int(not errors)
                report["errors"].extend(
                    {
                        "path": f"block_rules.blocks[{block_index}].conditions[{index}]",
                        "code": code,
                    }
                    for code in errors
                )
            if inline:
                block.update(resolved_conditions[0])
            else:
                block["conditions"] = resolved_conditions
    report["errors"] = list({
        (item["path"], item["code"]): item for item in report["errors"]
    }.values())
    report["status"] = "RESOLVED" if not report["errors"] else "CONTRACT_REJECT"
    return materialized, report


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
    del default_timeframe
    required = _required(condition, default=required_default)
    operator = condition.get("operator")
    errors: list[str] = []
    if operator not in _SUPPORTED_OPERATORS:
        errors.append("OPERATOR_UNSUPPORTED")
    if operator == "between" and not all(key in condition for key in ("min", "max")):
        errors.append("BETWEEN_BOUNDS_REQUIRED")
    if (
        condition.get("type") != "comparison"
        and operator not in {"between", "is_true", "is_false"}
        and operator in _SUPPORTED_OPERATORS
    ):
        if "value" not in condition:
            errors.append("OPERATOR_VALUE_REQUIRED")

    if condition.get("type") == "comparison":
        if not condition.get("left"):
            errors.append("LEFT_OPERAND_REQUIRED")
        if operator != "between" and not condition.get("right"):
            errors.append("RIGHT_OPERAND_REQUIRED")
        operands = condition.get("resolved_operands") or {}
        sides = ["left"] + (["right"] if operator != "between" else [])
        for side in sides:
            reference = operands.get(side)
            if not isinstance(reference, dict):
                errors.append(f"{side.upper()}:SOURCE_REQUIRED")
                continue
            errors.extend(
                f"{side.upper()}:{code}"
                for code in _validate_feature_reference(reference, required=required)
            )
        return list(dict.fromkeys(errors))

    indicator = condition.get("indicator") or condition.get("field")
    if not indicator:
        return ["INDICATOR_REQUIRED"]
    errors.extend(_validate_feature_reference(condition, required=required))
    if indicator == "breakout_distance_pct" and not condition.get("reference_window"):
        errors.append("REFERENCE_WINDOW_REQUIRED")
    return list(dict.fromkeys(errors))


def _validate_feature_reference(reference: dict, *, required: bool) -> list[str]:
    source = _normalize_source(reference.get("source"))
    errors: list[str] = []
    if not source:
        return ["SOURCE_REQUIRED"]
    if source not in {"ohlcv", "live_trade_flow", "live_order_book", "decision_context"}:
        return ["SOURCE_UNSUPPORTED"]
    if not reference.get("source_provider"):
        errors.append("SOURCE_PROVIDER_REQUIRED")
    if not reference.get("provider_policy_id"):
        errors.append("PROVIDER_POLICY_REQUIRED")
    if required and reference.get("max_age_seconds") is None:
        errors.append("FRESHNESS_POLICY_REQUIRED")
    elif reference.get("max_age_seconds") is not None:
        try:
            if float(reference.get("max_age_seconds")) <= 0:
                errors.append("MAX_AGE_SECONDS_INVALID")
        except (TypeError, ValueError):
            errors.append("MAX_AGE_SECONDS_INVALID")
    if source == "live_trade_flow":
        if reference.get("window_seconds") is None:
            errors.append("WINDOW_SECONDS_REQUIRED")
        if reference.get("timeframe") is not None:
            errors.append("TIMEFRAME_NOT_ALLOWED_FOR_LIVE_TRADE_FLOW")
        if reference.get("snapshot") is not None:
            errors.append("SNAPSHOT_NOT_ALLOWED_FOR_LIVE_TRADE_FLOW")
    elif source == "live_order_book":
        if reference.get("timeframe") is not None:
            errors.append("TIMEFRAME_NOT_ALLOWED_FOR_LIVE_ORDER_BOOK")
        if reference.get("snapshot") is not True and reference.get("window_seconds") is None:
            errors.append("SNAPSHOT_OR_WINDOW_REQUIRED")
    elif source == "ohlcv":
        if not reference.get("timeframe"):
            errors.append("TIMEFRAME_REQUIRED")
        if reference.get("window_seconds") is not None:
            errors.append("WINDOW_SECONDS_NOT_ALLOWED_FOR_OHLCV")
        candle_policy = _normalize_candle_policy(reference.get("candle_policy"))
        if candle_policy not in _SUPPORTED_CANDLE_POLICIES:
            errors.append("CANDLE_POLICY_REQUIRED_OR_UNSUPPORTED")
    return errors


def validate_profile_contract(profile_config: dict) -> list[dict]:
    errors: list[dict] = []
    sections: list[tuple[str, Iterable[dict], bool]] = []
    for section in ("filters", "signals", "entry_triggers"):
        conditions = ((profile_config or {}).get(section) or {}).get("conditions") or []
        sections.append((section, conditions, False))
    global_entry = (profile_config or {}).get("_global_entry_triggers")
    if isinstance(global_entry, dict):
        sections.append((
            "global_entry_triggers",
            global_entry.get("conditions") or [],
            False,
        ))
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
        if policy == "CLOSED_ONLY" and candidate.get("candle_closed") is None:
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
    if condition.get("type") == "comparison":
        operands = condition.get("resolved_operands") or {}
        ordered_sides = ["left"] + (
            ["right"] if condition.get("operator") != "between" else []
        )
        identities = {
            side: _expected_identity(operands.get(side) or {}, market_scope)
            for side in ordered_sides
        }
        policy_ids = {
            side: (operands.get(side) or {}).get("provider_policy_id")
            for side in ordered_sides
        }
        return {
            "section": section,
            "rule_id": rule_id,
            "condition_id": condition.get("id") or condition.get("condition_id"),
            "rule_logic": rule_logic,
            "type": "comparison",
            "indicator": None,
            "left": condition.get("left"),
            "right": condition.get("right"),
            "required": _required(condition, default=required_default),
            "operator": condition.get("operator"),
            "expected": {
                key: condition.get(key) for key in ("min", "max") if key in condition
            },
            "feature_identities": identities,
            "provider_policy_ids": policy_ids,
            "condition_contract_hash": canonical_hash({
                "ordered_operands": [
                    {
                        "side": side,
                        "feature_identity": identities[side],
                        "provider_policy_id": policy_ids[side],
                    }
                    for side in ordered_sides
                ],
                "operator": condition.get("operator"),
                "bounds": {
                    key: condition.get(key)
                    for key in ("min", "max") if key in condition
                },
            }),
        }
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


def _evaluate_reference(
    reference: dict,
    registry: list[dict],
    market_scope: dict,
    *,
    required: bool,
) -> dict:
    expected = _expected_identity(reference, market_scope)
    provider_policy_id = reference.get("provider_policy_id")
    frozen_candidate = reference.get("_resolved_feature")
    if isinstance(frozen_candidate, Mapping):
        candidates = [deepcopy(dict(frozen_candidate))]
        if not _matches_identity(candidates[0], expected, provider_policy_id):
            candidates = []
    else:
        candidates = [
            candidate for candidate in registry
            if _matches_identity(candidate, expected, provider_policy_id)
        ]
    if not candidates:
        return {
            "status": "CONTRACT_REJECT" if required else "SKIPPED_OPTIONAL",
            "reason_codes": _identity_mismatch_reasons(
                expected, provider_policy_id, registry
            ),
            "actual": None,
            "feature_identity": expected,
            "resolved_feature": None,
            "resolved_feature_hash": None,
        }
    if len(candidates) != 1:
        return {
            "status": "CONTRACT_REJECT" if required else "SKIPPED_OPTIONAL",
            "reason_codes": ["FEATURE_PROVENANCE_AMBIGUOUS"],
            "actual": None,
            "feature_identity": expected,
            "resolved_feature": None,
            "resolved_feature_hash": None,
        }
    candidate = candidates[0]
    resolved_hash = _identity_hash(_feature_identity(candidate), provider_policy_id)
    expected_hash = _identity_hash(expected, provider_policy_id)
    if resolved_hash != expected_hash:
        return {
            "status": "CONTRACT_REJECT",
            "reason_codes": ["FEATURE_IDENTITY_HASH_MISMATCH"],
            "actual": candidate.get("actual"),
            "feature_identity": expected,
            "resolved_feature": candidate,
            "resolved_feature_hash": resolved_hash,
        }
    invalid = _freshness_reasons(reference, candidate)
    return {
        "status": (
            "CONTRACT_REJECT" if required else "SKIPPED_OPTIONAL"
        ) if invalid else "RESOLVED",
        "reason_codes": invalid,
        "actual": candidate.get("actual"),
        "feature_identity": expected,
        "resolved_feature": candidate,
        "resolved_feature_hash": resolved_hash,
    }


def _evaluate_comparison_condition(
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
        condition,
        section=section,
        rule_id=rule_id,
        rule_logic=rule_logic,
        market_scope=market_scope,
        required_default=required_default,
    )
    resolution_errors = condition.get("_resolution_errors") or []
    validation_errors = validate_condition_contract(
        condition, required_default=required_default
    )
    if resolution_errors or validation_errors:
        return {
            **base,
            "status": "CONTRACT_REJECT",
            "result": "CONTRACT_REJECT",
            "reason_codes": list(dict.fromkeys(
                [*resolution_errors, *validation_errors]
            )),
            "actual": None,
            "target": None,
            "resolved_operands": {},
            "resolved_feature_hash": None,
        }
    operands = condition.get("resolved_operands") or {}
    sides = ["left"] + (["right"] if condition.get("operator") != "between" else [])
    evaluated = {
        side: _evaluate_reference(
            operands[side], registry, market_scope, required=base["required"]
        )
        for side in sides
    }
    invalid = [
        result for result in evaluated.values()
        if result["status"] != "RESOLVED"
    ]
    if invalid:
        return {
            **base,
            "status": "CONTRACT_REJECT" if base["required"] else "SKIPPED_OPTIONAL",
            "result": "CONTRACT_REJECT" if base["required"] else "SKIPPED_OPTIONAL",
            "reason_codes": list(dict.fromkeys(
                code for result in invalid for code in result["reason_codes"]
            )),
            "actual": evaluated.get("left", {}).get("actual"),
            "target": evaluated.get("right", {}).get("actual"),
            "resolved_operands": evaluated,
            "resolved_feature_hash": None,
        }
    actual = evaluated["left"]["actual"]
    target = evaluated.get("right", {}).get("actual")
    operator_condition = deepcopy(condition)
    if condition.get("operator") != "between":
        operator_condition["value"] = target
    try:
        passed = _apply_operator(operator_condition, actual)
    except Exception as exc:
        return {
            **base,
            "status": "CONTRACT_REJECT",
            "result": "CONTRACT_REJECT",
            "reason_codes": [f"EVALUATION_ERROR:{type(exc).__name__}"],
            "actual": actual,
            "target": target,
            "resolved_operands": evaluated,
            "resolved_feature_hash": None,
        }
    resolved_hash = canonical_hash([
        {"side": side, "hash": evaluated[side]["resolved_feature_hash"]}
        for side in sides
    ])
    return {
        **base,
        "status": "PASS" if passed else "FAIL",
        "result": "PASS" if passed else "FAIL",
        "reason_codes": [],
        "actual": actual,
        "target": target,
        "resolved_operands": evaluated,
        "resolved_feature_hash": resolved_hash,
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
    if condition.get("type") == "comparison":
        return _evaluate_comparison_condition(
            condition,
            registry,
            market_scope,
            section=section,
            rule_id=rule_id,
            rule_logic=rule_logic,
            required_default=required_default,
        )
    base = _condition_base(
        condition, section=section, rule_id=rule_id, rule_logic=rule_logic,
        market_scope=market_scope, required_default=required_default,
    )
    errors = list(condition.get("_resolution_errors") or []) + validate_condition_contract(
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
    frozen_candidate = condition.get("_resolved_feature")
    if isinstance(frozen_candidate, Mapping):
        candidates = [deepcopy(dict(frozen_candidate))]
        if not _matches_identity(candidates[0], expected, provider_policy_id):
            candidates = []
    else:
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
    if len(candidates) != 1:
        status = "CONTRACT_REJECT" if base["required"] else "SKIPPED_OPTIONAL"
        return {
            **base, "status": status, "result": status,
            "reason_codes": ["FEATURE_PROVENANCE_AMBIGUOUS"],
            "actual": None, "resolved_feature": None,
            "resolved_feature_hash": None,
        }
    candidate = candidates[0]
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
    gate_evaluation: Optional[dict] = None,
    runtime_policy: Optional[dict] = None,
    feature_registry: Optional[list[dict]] = None,
) -> dict:
    market_scope = {
        "exchange": "gate_io",
        "market_type": str(market_type or "spot").lower(),
        "normalized_symbol": normalize_symbol(asset.get("symbol")),
    }
    scoped_asset = dict(asset)
    scoped_asset["_l3_market_scope"] = market_scope
    registry = (
        [
            _registry_candidate(
                deepcopy(dict(candidate)),
                market_scope=market_scope,
                evaluated_at=evaluated_at,
            )
            for candidate in feature_registry
            if isinstance(candidate, Mapping)
        ]
        if feature_registry is not None
        else build_feature_registry(scoped_asset, evaluated_at=evaluated_at)
    )
    effective_runtime_policy = runtime_policy or (
        (profile_config or {}).get("_l3_gate_runtime_policy") or {}
    )
    profile_execution_contract = deepcopy(
        (profile_config or {}).get("_execution_contract") or {}
    )
    persisted_profile_snapshot = _persisted_profile_snapshot(profile_config or {})
    persisted_profile_hash = canonical_hash(persisted_profile_snapshot)
    effective_profile_config, resolution_report = materialize_runtime_profile_contract(
        profile_config=profile_config or {},
        profile_id=profile_id,
        runtime_policy=effective_runtime_policy,
        gate_evaluation=gate_evaluation,
        registry=registry,
    )
    collapsed_alias_rules: list[dict[str, Any]] = []
    for block in (((effective_profile_config or {}).get("block_rules") or {}).get("blocks") or []):
        if not isinstance(block, Mapping) or str(block.get("logic") or "AND").upper() != "AND":
            continue
        groups: dict[str, list[str]] = {}
        for condition in block.get("conditions") or []:
            if not isinstance(condition, Mapping):
                continue
            indicator = str(condition.get("indicator") or condition.get("field") or "")
            canonical = _FEATURE_ALIASES.get(indicator, indicator)
            groups.setdefault(canonical, []).append(indicator)
        duplicates = {
            canonical: values for canonical, values in groups.items() if len(values) > 1
        }
        if duplicates:
            collapsed_alias_rules.append(
                {
                    "profile_id": str(profile_id) if profile_id else None,
                    "rule_id": block.get("id"),
                    "rule_name": block.get("name"),
                    "collapsed_features": duplicates,
                    "reason_code": "DUPLICATE_FEATURE_ALIAS",
                }
            )
    sections: dict[str, dict] = {}
    for name in ("filters", "signals", "entry_triggers"):
        config = (effective_profile_config or {}).get(name)
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
    global_entry_config = (effective_profile_config or {}).get(
        "_global_entry_triggers"
    )
    if isinstance(global_entry_config, dict) and (
        global_entry_config.get("conditions") or []
    ):
        sections["global_entry_triggers"] = _evaluate_conditions(
            global_entry_config.get("conditions") or [],
            global_entry_config.get("logic", "AND"),
            registry,
            market_scope,
            section="global_entry_triggers",
        )
    else:
        sections["global_entry_triggers"] = {
            "logic": str((global_entry_config or {}).get("logic") or "AND").upper(),
            "passed": True,
            "contract_reject": False,
            "reason_codes": ["NO_GLOBAL_ENTRY_TRIGGERS"],
            "conditions": [],
        }
    blocks = _evaluate_blocks(effective_profile_config or {}, registry, market_scope)
    runtime_validation_errors = validate_profile_contract(
        effective_profile_config or {}
    )
    lineage_reasons = []
    if not profile_id:
        lineage_reasons.append("PROFILE_ID_MISSING")
    if not profile_name:
        lineage_reasons.append("PROFILE_NAME_MISSING")
    if profile_version is None:
        lineage_reasons.append("PROFILE_VERSION_MISSING")
    if not profile_config:
        lineage_reasons.append("RULES_SNAPSHOT_MISSING")
    if profile_execution_contract:
        if profile_execution_contract.get("contract_valid") is not True:
            lineage_reasons.append("PROFILE_EXECUTION_CONTRACT_INVALID")
        if str(profile_execution_contract.get("profile_id") or "") != str(
            profile_id or ""
        ):
            lineage_reasons.append("PROFILE_EXECUTION_ID_MISMATCH")
        if not profile_execution_contract.get("profile_version_id"):
            lineage_reasons.append("PROFILE_EXECUTION_VERSION_MISSING")
        projected_hash = profile_execution_contract.get("profile_projection_hash")
        if projected_hash and projected_hash != persisted_profile_hash:
            lineage_reasons.append("PROFILE_EXECUTION_HASH_MISMATCH")
    evaluation_reasons = _contract_reasons(sections, blocks)
    resolution_reasons = [
        str(item.get("code")) for item in resolution_report.get("errors") or []
    ]
    validation_reasons = [
        f"PROFILE_VALIDATION:{item['code']}" for item in runtime_validation_errors
    ]
    contract_reject = bool(
        lineage_reasons or runtime_validation_errors or resolution_reasons
        or blocks["contract_reject"]
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
        "condition_normalization_policy_version": (
            CONDITION_NORMALIZATION_POLICY_VERSION
        ),
        "mode": CONTRACT_MODE,
        "valid": not contract_reject,
        "operational_effect": False,
        "market_scope": market_scope,
        "provenance_resolution": {
            **resolution_report,
            "resolved_profile_config_hash": canonical_hash(
                effective_profile_config or {}
            ),
        },
        "profile_execution_contract": profile_execution_contract or None,
        "profile_lineage": {
            "profile_id": str(profile_id) if profile_id else None,
            "profile_name": profile_name,
            "profile_version": _iso(profile_version),
            "profile_version_id": profile_execution_contract.get(
                "profile_version_id"
            ),
            "profile_config_hash": persisted_profile_hash,
            "rules_snapshot": persisted_profile_snapshot,
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
            "profile_version_id": profile_execution_contract.get(
                "profile_version_id"
            ),
            "profile_config_hash": persisted_profile_hash,
            "rules_snapshot": persisted_profile_snapshot,
        },
        "evaluated_at": _iso(evaluated_at),
        "feature_registry": registry,
        "feature_alias_audit": {
            "contract_version": "feature_alias_audit_v1",
            "aliases": deepcopy(_FEATURE_ALIASES),
            "collapsed_and_rules": collapsed_alias_rules,
            "operational_effect": False,
        },
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
            lineage_reasons + validation_reasons + resolution_reasons
            + evaluation_reasons
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
    for section_name in (
        "filters", "signals", "entry_triggers", "global_entry_triggers",
        "block_rules",
    ):
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
