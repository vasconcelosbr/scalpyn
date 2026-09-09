from __future__ import annotations

import csv
import io
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..database import get_db
from ..models.pump_radar import (
    PumpRadarEvent,
    PumpRadarEventLink,
    PumpRadarHypothesis,
    PumpRadarIndicatorSnapshot,
    PumpRadarIndicatorValue,
    PumpRadarOHLCV,
    PumpRadarRangeResult,
    PumpRadarRun,
    PumpRadarRunAsset,
)
from ..schemas.pump_radar import PumpRadarConfig, PumpRadarHypothesisCreate, PumpRadarRunCreate
from ..services.config_service import config_service
from ..tasks.celery_app import QUEUE_PUMP_RADAR
from ..tasks.task_dispatch import enqueue
from .config import get_current_user_id

router = APIRouter(prefix="/api/pump-radar", tags=["Pump Radar"])
SCHEMA_VERSION = "pump_radar_api_v1"


def _number(value: Any) -> float | int | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return value


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _envelope(data: Any, *, provenance: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "schema": SCHEMA_VERSION,
        "timezone": "UTC",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provenance": provenance or {"source": "pump_radar_tables"},
        "data": data,
    }


def _require_capture() -> None:
    if not settings.PUMP_RADAR_CAPTURE_ENABLED:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="PUMP_RADAR_CAPTURE_DISABLED")


async def _owned_run(db: AsyncSession, run_id: UUID, user_id: UUID) -> PumpRadarRun:
    run = (await db.execute(select(PumpRadarRun).where(PumpRadarRun.id == run_id, PumpRadarRun.user_id == user_id))).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="PUMP_RADAR_RUN_NOT_FOUND")
    return run


def _quality_badge(run: PumpRadarRun | None, event_count: int = 0) -> str:
    if run is None or run.status == "QUEUED":
        return "PROCESSANDO" if run else "DADOS INSUFICIENTES"
    if run.status in {"RUNNING", "CANCELLING"}:
        return "PROCESSANDO"
    if run.status in {"FAILED", "CANCELLED"} or event_count == 0:
        return "DADOS INSUFICIENTES"
    if run.status == "PARTIAL" or run.failed_assets:
        return "COBERTURA PARCIAL"
    return "DADOS REAIS"


def _run_payload(run: PumpRadarRun, event_count: int = 0) -> dict[str, Any]:
    return {
        "id": str(run.id), "status": run.status, "mode": run.mode,
        "date_from": _iso(run.date_from), "date_to": _iso(run.date_to),
        "config_hash": run.config_hash, "timezone": run.timezone,
        "total_assets": run.total_assets, "processed_assets": run.processed_assets,
        "failed_assets": run.failed_assets, "event_count": event_count,
        "requested_at": _iso(run.requested_at), "started_at": _iso(run.started_at),
        "finished_at": _iso(run.finished_at), "cancel_requested_at": _iso(run.cancel_requested_at),
        "error_message": run.error_message, "quality_badge": _quality_badge(run, event_count),
        "provenance": run.provenance,
    }


@router.get("/capabilities")
async def capabilities(user_id: UUID = Depends(get_current_user_id)) -> dict[str, Any]:
    del user_id
    return _envelope({
        "market": "Gate.io Spot",
        "capture_enabled": settings.PUMP_RADAR_CAPTURE_ENABLED,
        "analysis_enabled": settings.PUMP_RADAR_ANALYSIS_ENABLED,
        "ui_enabled": settings.PUMP_RADAR_UI_ENABLED,
        "timeframes": ["5m", "15m", "1h"],
        "exports": ["csv", "json"],
        "profile_mutation": False,
    }, provenance={"source": "runtime_settings"})


@router.get("/config/schema")
async def config_schema(user_id: UUID = Depends(get_current_user_id)) -> dict[str, Any]:
    del user_id
    return _envelope({"config_type": "pump_radar_v1", "json_schema": PumpRadarConfig.model_json_schema()})


