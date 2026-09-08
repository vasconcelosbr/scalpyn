"""Complete, fail-closed Shadow Portfolio dataset capture and sharding."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
import math
from typing import Any, Iterable
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai_orchestration.hashing import canonical_hash, canonical_json
from ..models.shadow_trade import ShadowTrade
from ..models.shadow_trade_analysis import ShadowTradeReportItem, ShadowTradeReportRun
from ..models.systemic_ai import AIAnalysisShardRecord, AIDatasetSnapshotItemRecord


CONTRACT_VERSION = "shadow-portfolio-full-canonical-v2"
PROVIDER_PROJECTION_CONTRACT_VERSION = "shadow-provider-analysis-projection-v2"
SUPPORTED_CONTRACT_VERSIONS = frozenset({
    "shadow-portfolio-full-canonical-v1",
    CONTRACT_VERSION,
})
BREAKOUT_WINDOWS = ("5m", "15m", "30m", "1h")
PRICE_POSITION_INDICATORS = (
    "vwap_distance_pct",
    "bb_upper_distance_pct",
    "bb_middle_distance_pct",
    "bb_lower_distance_pct",
    "recent_high_5m_distance_pct",
    "recent_high_15m_distance_pct",
    "recent_high_30m_distance_pct",
    "recent_high_1h_distance_pct",
    "recent_low_15m_distance_pct",
    "price_change_1m_pct",
    "price_change_5m_pct",
    "price_change_15m_pct",
    "ema5_distance_pct",
    "ema9_distance_pct",
    "ema21_distance_pct",
    "ema50_distance_pct",
    "ema200_distance_pct",
)
SNAPSHOT_COLUMNS = {
    "config_snapshot": "configuration",
    "features_snapshot": "entry_features",
    "features_snapshot_exit": "exit_features",
    "exit_metrics_json": "exit_metrics",
    "rules_snapshot": "rules",
    "entry_risk_features_json": "entry_risk",
    "orchestrator_payload": "orchestrator",
    "reason_codes": "reason_codes",
    "feature_source_times": "feature_source_times",
}
REQUIRED_TRADE_FIELDS = (
    "id", "user_id", "symbol", "entry_price", "entry_timestamp", "event_id", "snapshot_id",
    "profile_id", "profile_version_id", "score_engine_version_id", "exchange", "timeframe",
    "feature_schema_version", "feature_extractor_version", "capture_contract_version",
    "label_contract_version", "barrier_contract_version",
    "features_captured_at", "feature_hash", "profile_config_hash", "score_engine_config_hash",
    "lineage_status", "watchlist_id", "watchlist_name", "watchlist_level",
    "lineage_confidence", "lineage_source", "lineage_resolved_at",
    "entry_risk_capture_status",
)
REQUIRED_SNAPSHOTS = ("configuration", "entry_features", "rules", "entry_risk")
REQUIRED_COMPLETED_TRADE_FIELDS = (
    "exit_price", "exit_timestamp", "outcome", "label_resolved_at",
)
OPTIONAL_COMPLETED_SNAPSHOTS = ("exit_features", "exit_metrics")
PROVIDER_TRADE_FIELDS = frozenset({
    "id", "symbol", "strategy", "direction", "amount_usdt", "entry_price",
    "entry_timestamp", "tp_price", "sl_price", "tp_pct", "sl_pct",
    "timeout_candles", "exit_price", "exit_timestamp", "outcome", "pnl_pct",
    "pnl_usdt", "holding_seconds", "status", "source", "rejected_by_layer",
    "rejected_by_rule",
    "features_coverage", "oldest_indicator_age_s", "market_data_confidence",
    "eligible_for_training", "btc_change_1h_pct",
    "n_concurrent_signals", "mae_pct", "mfe_pct", "closure_path", "final_return_pct",
    "net_return_pct", "fee_roundtrip_pct_applied", "tp_pct_applied", "sl_pct_applied",
    "atr_pct_at_entry", "elapsed_minutes", "profile_name", "strategy_type",
    "profile_status_at_entry", "final_priority_score", "ml_probability",
    "threshold_used", "score_status", "gate_action", "watchlist_name",
    "watchlist_level",
})
PROVIDER_CONFIG_SCALAR_FIELDS = frozenset({
    "amount_usdt", "tp_pct", "sl_pct", "timeout_candles", "barrier_mode",
    "barrier_effective_ratio", "barrier_configured_ratio", "entry_price_mode",
    "entry_quality", "shadow_monitor_mode", "ttt_enabled", "ttt_tp_pct",
    "ttt_timeout_minutes", "final_score", "technical_score",
})
PROVIDER_FEATURE_FIELDS = frozenset({
    "price", "rsi", "adx", "atr", "atr_pct", "di_plus", "di_minus", "plus_di",
    "minus_di", "ema5", "ema9", "ema21", "ema50", "ema200", "vwap",
    "macd", "macd_signal", "macd_hist", "macd_histogram", "bb_upper", "bb_middle",
    "bb_lower", "bb_width", "bollinger_width", "volume", "volume_ratio",
    "volume_relative", "volume_spike", "obv", "mfi", "taker_buy_ratio",
    "buy_sell_ratio", "orderbook_imbalance", "spread_pct", "depth_usdt",
    *PRICE_POSITION_INDICATORS,
})


class ShadowCanonicalContractError(RuntimeError):
    def __init__(self, code: str, *, details: dict[str, Any] | None = None):
        super().__init__(code)
        self.code = code
        self.details = details or {}


@dataclass(frozen=True)
class CanonicalItem:
    record_id: UUID
    shadow_trade_id: UUID
    report_position: int
    payload: dict[str, Any]
    item_hash: str
    payload_bytes: int
    estimated_tokens: int


@dataclass(frozen=True)
class ShardPlan:
    record_id: UUID
    shard_index: int
    items: tuple[CanonicalItem, ...]
    payload_hash: str
    payload_bytes: int
    estimated_input_tokens: int


@dataclass(frozen=True)
class CapturedShadowDataset:
    report_run_id: UUID
    captured_at: datetime
    items: tuple[CanonicalItem, ...]
    shards: tuple[ShardPlan, ...]
    dataset_hash: str
    manifest: dict[str, Any]


def _json_value(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _estimated_tokens(payload_bytes: int) -> int:
    # Canonical JSON can be substantially denser than prose. Use the most
    # conservative provider ratio supported by the orchestration layer so a
    # shard accepted during capture cannot become oversized only when the
    # provider request is assembled.
    return max(1, math.ceil(payload_bytes / 1.2))


def _leaf_paths(value: Any, prefix: str = "") -> Iterable[tuple[str, Any]]:
    if isinstance(value, dict):
        if not value:
            yield prefix, value
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            yield from _leaf_paths(child, child_prefix)
        return
    if isinstance(value, list):
        if not value:
            yield prefix, value
        else:
            for index, child in enumerate(value):
                yield from _leaf_paths(child, f"{prefix}[{index}]")
        return
    yield prefix, value


def _materialize_breakouts(features: dict[str, Any]) -> dict[str, Any]:
    return {
        window: {
            "value": features.get(f"recent_high_{window}_distance_pct"),
            "source_indicator": f"recent_high_{window}_distance_pct",
        }
        for window in BREAKOUT_WINDOWS
    }


def _required_missing(payload: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    trade = payload["trade"]
    snapshots = payload["snapshots"]
    for field in REQUIRED_TRADE_FIELDS:
        if trade.get(field) is None:
            missing.append(f"trade.{field}")
    for name in REQUIRED_SNAPSHOTS:
        if not isinstance(snapshots.get(name), dict) or not snapshots[name]:
            missing.append(f"snapshots.{name}")
    entry_risk = snapshots.get("entry_risk") or {}
    risk_status = entry_risk.get("contract_status") or {}
    contract_valid = risk_status.get("entry_risk_contract_valid")
    terminal_status = str(risk_status.get("status") or "").upper()
    capture_status = str(trade.get("entry_risk_capture_status") or "").upper()
    if not isinstance(contract_valid, bool):
        missing.append("snapshots.entry_risk.contract_status.entry_risk_contract_valid")
    # Some historical writers persisted an incomplete/non-terminal entry-risk
    # envelope.  For read-only root-cause analysis that state is itself
    # evidence and must remain visible; it is never upgraded to VALID.  We fail
    # closed only when the two persisted status identities contradict.
    if terminal_status and capture_status != terminal_status:
        missing.append("trade.entry_risk_capture_status")
    if terminal_status == "PARTIAL":
        if risk_status.get("reconstructible") is not True:
            missing.append("snapshots.entry_risk.contract_status.reconstructible")
        if not isinstance(risk_status.get("reason_codes"), list) or not risk_status["reason_codes"]:
            missing.append("snapshots.entry_risk.contract_status.reason_codes")
    completed = trade.get("status") == "COMPLETED" or trade.get("outcome") is not None
    if completed:
        for field in REQUIRED_COMPLETED_TRADE_FIELDS:
            if trade.get(field) is None:
                missing.append(f"trade.{field}")
    return missing


def _snapshot_availability(payload: dict[str, Any]) -> dict[str, Any]:
    """Describe optional historical evidence without pretending it exists.

    Entry evidence remains fail-closed.  Exit analytical snapshots were not
    captured by every historical writer, even though the terminal outcome and
    its immutable label are available.  Version 2 keeps those trades in a
    report-wide entry audit, but makes every absent exit field explicit so the
    provider cannot infer or fabricate it.
    """

    trade = payload["trade"]
    snapshots = payload["snapshots"]
    completed = trade.get("status") == "COMPLETED" or trade.get("outcome") is not None
    availability: dict[str, Any] = {}
    entry_risk = snapshots.get("entry_risk") or {}
    risk_status = entry_risk.get("contract_status") or {}
    terminal_risk_status = str(risk_status.get("status") or "").upper()
    unavailable_risk_paths = [
        path
        for path, present in (
            ("trade.entry_risk_captured_at", trade.get("entry_risk_captured_at") is not None),
            (
                "snapshots.entry_risk.contract_status.status",
                terminal_risk_status in {"VALID", "PARTIAL"},
            ),
        )
        if not present
    ]
    risk_is_terminal = terminal_risk_status in {"VALID", "PARTIAL"}
    if terminal_risk_status and not risk_is_terminal:
        risk_availability_status = "UNAVAILABLE"
        risk_reason_codes = ["ENTRY_RISK_CAPTURE_NOT_TERMINAL"]
    elif unavailable_risk_paths:
        risk_availability_status = "PARTIAL"
        risk_reason_codes = ["HISTORICAL_ENTRY_RISK_METADATA_UNAVAILABLE"]
    else:
        risk_availability_status = "AVAILABLE"
        risk_reason_codes = []
    availability["entry_risk_contract"] = {
        "status": risk_availability_status,
        "reason_codes": risk_reason_codes,
        "unavailable_paths": unavailable_risk_paths,
    }
    source_at = trade.get("feature_source_at")
    source_times = snapshots.get("feature_source_times")
    source_at_available = source_at is not None
    source_times_available = isinstance(source_times, dict) and bool(source_times)
    temporal_status = (
        "AVAILABLE" if source_at_available and source_times_available
        else "PARTIAL" if source_at_available or source_times_available
        else "UNAVAILABLE"
    )
    availability["entry_temporal_lineage"] = {
        "status": temporal_status,
        "reason_codes": (
            [] if temporal_status == "AVAILABLE"
            else ["HISTORICAL_FEATURE_SOURCE_TIMESTAMP_UNAVAILABLE"]
        ),
        "unavailable_paths": [
            path
            for path, present in (
                ("trade.feature_source_at", source_at_available),
                ("snapshots.feature_source_times", source_times_available),
            )
            if not present
        ],
    }
    entry_features = snapshots.get("entry_features") or {}
    unavailable_entry_indicators = [
        indicator
        for indicator in PRICE_POSITION_INDICATORS
        if entry_features.get(indicator) is None
    ]
    availability["entry_feature_indicators"] = {
        "status": (
            "AVAILABLE" if not unavailable_entry_indicators
            else "UNAVAILABLE" if len(unavailable_entry_indicators) == len(PRICE_POSITION_INDICATORS)
            else "PARTIAL"
        ),
        "unavailable_fields": unavailable_entry_indicators,
        "reason_codes": (
            [] if not unavailable_entry_indicators
            else ["HISTORICAL_ENTRY_INDICATOR_UNAVAILABLE"]
        ),
    }
    if completed:
        for name in OPTIONAL_COMPLETED_SNAPSHOTS:
            snapshot = snapshots.get(name)
            available = isinstance(snapshot, dict) and bool(snapshot)
            availability[name] = {
                "status": "AVAILABLE" if available else "UNAVAILABLE",
                "reason_codes": [] if available else ["HISTORICAL_EXIT_CAPTURE_UNAVAILABLE"],
            }
        exit_features = snapshots.get("exit_features") or {}
        unavailable_indicators = [
            indicator
            for indicator in PRICE_POSITION_INDICATORS
            if exit_features.get(indicator) is None
        ]
        availability["exit_feature_indicators"] = {
            "status": "AVAILABLE" if not unavailable_indicators else "PARTIAL",
            "unavailable_fields": unavailable_indicators,
            "reason_codes": (
                [] if not unavailable_indicators
                else ["HISTORICAL_EXIT_INDICATOR_UNAVAILABLE"]
            ),
        }
    return availability


def canonical_trade_payload(report_run_id: UUID, position: int, row: ShadowTrade) -> dict[str, Any]:
    trade: dict[str, Any] = {}
    snapshots: dict[str, Any] = {}
    for column in ShadowTrade.__table__.columns:
        name = column.name
        value = _json_value(getattr(row, name))
        snapshot_name = SNAPSHOT_COLUMNS.get(name)
        if snapshot_name is not None:
            snapshots[snapshot_name] = value
        else:
            trade[name] = value

    entry_features = snapshots.get("entry_features") or {}
    exit_features = snapshots.get("exit_features") or {}
    payload: dict[str, Any] = {
        "input_contract_version": CONTRACT_VERSION,
        "report_run_id": str(report_run_id),
        "report_position": position,
        "trade": trade,
        "snapshots": snapshots,
        "virtual_indicators": {
            "entry": {"breakout_distance_pct": _materialize_breakouts(entry_features)},
            "exit": {"breakout_distance_pct": _materialize_breakouts(exit_features)},
        },
    }
    payload["evidence_availability"] = _snapshot_availability(payload)
    missing = _required_missing(payload)
    if missing:
        raise ShadowCanonicalContractError(
            "REQUIRED_FIELD_MISSING",
            details={"shadow_trade_id": str(row.id), "report_position": position, "paths": missing},
        )
    # Version 1 emitted one reason per null leaf, recursively including verbose
    # configuration arrays.  Besides duplicating information already encoded
    # as JSON null, that made provider requests grow quadratically with nested
    # audit envelopes.  V2 records reasons only for evidence whose absence has
    # an explicit governed meaning.
    payload["null_reasons"] = {}
    for snapshot_name, availability in payload["evidence_availability"].items():
        if availability.get("status") == "UNAVAILABLE":
            payload["null_reasons"][f"snapshots.{snapshot_name}"] = (
                availability["reason_codes"][0]
            )
        for field in availability.get("unavailable_fields") or []:
            feature_scope = (
                "entry_features" if snapshot_name == "entry_feature_indicators"
                else "exit_features"
            )
            payload["null_reasons"][f"snapshots.{feature_scope}.{field}"] = (
                availability["reason_codes"][0]
            )
        for path in availability.get("unavailable_paths") or []:
            payload["null_reasons"][path] = availability["reason_codes"][0]
    return payload


def _compact_feature_evaluations(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    allowed = (
        "rule_id", "section", "indicator", "operator", "actual", "expected",
        "result", "status", "required",
    )
    return [
        {key: item.get(key) for key in allowed if key in item}
        for item in value
        if isinstance(item, dict)
    ]


def _authorization_projection(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    included = {
        "contract_version", "valid", "authorization_status", "final_decision",
        "technical_decision", "contract_technical_decision", "legacy_decision",
        "decision_drift", "operational_effect",
    }
    projected = {
        key: value[key]
        for key in sorted(included)
        if key in value and (
            value[key] is None or isinstance(value[key], (bool, int, float, str))
        )
    }
    evaluations = _compact_feature_evaluations(value.get("feature_evaluations"))
    projected["feature_evaluation_count"] = len(evaluations)
    projected["failed_feature_evaluations"] = [
        item for item in evaluations
        if item.get("result") is False or str(item.get("status") or "").upper() in {"FAIL", "REJECT"}
    ]
    projected["feature_evaluations_hash"] = canonical_hash(value.get("feature_evaluations"))
    projected["source_document_hash"] = canonical_hash(value)
    return projected


def _gate_projection(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    included = {
        "contract_version", "base_eligible", "would_authorize",
        "technical_would_authorize", "contract_would_authorize", "shadow_decision",
        "contract_shadow_decision", "operational_decision", "legacy_decision",
        "decision_drift", "promotion_status", "operational_effect", "score",
    }
    projected = {
        key: value[key]
        for key in sorted(included)
        if key in value and (
            value[key] is None or isinstance(value[key], (bool, int, float, str))
        )
    }
    projected["source_document_hash"] = canonical_hash(value)
    return projected


def _scalar_summary(value: Any, *, depth: int = 2) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        projected: dict[str, Any] = {}
        omitted: dict[str, Any] = {}
        for key in sorted(value):
            child = value[key]
            if child is None or isinstance(child, (bool, int, float, str)):
                projected[key] = child
            elif depth > 0 and isinstance(child, dict):
                projected[key] = _scalar_summary(child, depth=depth - 1)
            elif isinstance(child, list) and len(child) <= 12 and all(
                item is None or isinstance(item, (bool, int, float, str)) for item in child
            ):
                projected[key] = child
            else:
                omitted[key] = child
        if omitted:
            projected["omitted_field_names"] = sorted(omitted)
            projected["omitted_fields_hash"] = canonical_hash(omitted)
        return projected
    return {"source_document_hash": canonical_hash(value)}


def _feature_projection(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"source_document_hash": canonical_hash(value), "value_status": "UNAVAILABLE"}
    projected = {
        key: child
        for key, child in sorted(value.items())
        if key in PROVIDER_FEATURE_FIELDS
        and child is not None
        and isinstance(child, (bool, int, float, str))
    }
    projected["source_document_hash"] = canonical_hash(value)
    projected["source_field_count"] = len(value)
    projected["provider_field_count"] = len(projected) - 2
    return projected


def _feature_reference(value: Any) -> dict[str, Any]:
    return {
        "status": "AVAILABLE" if isinstance(value, dict) and bool(value) else "UNAVAILABLE",
        "source_field_count": len(value) if isinstance(value, dict) else 0,
    }


def _rules_projection(value: Any) -> dict[str, Any]:
    return {
        "source_document_hash": canonical_hash(value),
        "rules_summary": _scalar_summary(value, depth=3),
    }


def _risk_projection(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"source_document_hash": canonical_hash(value), "status": "UNAVAILABLE"}
    projected: dict[str, Any] = {"source_document_hash": canonical_hash(value)}
    for key in ("schema_version", "captured_at"):
        if key in value:
            projected[key] = value[key]
    status = value.get("contract_status")
    if isinstance(status, dict):
        projected["contract_status"] = {
            key: status[key]
            for key in (
                "status", "entry_risk_contract_valid", "reconstructible", "reason_codes"
            )
            if key in status
        }
    return projected


def _availability_projection(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"status": "UNAVAILABLE"}
    projected: dict[str, Any] = {
        "source_document_hash": canonical_hash(value),
        "available_count": 0,
    }
    for key, item in sorted(value.items()):
        if not isinstance(item, dict):
            continue
        unavailable = list(item.get("unavailable_fields") or item.get("unavailable_paths") or [])
        status = str(item.get("status") or "UNAVAILABLE").upper()
        reason_codes = item.get("reason_codes") or []
        if status == "AVAILABLE" and not unavailable and not reason_codes:
            projected["available_count"] += 1
            continue
        projected[key] = {
            "status": status,
            "reason_codes": reason_codes,
            "unavailable_count": len(unavailable),
        }
    return projected


def _source_times_projection(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or not value:
        return {
            "status": "UNAVAILABLE",
            "source_count": 0,
        }
    timestamps = sorted(str(item) for item in value.values() if item is not None)
    return {
        "status": "AVAILABLE" if timestamps else "UNAVAILABLE",
        "source_count": len(value),
        "oldest_source_at": timestamps[0] if timestamps else None,
        "newest_source_at": timestamps[-1] if timestamps else None,
    }


def _configuration_projection(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {
            "source_document_hash": canonical_hash(value),
            "value_status": "UNAVAILABLE",
        }
    projected = {
        key: child
        for key, child in sorted(value.items())
        if key in PROVIDER_CONFIG_SCALAR_FIELDS
        if child is None or isinstance(child, (bool, int, float, str))
    }
    authorization = _authorization_projection(value.get("l3_authorization_contract_v3"))
    if authorization is not None:
        projected["l3_authorization_contract_v3"] = authorization
    gate = _gate_projection(value.get("l3_gate_v2"))
    if gate is not None:
        projected["l3_gate_v2"] = gate
    projected["source_document_hash"] = canonical_hash(value)
    return projected


def _provider_trade_projection(payload: dict[str, Any]) -> dict[str, Any]:
    source_snapshots = dict(payload.get("snapshots") or {})
    source_trade = dict(payload.get("trade") or {})
    trade = {
        key: source_trade[key]
        for key in sorted(PROVIDER_TRADE_FIELDS)
        if key in source_trade and source_trade[key] is not None
    }
    snapshots = {
        "configuration": _configuration_projection(source_snapshots.get("configuration")),
        "entry_features": _feature_projection(source_snapshots.get("entry_features")),
        "exit_features": _feature_reference(source_snapshots.get("exit_features")),
        "exit_metrics": _scalar_summary(source_snapshots.get("exit_metrics"), depth=0),
        "entry_risk": _risk_projection(source_snapshots.get("entry_risk")),
        "feature_source_times": _source_times_projection(
            source_snapshots.get("feature_source_times")
        ),
        "reason_codes": source_snapshots.get("reason_codes") or [],
    }
    return {
        "report_position": payload.get("report_position"),
        "trade": trade,
        "snapshots": snapshots,
        "evidence_availability": _availability_projection(
            payload.get("evidence_availability")
        ),
        "canonical_payload_hash": canonical_hash(payload),
    }


def _shard_payload_for_items(
    dataset_snapshot_id: UUID,
    shard_index: int,
    items: Iterable[CanonicalItem | AIDatasetSnapshotItemRecord],
    *,
    projection_cache: dict[str, tuple[dict[str, Any], str, dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    item_list = list(items)
    is_v2 = bool(item_list) and all(
        dict(item.payload if isinstance(item, CanonicalItem) else item.canonical_json).get(
            "input_contract_version"
        ) == CONTRACT_VERSION
        for item in item_list
    )
    output_items = []
    rules_catalog: dict[str, dict[str, Any]] = {}
    for item in item_list:
        canonical = dict(
            item.payload if isinstance(item, CanonicalItem) else item.canonical_json
        )
        if is_v2:
            cached = projection_cache.get(item.item_hash) if projection_cache is not None else None
            if cached is None:
                provider_trade = _provider_trade_projection(canonical)
                rules = (canonical.get("snapshots") or {}).get("rules")
                rules_hash = canonical_hash(rules)
                rules_projection = _rules_projection(rules)
                provider_trade["rules_catalog_ref"] = rules_hash
                cached = (provider_trade, rules_hash, rules_projection)
                if projection_cache is not None:
                    projection_cache[item.item_hash] = cached
            provider_trade, rules_hash, rules_projection = cached
            rules_catalog.setdefault(rules_hash, rules_projection)
        else:
            provider_trade = canonical
        output_items.append({
            "shadow_trade_id": str(item.shadow_trade_id),
            "item_hash": item.item_hash,
            "canonical_trade": provider_trade,
        })
    payload = {
        "input_contract_version": CONTRACT_VERSION if is_v2 else (
            item_list[0].canonical_json.get("input_contract_version")
            if item_list and isinstance(item_list[0], AIDatasetSnapshotItemRecord)
            else item_list[0].payload.get("input_contract_version") if item_list else CONTRACT_VERSION
        ),
        "dataset_snapshot_id": str(dataset_snapshot_id),
        "shard_index": shard_index,
        "items": output_items,
    }
    if is_v2:
        payload["provider_projection_contract_version"] = (
            PROVIDER_PROJECTION_CONTRACT_VERSION
        )
        payload["rules_catalog"] = rules_catalog
    return payload


def _shard_payload(
    dataset_snapshot_id: UUID,
    shard_index: int,
    items: Iterable[CanonicalItem],
    *,
    projection_cache: dict[str, tuple[dict[str, Any], str, dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    return _shard_payload_for_items(
        dataset_snapshot_id,
        shard_index,
        items,
        projection_cache=projection_cache,
    )


def plan_shards(
    *, dataset_snapshot_id: UUID, items: tuple[CanonicalItem, ...], max_input_tokens: int,
) -> tuple[ShardPlan, ...]:
    if max_input_tokens <= 0:
        raise ShadowCanonicalContractError("SHARD_CONTEXT_EXCEEDED")
    groups: list[list[CanonicalItem]] = []
    current: list[CanonicalItem] = []
    projection_cache: dict[str, tuple[dict[str, Any], str, dict[str, Any]]] = {}
    for item in items:
        candidate = [*current, item]
        encoded = canonical_json(_shard_payload(
            dataset_snapshot_id, len(groups), candidate, projection_cache=projection_cache,
        )).encode("utf-8")
        estimate = _estimated_tokens(len(encoded))
        if estimate > max_input_tokens:
            if not current:
                raise ShadowCanonicalContractError(
                    "SHARD_CONTEXT_EXCEEDED",
                    details={"shadow_trade_id": str(item.shadow_trade_id), "estimated_tokens": estimate},
                )
            groups.append(current)
            current = [item]
            encoded = canonical_json(_shard_payload(
                dataset_snapshot_id, len(groups), current, projection_cache=projection_cache,
            )).encode("utf-8")
            estimate = _estimated_tokens(len(encoded))
            if estimate > max_input_tokens:
                raise ShadowCanonicalContractError(
                    "SHARD_CONTEXT_EXCEEDED",
                    details={"shadow_trade_id": str(item.shadow_trade_id), "estimated_tokens": estimate},
                )
        else:
            current = candidate
    if current:
        groups.append(current)

    plans: list[ShardPlan] = []
    for index, group in enumerate(groups):
        payload = _shard_payload(
            dataset_snapshot_id, index, group, projection_cache=projection_cache,
        )
        encoded = canonical_json(payload).encode("utf-8")
        plans.append(ShardPlan(
            record_id=uuid4(), shard_index=index, items=tuple(group),
            payload_hash=canonical_hash(payload), payload_bytes=len(encoded),
            estimated_input_tokens=_estimated_tokens(len(encoded)),
        ))
    return tuple(plans)


async def capture_report(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    report_run_id: UUID,
    dataset_snapshot_id: UUID,
    max_shard_input_tokens: int,
    captured_at: datetime,
) -> CapturedShadowDataset:
    report = (await db.execute(
        select(ShadowTradeReportRun).where(
            ShadowTradeReportRun.id == report_run_id,
            ShadowTradeReportRun.user_id == tenant_id,
        ).with_for_update(read=True)
    )).scalar_one_or_none()
    if report is None:
        raise ShadowCanonicalContractError("REPORT_RUN_NOT_FOUND")

    joined = list((await db.execute(
        select(ShadowTradeReportItem.position, ShadowTrade)
        .join(ShadowTrade, ShadowTrade.id == ShadowTradeReportItem.shadow_trade_id)
        .where(ShadowTradeReportItem.report_run_id == report_run_id)
        .order_by(ShadowTradeReportItem.position)
        .with_for_update(read=True)
    )).all())
    positions = [position for position, _ in joined]
    trade_ids = [row.id for _, row in joined]
    expected_positions = list(range(len(joined)))
    if report.total_trades != len(joined) or positions != expected_positions or len(trade_ids) != len(set(trade_ids)):
        raise ShadowCanonicalContractError(
            "REPORT_ROW_MISMATCH",
            details={
                "expected": report.total_trades,
                "loaded": len(joined),
                "positions_contiguous": positions == expected_positions,
                "unique_trades": len(trade_ids) == len(set(trade_ids)),
            },
        )

    items: list[CanonicalItem] = []
    coverage: dict[str, dict[str, int]] = {}
    for position, trade_row in joined:
        payload = canonical_trade_payload(report_run_id, position, trade_row)
        encoded = canonical_json(payload).encode("utf-8")
        item = CanonicalItem(
            record_id=uuid4(), shadow_trade_id=trade_row.id, report_position=position,
            payload=payload, item_hash=canonical_hash(payload), payload_bytes=len(encoded),
            estimated_tokens=_estimated_tokens(len(encoded)),
        )
        items.append(item)
        for path, value in _leaf_paths(payload):
            stats = coverage.setdefault(path, {"present": 0, "null": 0})
            stats["null" if value is None else "present"] += 1

    item_tuple = tuple(items)
    shards = plan_shards(
        dataset_snapshot_id=dataset_snapshot_id,
        items=item_tuple,
        max_input_tokens=max_shard_input_tokens,
    )
    item_hashes = [item.item_hash for item in item_tuple]
    optional_missingness: dict[str, int] = {}
    for item in item_tuple:
        for path in item.payload.get("null_reasons") or {}:
            optional_missingness[path] = optional_missingness.get(path, 0) + 1
    dataset_hash = canonical_hash({
        "input_contract_version": CONTRACT_VERSION,
        "report_run_id": str(report_run_id),
        "ordered_item_hashes": item_hashes,
    })
    manifest = {
        "input_contract_version": CONTRACT_VERSION,
        "capture_at": captured_at.isoformat(),
        "report_run_id": str(report_run_id),
        "source_item_count": report.total_trades,
        "processed_item_count": 0,
        "coverage_status": "CAPTURED_COMPLETE",
        "shard_count": len(shards),
        "dataset_hash": dataset_hash,
        "legacy_incomplete": False,
        "coverage_by_path": coverage,
        "ordered_item_hashes": item_hashes,
        "shard_plan": [{
            "shard_index": shard.shard_index,
            "item_count": len(shard.items),
            "item_ids": [str(item.shadow_trade_id) for item in shard.items],
            "item_hashes": [item.item_hash for item in shard.items],
            "payload_hash": shard.payload_hash,
            "payload_bytes": shard.payload_bytes,
            "estimated_input_tokens": shard.estimated_input_tokens,
        } for shard in shards],
        "missing_required_fields": [],
        "optional_missingness_by_path": optional_missingness,
    }
    return CapturedShadowDataset(
        report_run_id=report_run_id, captured_at=captured_at, items=item_tuple,
        shards=shards, dataset_hash=dataset_hash, manifest=manifest,
    )


async def persist_capture(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    ai_request_id: UUID,
    dataset_snapshot_id: UUID,
    capture: CapturedShadowDataset,
) -> None:
    for item in capture.items:
        db.add(AIDatasetSnapshotItemRecord(
            id=item.record_id, tenant_id=tenant_id, dataset_snapshot_id=dataset_snapshot_id,
            report_run_id=capture.report_run_id, shadow_trade_id=item.shadow_trade_id,
            report_position=item.report_position, canonical_json=item.payload,
            item_hash=item.item_hash, payload_bytes=item.payload_bytes,
            estimated_tokens=item.estimated_tokens,
        ))
    for shard in capture.shards:
        db.add(AIAnalysisShardRecord(
            id=shard.record_id, tenant_id=tenant_id, ai_request_id=ai_request_id,
            dataset_snapshot_id=dataset_snapshot_id, shard_index=shard.shard_index,
            status="PLANNED", item_count=len(shard.items),
            item_ids=[str(item.record_id) for item in shard.items],
            item_hashes=[item.item_hash for item in shard.items],
            payload_hash=shard.payload_hash, payload_bytes=shard.payload_bytes,
            estimated_input_tokens=shard.estimated_input_tokens,
        ))
    await db.flush()


async def load_canonical_items(
    db: AsyncSession, *, tenant_id: UUID, dataset_snapshot_id: UUID,
) -> list[dict[str, Any]]:
    rows = list((await db.execute(
        select(AIDatasetSnapshotItemRecord).where(
            AIDatasetSnapshotItemRecord.tenant_id == tenant_id,
            AIDatasetSnapshotItemRecord.dataset_snapshot_id == dataset_snapshot_id,
        ).order_by(AIDatasetSnapshotItemRecord.report_position)
    )).scalars())
    return [dict(row.canonical_json) for row in rows]


def provider_shard_payload(dataset_snapshot_id: UUID, shard: AIAnalysisShardRecord, items: list[AIDatasetSnapshotItemRecord]) -> dict[str, Any]:
    payload = _shard_payload_for_items(dataset_snapshot_id, shard.shard_index, items)
    if canonical_hash(payload) != shard.payload_hash:
        raise ShadowCanonicalContractError("DATASET_RECONCILIATION_FAILED")
    return payload


def reconcile_shard_results(
    *, expected_items: list[AIDatasetSnapshotItemRecord], shards: list[AIAnalysisShardRecord],
) -> None:
    expected = {str(item.shadow_trade_id): item.item_hash for item in expected_items}
    seen: dict[str, str] = {}
    for shard in shards:
        if shard.status != "COMPLETED" or not isinstance(shard.result_json, dict):
            raise ShadowCanonicalContractError("SHARD_FAILED")
        processed = shard.result_json.get("processed_items")
        if not isinstance(processed, list):
            raise ShadowCanonicalContractError("DATASET_RECONCILIATION_FAILED")
        for item in processed:
            item_id = str((item or {}).get("shadow_trade_id") or "")
            item_hash = str((item or {}).get("item_hash") or "")
            if not item_id or item_id in seen or expected.get(item_id) != item_hash:
                raise ShadowCanonicalContractError("DATASET_RECONCILIATION_FAILED")
            seen[item_id] = item_hash
    if seen != expected:
        raise ShadowCanonicalContractError("DATASET_RECONCILIATION_FAILED")


__all__ = [
    "CONTRACT_VERSION", "PRICE_POSITION_INDICATORS", "CapturedShadowDataset",
    "ShadowCanonicalContractError", "capture_report", "load_canonical_items",
    "persist_capture", "plan_shards", "provider_shard_payload", "reconcile_shard_results",
]
