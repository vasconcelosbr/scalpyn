"""Detailed Shadow Portfolio reports, immutable selections and JSON exports."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models.backoffice import DecisionLog
from ..models.shadow_trade import ShadowTrade
from ..models.shadow_trade_analysis import ShadowTradeReportItem, ShadowTradeReportRun
from .config import get_current_user_id
from .shadow_trades import _to_detail, get_shadow_trade_chart


router = APIRouter(prefix="/api/shadow-trade-reports", tags=["Shadow Trade Reports"])

ReportSource = Literal["L3", "L3_REJECTED", "L1_SPECTRUM"]
ReportOutcome = Literal["TP_HIT", "SL_HIT"]
_DECISION_UNSET = object()


class DetailedReportRequest(BaseModel):
    sources: list[ReportSource] = Field(min_length=1)
    watchlist_ids: list[UUID] = Field(default_factory=list)
    include_legacy_watchlist: bool = False
    profile_ids: list[UUID] = Field(default_factory=list)
    outcomes: list[ReportOutcome] = Field(default_factory=lambda: ["TP_HIT", "SL_HIT"])
    date_from: datetime
    date_to: datetime
    timezone: str = Field(default="UTC", min_length=1, max_length=80)

    @model_validator(mode="after")
    def validate_range(self):
        if self.date_to <= self.date_from:
            raise ValueError("date_to must be greater than date_from")
        if not self.outcomes:
            raise ValueError("at least one outcome is required")
        return self


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _sha(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def _report_completeness(trades: list[ShadowTrade]) -> dict[str, int | bool]:
    """Materialize the report's provider-readiness contract before AI use."""
    required_missing = {
        "missing_watchlist_id": sum(1 for trade in trades if trade.watchlist_id is None),
        "missing_watchlist_name": sum(1 for trade in trades if trade.watchlist_name is None),
        "missing_watchlist_level": sum(1 for trade in trades if trade.watchlist_level is None),
        "missing_lineage_confidence": sum(1 for trade in trades if trade.lineage_confidence is None),
        "missing_lineage_source": sum(1 for trade in trades if trade.lineage_source is None),
        "missing_lineage_resolved_at": sum(1 for trade in trades if trade.lineage_resolved_at is None),
        "missing_rules_snapshot": sum(
            1
            for trade in trades
            if not isinstance(trade.rules_snapshot, dict) or not trade.rules_snapshot
        ),
    }
    return {
        # Existing keys remain stable for current API consumers.
        "missing_watchlist_lineage": required_missing["missing_watchlist_id"],
        "missing_profile_lineage": sum(1 for trade in trades if trade.profile_id is None),
        "missing_entry_snapshot": sum(1 for trade in trades if not trade.features_snapshot),
        "missing_exit_snapshot": sum(1 for trade in trades if not trade.features_snapshot_exit),
        **required_missing,
        "canonical_analysis_ready": not any(required_missing.values()),
    }


def _normalized_filters(payload: DetailedReportRequest) -> dict[str, Any]:
    return {
        "sources": sorted(set(payload.sources)),
        "watchlist_ids": sorted(str(value) for value in set(payload.watchlist_ids)),
        "include_legacy_watchlist": payload.include_legacy_watchlist,
        "profile_ids": sorted(str(value) for value in set(payload.profile_ids)),
        "outcomes": sorted(set(payload.outcomes)),
        "date_from": _utc(payload.date_from).isoformat(),
        "date_to": _utc(payload.date_to).isoformat(),
        "date_basis": "COALESCE(exit_timestamp,completed_at)",
        "timezone": payload.timezone,
    }


def _report_conditions(user_id: UUID, filters: dict[str, Any]) -> list[Any]:
    closed_at = func.coalesce(ShadowTrade.exit_timestamp, ShadowTrade.completed_at)
    conditions: list[Any] = [
        ShadowTrade.user_id == user_id,
        ShadowTrade.status == "COMPLETED",
        ShadowTrade.outcome.in_(filters["outcomes"]),
        ShadowTrade.source.in_(filters["sources"]),
        closed_at >= datetime.fromisoformat(filters["date_from"]),
        closed_at < datetime.fromisoformat(filters["date_to"]),
    ]
    if filters["profile_ids"]:
        conditions.append(ShadowTrade.profile_id.in_([UUID(value) for value in filters["profile_ids"]]))
    watchlist_ids = [UUID(value) for value in filters["watchlist_ids"]]
    if watchlist_ids and filters["include_legacy_watchlist"]:
        conditions.append(
            (ShadowTrade.watchlist_id.in_(watchlist_ids)) | (ShadowTrade.watchlist_id.is_(None))
        )
    elif watchlist_ids:
        conditions.append(ShadowTrade.watchlist_id.in_(watchlist_ids))
    return conditions