@router.post("/runs", status_code=202)
async def create_run(payload: PumpRadarRunCreate, db: AsyncSession = Depends(get_db), user_id: UUID = Depends(get_current_user_id)) -> dict[str, Any]:
    _require_capture()
    raw_config = await config_service.get_config(db, "pump_radar_v1", user_id)
    config = PumpRadarConfig.model_validate(raw_config or {})
    now = datetime.now(timezone.utc)
    if payload.mode == "backfill":
        start = datetime.combine(payload.date_from, time.min, tzinfo=timezone.utc) if payload.date_from else now - timedelta(days=config.backfill_days)
    else:
        start = datetime.combine(payload.date_from, time.min, tzinfo=timezone.utc) if payload.date_from else now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = datetime.combine(payload.date_to, time.max, tzinfo=timezone.utc) if payload.date_to else now
    run = PumpRadarRun(
        user_id=user_id, status="QUEUED", mode=payload.mode, date_from=start, date_to=end,
        config_snapshot=config.model_dump(mode="json"), config_hash=config.digest(), timezone=config.timezone,
        provenance={"requested_by": str(user_id), "queue": QUEUE_PUMP_RADAR},
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    task_id = enqueue(
        "app.tasks.pump_radar.inventory", dedup_key=f"pump-radar:{run.id}:inventory",
        ttl_seconds=600, queue=QUEUE_PUMP_RADAR, args=(str(run.id),),
    )
    return _envelope({**_run_payload(run), "task_id": task_id})


@router.get("/runs")
async def list_runs(limit: int = Query(default=20, ge=1, le=100), db: AsyncSession = Depends(get_db), user_id: UUID = Depends(get_current_user_id)) -> dict[str, Any]:
    rows = (await db.execute(
        select(PumpRadarRun, func.count(PumpRadarEvent.id))
        .outerjoin(PumpRadarEvent, PumpRadarEvent.run_id == PumpRadarRun.id)
        .where(PumpRadarRun.user_id == user_id)
        .group_by(PumpRadarRun.id).order_by(PumpRadarRun.requested_at.desc()).limit(limit)
    )).all()
    return _envelope([_run_payload(run, count) for run, count in rows])


@router.get("/runs/{run_id}")
async def get_run(run_id: UUID, db: AsyncSession = Depends(get_db), user_id: UUID = Depends(get_current_user_id)) -> dict[str, Any]:
    run = await _owned_run(db, run_id, user_id)
    event_count = (await db.execute(select(func.count()).select_from(PumpRadarEvent).where(PumpRadarEvent.run_id == run_id))).scalar_one()
    shadow_count = (await db.execute(select(func.count()).select_from(PumpRadarEventLink).join(PumpRadarEvent).where(PumpRadarEvent.run_id == run_id, PumpRadarEventLink.user_id == user_id))).scalar_one()
    primary_count = (await db.execute(select(func.count()).select_from(PumpRadarEventLink).join(PumpRadarEvent).where(PumpRadarEvent.run_id == run_id, PumpRadarEventLink.user_id == user_id, PumpRadarEventLink.is_primary.is_(True)))).scalar_one()
    delays = (await db.execute(select(PumpRadarEventLink.delay_seconds).join(PumpRadarEvent).where(PumpRadarEvent.run_id == run_id, PumpRadarEventLink.user_id == user_id, PumpRadarEventLink.delay_seconds.is_not(None)))).scalars().all()
    median_delay = sorted(delays)[len(delays) // 2] if delays else None
    coverage_row = (await db.execute(select(func.min(PumpRadarRunAsset.coverage), func.avg(PumpRadarRunAsset.coverage)).where(PumpRadarRunAsset.run_id == run_id))).one()
    payload = _run_payload(run, event_count)
    if payload["quality_badge"] == "DADOS REAIS" and coverage_row[0] is not None and float(coverage_row[0]) < 1:
        payload["quality_badge"] = "COBERTURA PARCIAL"
    payload["coverage"] = {"minimum": _number(coverage_row[0]), "average": _number(coverage_row[1]), "assets": run.total_assets}
    return _envelope({**payload, "summary": {"pumps_identified": event_count, "with_shadow_entry": primary_count, "without_entry": max(event_count - primary_count, 0), "shadow_links": shadow_count, "median_delay_seconds": median_delay}})


@router.post("/runs/{run_id}/cancel", status_code=202)
async def cancel_run(run_id: UUID, db: AsyncSession = Depends(get_db), user_id: UUID = Depends(get_current_user_id)) -> dict[str, Any]:
    run = await _owned_run(db, run_id, user_id)
    if run.status in {"COMPLETED", "FAILED", "CANCELLED", "PARTIAL"}:
        return _envelope(_run_payload(run))
    run.cancel_requested_at = datetime.now(timezone.utc)
    run.status = "CANCELLING"
    await db.commit()
    return _envelope(_run_payload(run))


@router.get("/runs/{run_id}/assets")
async def list_assets(run_id: UUID, search: str | None = None, limit: int = Query(default=100, ge=1, le=500), offset: int = Query(default=0, ge=0), db: AsyncSession = Depends(get_db), user_id: UUID = Depends(get_current_user_id)) -> dict[str, Any]:
    await _owned_run(db, run_id, user_id)
    link_counts = select(PumpRadarEventLink.event_id, func.count(PumpRadarEventLink.id).label("links"), func.count(PumpRadarEventLink.entry_at).label("entries")).where(PumpRadarEventLink.user_id == user_id).group_by(PumpRadarEventLink.event_id).subquery()
    query = select(PumpRadarEvent, func.coalesce(link_counts.c.links, 0), func.coalesce(link_counts.c.entries, 0)).outerjoin(link_counts, link_counts.c.event_id == PumpRadarEvent.id).where(PumpRadarEvent.run_id == run_id)
    if search:
        query = query.where(PumpRadarEvent.symbol.ilike(f"%{search.strip()}%"))
    rows = (await db.execute(query.order_by(PumpRadarEvent.rise_pct.desc(), PumpRadarEvent.start_at).offset(offset).limit(limit))).all()
    data = [{
        "event_id": str(event.id), "symbol": event.symbol, "rise_pct": _number(event.rise_pct),
        "start_at": _iso(event.start_at), "confirmed_market_at": _iso(event.confirmed_market_at),
        "detected_at": _iso(event.detected_at), "peak_at": _iso(event.peak_at), "end_at": _iso(event.end_at),
        "reconstruction_status": event.reconstruction_status, "quality_status": event.quality_status,
        "shadow_links": links, "shadow_entries": entries,
    } for event, links, entries in rows]
    return _envelope({"items": data, "limit": limit, "offset": offset})


async def _owned_event(db: AsyncSession, run_id: UUID, event_id: UUID, user_id: UUID) -> PumpRadarEvent:
    await _owned_run(db, run_id, user_id)
    event = (await db.execute(select(PumpRadarEvent).where(PumpRadarEvent.id == event_id, PumpRadarEvent.run_id == run_id))).scalar_one_or_none()
    if event is None:
        raise HTTPException(status_code=404, detail="PUMP_RADAR_EVENT_NOT_FOUND")
    return event


@router.get("/runs/{run_id}/events/{event_id}")
async def get_event(run_id: UUID, event_id: UUID, db: AsyncSession = Depends(get_db), user_id: UUID = Depends(get_current_user_id)) -> dict[str, Any]:
    event = await _owned_event(db, run_id, event_id, user_id)
    links = (await db.execute(select(PumpRadarEventLink).where(PumpRadarEventLink.event_id == event_id, PumpRadarEventLink.user_id == user_id).order_by(PumpRadarEventLink.approval_at))).scalars().all()
    return _envelope({
        "id": str(event.id), "symbol": event.symbol, "start_at": _iso(event.start_at),
        "confirmed_market_at": _iso(event.confirmed_market_at), "detected_at": _iso(event.detected_at),
        "peak_at": _iso(event.peak_at), "end_at": _iso(event.end_at),
        "start_price": _number(event.start_price), "peak_price": _number(event.peak_price),
        "end_price": _number(event.end_price), "rise_pct": _number(event.rise_pct),
        "retracement_pct": _number(event.retracement_pct), "is_incomplete": event.is_incomplete,
        "reconstruction_status": event.reconstruction_status, "quality_status": event.quality_status,
        "provenance": event.provenance,
        "links": [{
            "id": str(link.id), "shadow_trade_id": str(link.shadow_trade_id) if link.shadow_trade_id else None,
            "decision_id": link.decision_id, "link_kind": link.link_kind, "is_primary": link.is_primary,
            "approval_at": _iso(link.approval_at), "simulation_created_at": _iso(link.simulation_created_at),
            "entry_at": _iso(link.entry_at), "exit_at": _iso(link.exit_at),
            "delay_seconds": link.delay_seconds, "realized_pnl_pct": _number(link.realized_pnl_pct),
            "profile_id": str(link.profile_id) if link.profile_id else None,
            "profile_version_id": str(link.profile_version_id) if link.profile_version_id else None,
            "profile_config_hash": link.profile_config_hash, "provenance": link.provenance,
        } for link in links],
    })


@router.get("/runs/{run_id}/events/{event_id}/chart")
async def get_chart(run_id: UUID, event_id: UUID, selected_at: datetime | None = None, hide_future: bool = True, db: AsyncSession = Depends(get_db), user_id: UUID = Depends(get_current_user_id)) -> dict[str, Any]:
    event = await _owned_event(db, run_id, event_id, user_id)
    context_start = event.start_at - timedelta(hours=8)
    natural_end = (event.end_at or event.confirmed_market_at) + timedelta(hours=2)
    cutoff = selected_at if hide_future and selected_at else natural_end
    result: dict[str, list[dict[str, Any]]] = {}
    for timeframe in ("1h", "15m", "5m"):
        rows = (await db.execute(select(PumpRadarOHLCV).where(
            PumpRadarOHLCV.symbol == event.symbol, PumpRadarOHLCV.timeframe == timeframe,
            PumpRadarOHLCV.open_time >= context_start, PumpRadarOHLCV.close_time <= min(natural_end, cutoff),
            PumpRadarOHLCV.is_closed.is_(True),
            # Known availability is enforced; absent availability is retained
            # only as visibly reconstructed history.
            (PumpRadarOHLCV.available_at.is_(None) | (PumpRadarOHLCV.available_at <= cutoff)),
        ).order_by(PumpRadarOHLCV.open_time))).scalars().all()
        result[timeframe] = [{
            "time": _iso(row.open_time), "close_time": _iso(row.close_time),
            "available_at": _iso(row.available_at), "open": _number(row.open), "high": _number(row.high),
            "low": _number(row.low), "close": _number(row.close), "volume": _number(row.volume_base),
            "quality_status": row.quality_status, "contract_version": row.contract_version,
        } for row in rows]
    links = (await db.execute(select(PumpRadarEventLink).where(PumpRadarEventLink.event_id == event_id, PumpRadarEventLink.user_id == user_id))).scalars().all()
    markers = [
        {"kind": "START", "at": _iso(event.start_at), "price": _number(event.start_price)},
        {"kind": "PEAK", "at": _iso(event.peak_at), "price": _number(event.peak_price)},
        {"kind": "END", "at": _iso(event.end_at), "price": _number(event.end_price)},
    ]
    for link in links:
        markers.extend([
            {"kind": "APPROVAL", "at": _iso(link.approval_at), "link_id": str(link.id)},
            {"kind": "ENTRY", "at": _iso(link.entry_at), "link_id": str(link.id)},
            {"kind": "EXIT", "at": _iso(link.exit_at), "link_id": str(link.id), "pnl_pct": _number(link.realized_pnl_pct)},
        ])
    return _envelope({"event_id": str(event.id), "symbol": event.symbol, "selected_at": _iso(cutoff), "hide_future": hide_future, "candles": result, "markers": [marker for marker in markers if marker.get("at")]}, provenance={"source": "pump_radar_ohlcv", "point_in_time": True, "reconstructed_history_possible": True})


@router.get("/runs/{run_id}/events/{event_id}/snapshots")
async def get_snapshots(run_id: UUID, event_id: UUID, at: datetime | None = None, db: AsyncSession = Depends(get_db), user_id: UUID = Depends(get_current_user_id)) -> dict[str, Any]:
    await _owned_event(db, run_id, event_id, user_id)
    query = select(PumpRadarIndicatorSnapshot).where(PumpRadarIndicatorSnapshot.event_id == event_id, PumpRadarIndicatorSnapshot.user_id == user_id)
    if at:
        query = query.where(PumpRadarIndicatorSnapshot.snapshot_at <= at)
    snapshots = (await db.execute(query.order_by(PumpRadarIndicatorSnapshot.snapshot_at))).scalars().all()
    data = []
    for snapshot in snapshots:
        values = (await db.execute(select(PumpRadarIndicatorValue).where(PumpRadarIndicatorValue.snapshot_id == snapshot.id).order_by(PumpRadarIndicatorValue.layer, PumpRadarIndicatorValue.indicator_id))).scalars().all()
        data.append({"id": str(snapshot.id), "snapshot_at": _iso(snapshot.snapshot_at), "source_priority": snapshot.source_priority, "state": snapshot.state, "schema_version": snapshot.schema_version, "feature_engine_version": snapshot.feature_engine_version, "coverage": _number(snapshot.coverage), "provenance": snapshot.provenance, "values": [{"indicator_id": value.indicator_id, "layer": value.layer, "timeframe": value.timeframe, "state": value.state, "numeric_value": _number(value.numeric_value), "text_value": value.text_value, "rule": value.rule, "numerator": value.numerator, "denominator": value.denominator, "source": value.source, "version": value.version, "provenance": value.provenance} for value in values]})
    return _envelope(data)


@router.get("/runs/{run_id}/comparisons")
async def comparisons(run_id: UUID, event_id: UUID | None = None, layer: str | None = None, db: AsyncSession = Depends(get_db), user_id: UUID = Depends(get_current_user_id)) -> dict[str, Any]:
    await _owned_run(db, run_id, user_id)
    query = select(PumpRadarEvent, PumpRadarIndicatorSnapshot, PumpRadarIndicatorValue).join(PumpRadarIndicatorSnapshot, PumpRadarIndicatorSnapshot.event_id == PumpRadarEvent.id).join(PumpRadarIndicatorValue, PumpRadarIndicatorValue.snapshot_id == PumpRadarIndicatorSnapshot.id).where(PumpRadarEvent.run_id == run_id, PumpRadarIndicatorSnapshot.user_id == user_id)
    if event_id:
        query = query.where(PumpRadarEvent.id == event_id)
    if layer:
        query = query.where(PumpRadarIndicatorValue.layer == layer.upper())
    rows = (await db.execute(query.order_by(PumpRadarIndicatorValue.layer, PumpRadarIndicatorValue.indicator_id, PumpRadarIndicatorSnapshot.snapshot_at))).all()
    return _envelope([{"event_id": str(event.id), "symbol": event.symbol, "snapshot_at": _iso(snapshot.snapshot_at), "indicator_id": value.indicator_id, "layer": value.layer, "timeframe": value.timeframe, "state": value.state, "value": _number(value.numeric_value) if value.numeric_value is not None else value.text_value, "rule": value.rule, "source": value.source, "version": value.version, "numerator": value.numerator, "denominator": value.denominator, "coverage": _number(snapshot.coverage), "provenance": value.provenance} for event, snapshot, value in rows])


@router.get("/runs/{run_id}/ranges")
async def ranges(run_id: UUID, timeframe: str | None = None, db: AsyncSession = Depends(get_db), user_id: UUID = Depends(get_current_user_id)) -> dict[str, Any]:
    await _owned_run(db, run_id, user_id)
    query = select(PumpRadarRangeResult).where(PumpRadarRangeResult.run_id == run_id)
    if timeframe and timeframe != "combined":
        query = query.where(PumpRadarRangeResult.timeframe == timeframe)
    rows = (await db.execute(query.order_by(PumpRadarRangeResult.validation_status.desc(), PumpRadarRangeResult.indicator_id))).scalars().all()
    return _envelope([{"id": str(row.id), "indicator_id": row.indicator_id, "layer": row.layer, "timeframe": row.timeframe, "range_key": row.range_key, "lower_bound": _number(row.lower_bound), "upper_bound": _number(row.upper_bound), "pump_numerator": row.pump_numerator, "pump_denominator": row.pump_denominator, "control_numerator": row.control_numerator, "control_denominator": row.control_denominator, "coverage": _number(row.coverage), "difference": _number(row.difference), "ratio": _number(row.ratio), "confidence_interval": row.confidence_interval, "validation_status": row.validation_status, "discovery_boundary": _iso(row.discovery_boundary), "provenance": row.provenance} for row in rows])


@router.get("/runs/{run_id}/profiles")
async def profiles(run_id: UUID, db: AsyncSession = Depends(get_db), user_id: UUID = Depends(get_current_user_id)) -> dict[str, Any]:
    await _owned_run(db, run_id, user_id)
    rows = (await db.execute(select(PumpRadarEventLink.profile_id, PumpRadarEventLink.profile_version_id, PumpRadarEventLink.profile_config_hash, func.count()).join(PumpRadarEvent).where(PumpRadarEvent.run_id == run_id, PumpRadarEventLink.user_id == user_id).group_by(PumpRadarEventLink.profile_id, PumpRadarEventLink.profile_version_id, PumpRadarEventLink.profile_config_hash))).all()
    return _envelope([{"profile_id": str(profile_id) if profile_id else None, "profile_version_id": str(version_id) if version_id else None, "profile_config_hash": config_hash, "link_count": count} for profile_id, version_id, config_hash, count in rows])


ALIASES = {"orderbook_pressure": "bid_ask_imbalance"}


@router.post("/runs/{run_id}/hypotheses", status_code=201)
async def create_hypothesis(run_id: UUID, payload: PumpRadarHypothesisCreate, db: AsyncSession = Depends(get_db), user_id: UUID = Depends(get_current_user_id)) -> dict[str, Any]:
    await _owned_run(db, run_id, user_id)
    canonical: set[str] = set()
    for condition in payload.conditions:
        indicator = condition.get("indicator_id")
        if not isinstance(indicator, str) or not indicator:
            raise HTTPException(status_code=422, detail="Each condition requires indicator_id")
        key = ALIASES.get(indicator, indicator)
        if key in canonical:
            raise HTTPException(status_code=422, detail="Alias or redundant indicator condition")
        canonical.add(key)
    hypothesis = PumpRadarHypothesis(
        run_id=run_id, user_id=user_id, title=payload.title, status="CANDIDATE",
        conditions=payload.conditions,
        profile_id=UUID(payload.profile_id) if payload.profile_id else None,
        profile_version_id=UUID(payload.profile_version_id) if payload.profile_version_id else None,
        profile_config_hash=payload.profile_config_hash,
        provenance={"allowed_actions": ["candidate", "replay", "export"], "profile_mutation": False},
    )
    db.add(hypothesis)
    await db.commit()
    await db.refresh(hypothesis)
    return _envelope({"id": str(hypothesis.id), "status": hypothesis.status, "conditions": hypothesis.conditions, "provenance": hypothesis.provenance})


@router.get("/runs/{run_id}/export")
async def export_run(run_id: UUID, dataset: str = Query(pattern="^(assets|events|comparisons|ranges)$"), format: str = Query(pattern="^(csv|json)$"), db: AsyncSession = Depends(get_db), user_id: UUID = Depends(get_current_user_id)):
    await _owned_run(db, run_id, user_id)
    if dataset in {"assets", "events"}:
        rows = (await db.execute(select(PumpRadarEvent).where(PumpRadarEvent.run_id == run_id).order_by(PumpRadarEvent.start_at))).scalars().all()
        data = [{"event_id": str(row.id), "symbol": row.symbol, "start_at": _iso(row.start_at), "confirmed_market_at": _iso(row.confirmed_market_at), "detected_at": _iso(row.detected_at), "peak_at": _iso(row.peak_at), "end_at": _iso(row.end_at), "rise_pct": _number(row.rise_pct), "reconstruction_status": row.reconstruction_status, "quality_status": row.quality_status} for row in rows]
    elif dataset == "ranges":
        rows = (await db.execute(select(PumpRadarRangeResult).where(PumpRadarRangeResult.run_id == run_id))).scalars().all()
        data = [{"indicator_id": row.indicator_id, "timeframe": row.timeframe, "range_key": row.range_key, "pump_numerator": row.pump_numerator, "pump_denominator": row.pump_denominator, "control_numerator": row.control_numerator, "control_denominator": row.control_denominator, "validation_status": row.validation_status} for row in rows]
    else:
        rows = (await db.execute(select(PumpRadarEvent.symbol, PumpRadarIndicatorSnapshot.snapshot_at, PumpRadarIndicatorValue.indicator_id, PumpRadarIndicatorValue.layer, PumpRadarIndicatorValue.timeframe, PumpRadarIndicatorValue.state, PumpRadarIndicatorValue.numeric_value, PumpRadarIndicatorValue.text_value, PumpRadarIndicatorValue.source, PumpRadarIndicatorValue.version).join(PumpRadarIndicatorSnapshot, PumpRadarIndicatorSnapshot.event_id == PumpRadarEvent.id).join(PumpRadarIndicatorValue, PumpRadarIndicatorValue.snapshot_id == PumpRadarIndicatorSnapshot.id).where(PumpRadarEvent.run_id == run_id, PumpRadarIndicatorSnapshot.user_id == user_id))).all()
        data = [{"symbol": row[0], "snapshot_at": _iso(row[1]), "indicator_id": row[2], "layer": row[3], "timeframe": row[4], "state": row[5], "value": _number(row[6]) if row[6] is not None else row[7], "source": row[8], "version": row[9]} for row in rows]
    if format == "json":
        return _envelope(data, provenance={"source": dataset, "run_id": str(run_id)})
    buffer = io.StringIO()
    if data:
        writer = csv.DictWriter(buffer, fieldnames=list(data[0].keys()))
        writer.writeheader()
        writer.writerows(data)
    return StreamingResponse(iter([buffer.getvalue()]), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="pump-radar-{run_id}-{dataset}.csv"'})
