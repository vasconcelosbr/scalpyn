"""Descriptive (non-validated) indicator commonality across a Pump Radar selection.

Shared by the live UI endpoint (`/runs/{run_id}/indicator-summary`), the
materialized report export, and the governed AI module reader
(`ModuleAIAnalysisService`) -- all three must see the same numbers for the
same event_ids, so the aggregation lives in one place.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.pump_radar import (
    PumpRadarEvent,
    PumpRadarEventLink,
    PumpRadarIndicatorSnapshot,
    PumpRadarIndicatorValue,
)

_SOURCE_PRIORITY = {"DECISION_SNAPSHOT": 0, "EVALUATION_ENVELOPE": 1}


def _number(value: Any) -> float | int | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return value


async def build_indicator_summary(db: AsyncSession, run_id: UUID, event_ids: list[UUID], user_id: UUID) -> dict[str, Any]:
    """Descriptive (non-validated) commonality of indicators across a selection of events.

    Unlike `/runs/{run_id}/ranges` (a discovery/validation statistical table
    computed once for the whole run against matched controls), this groups
    whatever event_ids the caller selected -- one event's shadow entries, or
    every event currently listed -- with no control population and no
    significance test. It is meant to be read as "what these entries had in
    common", not as a validated hypothesis.
    """
    events = (await db.execute(select(PumpRadarEvent).where(PumpRadarEvent.id.in_(event_ids), PumpRadarEvent.run_id == run_id))).scalars().all()
    events_by_id = {event.id: event for event in events}
    found_ids = list(events_by_id)
    if not found_ids:
        return {"event_count": 0, "link_count": 0, "indicators": []}
    links = (await db.execute(select(PumpRadarEventLink).where(PumpRadarEventLink.event_id.in_(found_ids), PumpRadarEventLink.user_id == user_id))).scalars().all()
    links_by_event: dict[UUID, list[PumpRadarEventLink]] = {}
    for link in links:
        links_by_event.setdefault(link.event_id, []).append(link)
    rows = (await db.execute(
        select(
            PumpRadarIndicatorSnapshot.event_id, PumpRadarIndicatorSnapshot.snapshot_at, PumpRadarIndicatorSnapshot.state,
            PumpRadarIndicatorValue.indicator_id, PumpRadarIndicatorValue.layer, PumpRadarIndicatorValue.timeframe,
            PumpRadarIndicatorValue.numeric_value, PumpRadarIndicatorValue.text_value, PumpRadarIndicatorValue.source,
        )
        .join(PumpRadarIndicatorValue, PumpRadarIndicatorValue.snapshot_id == PumpRadarIndicatorSnapshot.id)
        .where(PumpRadarIndicatorSnapshot.event_id.in_(found_ids), PumpRadarIndicatorSnapshot.user_id == user_id)
    )).all()
    by_key: dict[tuple[Any, ...], list[tuple[int, Any]]] = {}
    for event_id, snapshot_at, state, indicator_id, layer, timeframe, numeric_value, text_value, source in rows:
        if state == "UNAVAILABLE":
            continue
        key = (event_id, layer, timeframe, indicator_id, snapshot_at)
        value = _number(numeric_value) if numeric_value is not None else text_value
        by_key.setdefault(key, []).append((_SOURCE_PRIORITY.get(source, 2), value))

    def value_at(event_id: UUID, layer: str, timeframe: str, indicator_id: str, at: datetime | None) -> Any:
        if at is None:
            return None
        candidates = by_key.get((event_id, layer, timeframe, indicator_id, at))
        if not candidates:
            return None
        return sorted(candidates, key=lambda item: item[0])[0][1]

    indicator_keys = sorted({(layer, timeframe, indicator_id) for (_, layer, timeframe, indicator_id, _) in by_key})
    indicators = []
    for layer, timeframe, indicator_id in indicator_keys:
        anchors: dict[str, list[dict[str, Any]]] = {"before": [], "start": [], "approval": [], "entry": []}
        for event_id in found_ids:
            event = events_by_id[event_id]
            before_value = value_at(event_id, layer, timeframe, indicator_id, event.start_at - timedelta(minutes=5))
            if before_value is not None:
                anchors["before"].append({"event_id": str(event_id), "symbol": event.symbol, "value": before_value})
            start_value = value_at(event_id, layer, timeframe, indicator_id, event.start_at)
            if start_value is not None:
                anchors["start"].append({"event_id": str(event_id), "symbol": event.symbol, "value": start_value})
            for link in links_by_event.get(event_id, []):
                approval_value = value_at(event_id, layer, timeframe, indicator_id, link.approval_at)
                if approval_value is not None:
                    anchors["approval"].append({"event_id": str(event_id), "link_id": str(link.id), "symbol": event.symbol, "value": approval_value})
                entry_value = value_at(event_id, layer, timeframe, indicator_id, link.entry_at)
                if entry_value is not None:
                    anchors["entry"].append({"event_id": str(event_id), "link_id": str(link.id), "symbol": event.symbol, "value": entry_value})
        summary_anchors = {}
        for anchor_name, samples in anchors.items():
            numeric_samples = [sample["value"] for sample in samples if isinstance(sample["value"], (int, float))]
            summary_anchors[anchor_name] = {
                "count": len(samples),
                "numeric_coverage": len(numeric_samples),
                "min": min(numeric_samples) if numeric_samples else None,
                "max": max(numeric_samples) if numeric_samples else None,
                "samples": samples,
            }
        indicators.append({"layer": layer, "timeframe": timeframe, "indicator_id": indicator_id, "anchors": summary_anchors})
    return {
        "event_count": len(found_ids),
        "link_count": sum(len(value) for value in links_by_event.values()),
        "indicators": indicators,
    }