async def _owned_run(db: AsyncSession, user_id: UUID, run_id: UUID) -> ShadowTradeReportRun:
    run = (
        await db.execute(
            select(ShadowTradeReportRun).where(
                ShadowTradeReportRun.id == run_id,
                ShadowTradeReportRun.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Detailed report run not found")
    return run


def _run_dict(run: ShadowTradeReportRun) -> dict[str, Any]:
    return jsonable_encoder(
        {
            "id": run.id,
            "status": run.status,
            "filters": run.filters,
            "filters_hash": run.filters_hash,
            "trade_ids_hash": run.trade_ids_hash,
            "timezone": run.timezone,
            "total_trades": run.total_trades,
            "completeness": run.completeness,
            "created_at": run.created_at,
        }
    )


async def _decision_for_trade(
    db: AsyncSession, user_id: UUID, trade: ShadowTrade
) -> DecisionLog | None:
    if trade.decision_id is None:
        return None
    return (
        await db.execute(
            select(DecisionLog).where(
                DecisionLog.id == trade.decision_id,
                DecisionLog.user_id == user_id,
            )
        )
    ).scalar_one_or_none()


def _indicator_comparison(entry: dict[str, Any], exit_: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for indicator in sorted(set(entry) | set(exit_)):
        if indicator.startswith("_"):
            continue
        before = entry.get(indicator)
        after = exit_.get(indicator)
        absolute = None
        percent = None
        if isinstance(before, (int, float)) and isinstance(after, (int, float)):
            absolute = float(after) - float(before)
            percent = None if before == 0 else absolute / abs(float(before)) * 100
        rows.append(
            {
                "indicator": indicator,
                "entry": before,
                "exit": after,
                "delta_absolute": absolute,
                "delta_percent": percent,
            }
        )
    return rows


async def build_trade_export(
    db: AsyncSession,
    user_id: UUID,
    trade: ShadowTrade,
    *,
    report_run_id: UUID | None = None,
    include_chart: bool = True,
    decision: DecisionLog | None | object = _DECISION_UNSET,
) -> dict[str, Any]:
    if decision is _DECISION_UNSET:
        decision = await _decision_for_trade(db, user_id, trade)
    detail = _to_detail(trade, decision=decision if isinstance(decision, DecisionLog) else None)
    detail_json = jsonable_encoder(detail)
    entry = detail.entry_metrics or detail.features_snapshot or {}
    exit_failed = bool((detail.features_snapshot_exit or {}).get("_capture_failed") is True)
    exit_ = detail.exit_metrics or ({} if exit_failed else detail.features_snapshot_exit or {})
    decision_metrics = detail.decision_metrics or {}
    l3_gate = (
        decision_metrics.get("l3_gate_v2")
        or (trade.config_snapshot or {}).get("l3_gate_v2")
        or {}
    )
    block_rules_audit = (
        l3_gate.get("block_rules")
        or decision_metrics.get("block_rules_audit")
        or {}
    )
    block_rules_lineage = (
        decision_metrics.get("block_rules_lineage")
        or (trade.rules_snapshot or {}).get("_block_rules_lineage")
        or {}
    )
    chart: dict[str, Any] | None = None
    chart_error: str | None = None
    if include_chart:
        try:
            chart_model = await get_shadow_trade_chart(
                shadow_id=trade.id,
                context_minutes=30,
                db=db,
                user_id=user_id,
            )
            chart = jsonable_encoder(chart_model)
            chart["markers"] = {
                "buy": {"label": "B", "timestamp": chart["entry_timestamp"], "price": chart["entry_price"]},
                "sell": {
                    "label": "S",
                    "timestamp": chart["exit_timestamp"],
                    "price": chart["exit_price"],
                    "outcome": chart["outcome"],
                },
            }
        except HTTPException as exc:
            chart_error = str(exc.detail)

    lineage = {
        "source": trade.source,
        "watchlist_id": str(trade.watchlist_id) if trade.watchlist_id else None,
        "watchlist_name": trade.watchlist_name,
        "watchlist_level": trade.watchlist_level,
        "source_watchlist_id": str(trade.source_watchlist_id) if trade.source_watchlist_id else None,
        "lineage_confidence": trade.lineage_confidence,
        "lineage_source": trade.lineage_source,
        "lineage_resolved_at": trade.lineage_resolved_at,
        "event_id": str(trade.event_id) if trade.event_id else None,
        "snapshot_id": str(trade.snapshot_id) if trade.snapshot_id else None,
        "profile_version_id": str(trade.profile_version_id) if trade.profile_version_id else None,
        "score_engine_version_id": str(trade.score_engine_version_id) if trade.score_engine_version_id else None,
        "profile_config_hash": (
            block_rules_lineage.get("profile_config_hash")
            or trade.profile_config_hash
        ),
        "profile_block_rules_hash": (
            block_rules_lineage.get("profile_block_rules_hash")
            or block_rules_audit.get("profile_block_rules_hash")
        ),
        "global_block_rules_hash": (
            block_rules_lineage.get("global_block_rules_hash")
            or block_rules_audit.get("global_block_rules_hash")
        ),
        "effective_block_rules_hash": (
            block_rules_lineage.get("effective_block_rules_hash")
            or block_rules_audit.get("effective_block_rules_hash")
        ),
        "score_engine_config_hash": trade.score_engine_config_hash,
        "feature_hash": trade.feature_hash,
        "feature_schema_version": trade.feature_schema_version,
        "capture_contract_version": trade.capture_contract_version,
        "label_contract_version": trade.label_contract_version,
        "barrier_contract_version": trade.barrier_contract_version,
        "lineage_status": trade.lineage_status,
        "eligible_for_training": trade.eligible_for_training,
    }
    return jsonable_encoder(
        {
            "export_metadata": {
                "schema": "scalpyn.shadow_trade_export",
                "schema_version": "2.2.0",
                "generated_at": datetime.now(timezone.utc),
                "report_run_id": report_run_id,
                "completeness": {
                    "trade_detail_loaded": True,
                    "chart_loaded": chart is not None,
                    "chart_error": chart_error,
                    "watchlist_lineage_present": trade.watchlist_id is not None,
                    "profile_lineage_present": trade.profile_id is not None,
                    "entry_snapshot_present": bool(entry),
                    "exit_snapshot_present": bool(exit_),
                    "exit_capture_failed": exit_failed,
                },
            },
            "selection_context": {
                "source": trade.source,
                "watchlist_id": lineage["watchlist_id"],
                "profile_id": str(trade.profile_id) if trade.profile_id else None,
            },
            "trade": detail_json,
            "decision_audit": {
                "strategy": detail.decision_strategy,
                "score": detail.decision_score,
                "decision": detail.decision_decision,
                "event_type": detail.decision_event_type,
                "l1_pass": detail.decision_l1_pass,
                "l2_pass": detail.decision_l2_pass,
                "l3_pass": detail.decision_l3_pass,
                "latency_ms": detail.decision_latency_ms,
                "created_at": detail.decision_created_at,
                "reasons": detail.decision_reasons,
                "metrics": detail.decision_metrics,
            },
            "lineage": lineage,
            "snapshots": {
                "config": trade.config_snapshot,
                "profile_rules": trade.rules_snapshot,
                "block_rules_audit": block_rules_audit or None,
                "block_rules_lineage": block_rules_lineage or None,
                "indicators_at_entry": trade.features_snapshot,
                "indicators_at_exit": trade.features_snapshot_exit,
                "exit_metrics": trade.exit_metrics_json,
                "entry_risk_features": trade.entry_risk_features_json,
            },
            "entry_risk": {
                "capture_status": trade.entry_risk_capture_status,
                "captured_at": trade.entry_risk_captured_at,
                "entry_risk_features": trade.entry_risk_features_json,
                "entry_exhaustion_score": (
                    (trade.features_snapshot or {}).get("entry_exhaustion_score")
                ),
                "momentum_intensity_score": (
                    ((trade.entry_risk_features_json or {}).get("momentum_intensity") or {}).get(
                        "momentum_intensity_score"
                    )
                ),
                "exhaustion_risk_score": (
                    ((trade.entry_risk_features_json or {}).get("exhaustion_risk") or {}).get(
                        "exhaustion_risk_score"
                    )
                ),
                "operational_effect": False,
            },
            "indicator_analysis": {
                "entry_metrics": entry,
                "exit_metrics": exit_,
                "comparison": _indicator_comparison(entry, exit_),
            },
            "chart": chart,
            "raw": {"trade_detail": detail_json, "chart_response": chart},
        }
    )


@router.get("/facets")
async def detailed_report_facets(
    sources: list[ReportSource] = Query(default=["L3"]),
    watchlist_ids: list[UUID] = Query(default=[]),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    base = [
        ShadowTrade.user_id == user_id,
        ShadowTrade.status == "COMPLETED",
        ShadowTrade.outcome.in_(["TP_HIT", "SL_HIT"]),
        ShadowTrade.source.in_(sources),
    ]
    watchlists = (
        await db.execute(
            select(
                ShadowTrade.watchlist_id,
                func.max(ShadowTrade.watchlist_name).label("name"),
                func.count(ShadowTrade.id).label("count"),
            )
            .where(and_(*base))
            .group_by(ShadowTrade.watchlist_id)
            .order_by(func.max(ShadowTrade.watchlist_name).asc().nulls_last())
        )
    ).all()
    profile_conditions = list(base)
    if watchlist_ids:
        profile_conditions.append(ShadowTrade.watchlist_id.in_(watchlist_ids))
    profiles = (
        await db.execute(
            select(
                ShadowTrade.profile_id,
                func.max(ShadowTrade.profile_name).label("name"),
                func.count(ShadowTrade.id).label("count"),
            )
            .where(and_(*profile_conditions))
            .group_by(ShadowTrade.profile_id)
            .order_by(func.max(ShadowTrade.profile_name).asc().nulls_last())
        )
    ).all()
    return {
        "sources": [
            {"id": "L3", "label": "Aprovados (L3)"},
            {"id": "L3_REJECTED", "label": "Rejeitados (L3)"},
            {"id": "L1_SPECTRUM", "label": "Dataset ML (L1)"},
        ],
        "watchlists": [
            {"id": str(row.watchlist_id) if row.watchlist_id else None, "name": row.name or "Sem vínculo/legado", "count": row.count}
            for row in watchlists
        ],
        "profiles": [
            {"id": str(row.profile_id) if row.profile_id else None, "name": row.name or "Sem profile/legado", "count": row.count}
            for row in profiles
        ],
    }


@router.post("/runs", status_code=201)
async def create_detailed_report_run(
    payload: DetailedReportRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    filters = _normalized_filters(payload)
    closed_at = func.coalesce(ShadowTrade.exit_timestamp, ShadowTrade.completed_at)
    trades = (
        await db.execute(
            select(ShadowTrade).where(and_(*_report_conditions(user_id, filters))).order_by(
                desc(closed_at), desc(ShadowTrade.id)
            )
        )
    ).scalars().all()
    trade_ids = [str(trade.id) for trade in trades]
    completeness = _report_completeness(trades)
    run = ShadowTradeReportRun(
        user_id=user_id,
        filters=filters,
        filters_hash=_sha(filters),
        trade_ids_hash=_sha(trade_ids),
        timezone=payload.timezone,
        total_trades=len(trades),
        status="READY",
        completeness=completeness,
    )
    db.add(run)
    await db.flush()
    db.add_all(
        [
            ShadowTradeReportItem(report_run_id=run.id, shadow_trade_id=trade.id, position=index)
            for index, trade in enumerate(trades)
        ]
    )
    await db.commit()
    await db.refresh(run)
    return _run_dict(run)


@router.get("/runs/{run_id}")
async def get_detailed_report_run(
    run_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    run = await _owned_run(db, user_id, run_id)
    return _run_dict(run)


@router.get("/runs/{run_id}/trades")
async def list_detailed_report_trades(
    run_id: UUID,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    run = await _owned_run(db, user_id, run_id)
    rows = (
        await db.execute(
            select(ShadowTradeReportItem.position, ShadowTrade)
            .join(ShadowTrade, ShadowTrade.id == ShadowTradeReportItem.shadow_trade_id)
            .where(ShadowTradeReportItem.report_run_id == run.id)
            .order_by(ShadowTradeReportItem.position)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    items = []
    for position, trade in rows:
        items.append(
            {
                "position": position,
                "id": str(trade.id),
                "closed_at": trade.exit_timestamp or trade.completed_at,
                "symbol": trade.symbol,
                "source": trade.source,
                "watchlist_id": str(trade.watchlist_id) if trade.watchlist_id else None,
                "watchlist_name": trade.watchlist_name,
                "profile_id": str(trade.profile_id) if trade.profile_id else None,
                "profile_name": trade.profile_name,
                "profile_version": trade.profile_version,
                "profile_config_hash": trade.profile_config_hash,
                "outcome": trade.outcome,
                "entry_price": trade.entry_price,
                "exit_price": trade.exit_price,
                "pnl_pct": trade.pnl_pct,
                "pnl_usdt": trade.pnl_usdt,
                "mae_pct": trade.mae_pct,
                "mfe_pct": trade.mfe_pct,
                "holding_seconds": trade.holding_seconds,
                "lineage_confidence": trade.lineage_confidence,
                "entry_snapshot_present": bool(trade.features_snapshot),
                "exit_snapshot_present": bool(trade.features_snapshot_exit),
                "entry_risk_capture_status": trade.entry_risk_capture_status,
                "entry_risk_contract_valid": (
                    ((trade.entry_risk_features_json or {}).get("contract_status") or {}).get(
                        "entry_risk_contract_valid"
                    )
                ),
            }
        )
    return {"items": jsonable_encoder(items), "page": page, "page_size": page_size, "total": run.total_trades}


@router.get("/trades/{trade_id}/export")
async def export_shadow_trade(
    trade_id: UUID,
    include_chart: bool = Query(default=True),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    trade = (
        await db.execute(
            select(ShadowTrade).where(ShadowTrade.id == trade_id, ShadowTrade.user_id == user_id)
        )
    ).scalar_one_or_none()
    if trade is None:
        raise HTTPException(status_code=404, detail="Shadow trade not found")
    return await build_trade_export(db, user_id, trade, include_chart=include_chart)


@router.get("/runs/{run_id}/export")
async def export_detailed_report(
    run_id: UUID,
    include_chart: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    run = await _owned_run(db, user_id, run_id)
    rows = (
        await db.execute(
            select(ShadowTrade)
            .join(ShadowTradeReportItem, ShadowTradeReportItem.shadow_trade_id == ShadowTrade.id)
            .where(ShadowTradeReportItem.report_run_id == run.id)
            .order_by(ShadowTradeReportItem.position)
        )
    ).scalars().all()
    decision_ids = [trade.decision_id for trade in rows if trade.decision_id is not None]
    decisions = {}
    if decision_ids:
        decision_rows = (
            await db.execute(
                select(DecisionLog).where(
                    DecisionLog.user_id == user_id,
                    DecisionLog.id.in_(decision_ids),
                )
            )
        ).scalars().all()
        decisions = {decision.id: decision for decision in decision_rows}
    trades = [
        await build_trade_export(
            db,
            user_id,
            trade,
            report_run_id=run.id,
            include_chart=include_chart,
            decision=decisions.get(trade.decision_id),
        )
        for trade in rows
    ]
    return {
        "export_metadata": {
            "schema": "scalpyn.shadow_trade_report_export",
            "schema_version": "1.0.0",
            "generated_at": datetime.now(timezone.utc),
            "report_run_id": str(run.id),
            "filters_hash": run.filters_hash,
            "trade_ids_hash": run.trade_ids_hash,
        },
        "selection": run.filters,
        "summary": {"total_trades": run.total_trades},
        "completeness": run.completeness,
        "trades": trades,
    }
