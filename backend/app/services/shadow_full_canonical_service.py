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


CONTRACT_VERSION = "shadow-portfolio-full-canonical-v1"
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
    "label_contract_version", "barrier_contract_version", "feature_source_at",
    "features_captured_at", "feature_hash", "profile_config_hash", "score_engine_config_hash",
    "lineage_status", "watchlist_id", "watchlist_name", "watchlist_level",
    "lineage_confidence", "lineage_source", "lineage_resolved_at",
    "entry_risk_capture_status", "entry_risk_captured_at",
)
REQUIRED_SNAPSHOTS = ("configuration", "entry_features", "rules", "entry_risk")
REQUIRED_COMPLETED_TRADE_FIELDS = (
    "exit_price", "exit_timestamp", "outcome", "label_resolved_at",
)
REQUIRED_COMPLETED_SNAPSHOTS = ("exit_features", "exit_metrics")


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
    if terminal_status not in {"VALID", "PARTIAL"}:
        missing.append("snapshots.entry_risk.contract_status.status")
    if capture_status != terminal_status:
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
        for name in REQUIRED_COMPLETED_SNAPSHOTS:
            if not isinstance(snapshots.get(name), dict) or not snapshots[name]:
                missing.append(f"snapshots.{name}")
        for snapshot_name in ("entry_features", "exit_features"):
            features = snapshots.get(snapshot_name) or {}
            for indicator in PRICE_POSITION_INDICATORS:
                if features.get(indicator) is None:
                    missing.append(f"snapshots.{snapshot_name}.{indicator}")
    return missing


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
    missing = _required_missing(payload)
    if missing:
        raise ShadowCanonicalContractError(
            "REQUIRED_FIELD_MISSING",
            details={"shadow_trade_id": str(row.id), "report_position": position, "paths": missing},
        )
    payload["null_reasons"] = {
        path: "SOURCE_VALUE_NULL"
        for path, value in _leaf_paths({"trade": trade, "snapshots": snapshots})
        if value is None
    }
    return payload


def _shard_payload(dataset_snapshot_id: UUID, shard_index: int, items: Iterable[CanonicalItem]) -> dict[str, Any]:
    return {
        "input_contract_version": CONTRACT_VERSION,
        "dataset_snapshot_id": str(dataset_snapshot_id),
        "shard_index": shard_index,
        "items": [{
            "shadow_trade_id": str(item.shadow_trade_id),
            "item_hash": item.item_hash,
            "canonical_trade": item.payload,
        } for item in items],
    }


def plan_shards(
    *, dataset_snapshot_id: UUID, items: tuple[CanonicalItem, ...], max_input_tokens: int,
) -> tuple[ShardPlan, ...]:
    if max_input_tokens <= 0:
        raise ShadowCanonicalContractError("SHARD_CONTEXT_EXCEEDED")
    groups: list[list[CanonicalItem]] = []
    current: list[CanonicalItem] = []
    for item in items:
        candidate = [*current, item]
        encoded = canonical_json(_shard_payload(dataset_snapshot_id, len(groups), candidate)).encode("utf-8")
        estimate = _estimated_tokens(len(encoded))
        if estimate > max_input_tokens:
            if not current:
                raise ShadowCanonicalContractError(
                    "SHARD_CONTEXT_EXCEEDED",
                    details={"shadow_trade_id": str(item.shadow_trade_id), "estimated_tokens": estimate},
                )
            groups.append(current)
            current = [item]
            encoded = canonical_json(_shard_payload(dataset_snapshot_id, len(groups), current)).encode("utf-8")
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
        payload = _shard_payload(dataset_snapshot_id, index, group)
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
    payload = {
        "input_contract_version": CONTRACT_VERSION,
        "dataset_snapshot_id": str(dataset_snapshot_id),
        "shard_index": shard.shard_index,
        "items": [{
            "shadow_trade_id": str(item.shadow_trade_id),
            "item_hash": item.item_hash,
            "canonical_trade": dict(item.canonical_json),
        } for item in items],
    }
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
