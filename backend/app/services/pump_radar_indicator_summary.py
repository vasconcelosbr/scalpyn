"""Descriptive (non-validated) indicator commonality across a Pump Radar selection.

Shared by the live UI endpoint (`/runs/{run_id}/indicator-summary`), the
materialized report export, and the governed AI module reader
(`ModuleAIAnalysisService`) -- all three must see the same numbers for the
same event_ids, so the aggregation lives in one place.
"""

from __future__ import annotations

from dataclasses import dataclass
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
_IDENTITY_ELIGIBLE_SOURCE_PRIORITY = "DECISION_SNAPSHOT"

# Sentinel distinct from None: resolution found competing, equally-ranked
# snapshot versions with no persisted reference to break the tie. Must not be
# treated as "no data" (None) nor resolved by picking latest/first.
_AMBIGUOUS = object()


def _number(value: Any) -> float | int | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return value


def _normalize_identity(raw: Any) -> str | None:
    """Canonical string form of a shadow_trade_id, or None if absent/malformed.

    Absent and malformed both resolve to None -- neither may ever produce a
    match; a malformed value must not be treated as a permissive "legacy, no
    identity" match either, so it is excluded from candidacy the same way.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        return str(UUID(text))
    except (ValueError, AttributeError, TypeError):
        return None


@dataclass(frozen=True, slots=True)
class _Candidate:
    snapshot_id: Any
    value: Any
    snapshot_at: datetime
    priority: int
    shadow_trade_id: str | None


async def build_indicator_summary(db: AsyncSession, run_id: UUID, event_ids: list[UUID], user_id: UUID) -> dict[str, Any]:
    """Descriptive (non-validated) commonality of indicators across a selection of events.

    Unlike `/runs/{run_id}/ranges` (a discovery/validation statistical table
    computed once for the whole run against matched controls), this groups
    whatever event_ids the caller selected -- one event's shadow entries, or
    every event currently listed -- with no control population and no
    significance test. It is meant to be read as "what these entries had in
    common", not as a validated hypothesis.

    `before`/`start` anchors are resolved by exact-timestamp match only, as
    before. `approval`/`entry` anchors additionally resolve by the shadow
    trade's persisted identity (`PumpRadarEventLink.shadow_trade_id` against
    `PumpRadarIndicatorSnapshot.provenance["shadow_trade_id"]`, DECISION_SNAPSHOT
    sources only) with `snapshot_at <= anchor_at`, since the correct snapshot
    is frequently captured a moment before or after the anchor rather than at
    the exact same instant. A DECISION_SNAPSHOT known to belong to a different
    shadow trade is never used for a link, even on an exact timestamp match.
    Competing, equally-ranked snapshot versions with no way to disambiguate
    are reported as absent (with a separate ambiguous-count), never resolved
    by picking the latest or first.
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
            PumpRadarIndicatorSnapshot.id, PumpRadarIndicatorSnapshot.event_id, PumpRadarIndicatorSnapshot.snapshot_at,
            PumpRadarIndicatorSnapshot.state, PumpRadarIndicatorSnapshot.source_priority, PumpRadarIndicatorSnapshot.provenance,
            PumpRadarIndicatorValue.indicator_id, PumpRadarIndicatorValue.layer, PumpRadarIndicatorValue.timeframe,
            PumpRadarIndicatorValue.numeric_value, PumpRadarIndicatorValue.text_value, PumpRadarIndicatorValue.source,
        )
        .join(PumpRadarIndicatorValue, PumpRadarIndicatorValue.snapshot_id == PumpRadarIndicatorSnapshot.id)
        .where(PumpRadarIndicatorSnapshot.event_id.in_(found_ids), PumpRadarIndicatorSnapshot.user_id == user_id)
    )).all()

    by_timestamp: dict[tuple[Any, ...], list[_Candidate]] = {}
    by_identity: dict[tuple[Any, ...], list[_Candidate]] = {}
    for snapshot_id, event_id, snapshot_at, state, source_priority, provenance, indicator_id, layer, timeframe, numeric_value, text_value, source in rows:
        if state == "UNAVAILABLE":
            continue
        value = _number(numeric_value) if numeric_value is not None else text_value
        shadow_trade_id = None
        if source_priority == _IDENTITY_ELIGIBLE_SOURCE_PRIORITY and isinstance(provenance, dict):
            shadow_trade_id = _normalize_identity(provenance.get("shadow_trade_id"))
        candidate = _Candidate(
            snapshot_id=snapshot_id, value=value, snapshot_at=snapshot_at,
            priority=_SOURCE_PRIORITY.get(source, 2), shadow_trade_id=shadow_trade_id,
        )
        by_timestamp.setdefault((event_id, layer, timeframe, indicator_id, snapshot_at), []).append(candidate)
        if shadow_trade_id is not None:
            by_identity.setdefault((event_id, layer, timeframe, indicator_id, shadow_trade_id), []).append(candidate)

    def value_at(event_id: UUID, layer: str, timeframe: str, indicator_id: str, at: datetime | None) -> Any:
        if at is None:
            return None
        candidates = by_timestamp.get((event_id, layer, timeframe, indicator_id, at))
        if not candidates:
            return None
        return sorted(candidates, key=lambda c: c.priority)[0].value

    def resolve_anchor(
        event_id: UUID, layer: str, timeframe: str, indicator_id: str,
        anchor_at: datetime | None, link_shadow_trade_id: str | None,
    ) -> dict[str, Any] | object | None:
        if anchor_at is None:
            return None

        identity_candidates: list[_Candidate] = []
        if link_shadow_trade_id is not None:
            identity_candidates = [
                c for c in by_identity.get((event_id, layer, timeframe, indicator_id, link_shadow_trade_id), [])
                if c.snapshot_at <= anchor_at
            ]

        timestamp_candidates = by_timestamp.get((event_id, layer, timeframe, indicator_id, anchor_at), [])
        if link_shadow_trade_id is not None:
            # A DECISION_SNAPSHOT known to belong to a different shadow trade is
            # never reintroduced through the timestamp path, even on an exact match.
            timestamp_candidates = [
                c for c in timestamp_candidates
                if c.shadow_trade_id is None or c.shadow_trade_id == link_shadow_trade_id
            ]

        pool: dict[Any, tuple[_Candidate, str]] = {}
        for c in identity_candidates:
            pool[c.snapshot_id] = (c, "shadow_trade_id")
        for c in timestamp_candidates:
            pool.setdefault(c.snapshot_id, (c, "exact_timestamp"))

        if not pool:
            return None

        best_priority = min(c.priority for c, _ in pool.values())
        winners = [(c, tag) for c, tag in pool.values() if c.priority == best_priority]
        if len({c.snapshot_id for c, _ in winners}) > 1:
            return _AMBIGUOUS
        winner, matched_by = winners[0]
        return {
            "value": winner.value,
            "matched_by": matched_by,
            "source_snapshot_id": str(winner.snapshot_id),
            "source_snapshot_at": winner.snapshot_at.isoformat() if winner.snapshot_at else None,
        }

    indicator_keys = sorted({(layer, timeframe, indicator_id) for (_, layer, timeframe, indicator_id, _) in by_timestamp})
    indicators = []
    for layer, timeframe, indicator_id in indicator_keys:
        anchors: dict[str, list[dict[str, Any]]] = {"before": [], "start": [], "approval": [], "entry": []}
        ambiguous_counts = {"approval": 0, "entry": 0}
        for event_id in found_ids:
            event = events_by_id[event_id]
            before_value = value_at(event_id, layer, timeframe, indicator_id, event.start_at - timedelta(minutes=5))
            if before_value is not None:
                anchors["before"].append({"event_id": str(event_id), "symbol": event.symbol, "value": before_value})
            start_value = value_at(event_id, layer, timeframe, indicator_id, event.start_at)
            if start_value is not None:
                anchors["start"].append({"event_id": str(event_id), "symbol": event.symbol, "value": start_value})
            for link in links_by_event.get(event_id, []):
                link_shadow_trade_id = _normalize_identity(getattr(link, "shadow_trade_id", None))
                for anchor_name, anchor_at in (("approval", link.approval_at), ("entry", link.entry_at)):
                    resolved = resolve_anchor(event_id, layer, timeframe, indicator_id, anchor_at, link_shadow_trade_id)
                    if resolved is _AMBIGUOUS:
                        ambiguous_counts[anchor_name] += 1
                        continue
                    if resolved is None:
                        continue
                    anchors[anchor_name].append({
                        "event_id": str(event_id), "link_id": str(link.id), "symbol": event.symbol,
                        "value": resolved["value"], "matched_by": resolved["matched_by"],
                        "source_snapshot_id": resolved["source_snapshot_id"], "source_snapshot_at": resolved["source_snapshot_at"],
                    })
        summary_anchors = {}
        for anchor_name, samples in anchors.items():
            numeric_samples = [sample["value"] for sample in samples if isinstance(sample["value"], (int, float))]
            entry: dict[str, Any] = {
                "count": len(samples),
                "numeric_coverage": len(numeric_samples),
                "min": min(numeric_samples) if numeric_samples else None,
                "max": max(numeric_samples) if numeric_samples else None,
                "samples": samples,
            }
            if anchor_name in ambiguous_counts:
                entry["ambiguous_count"] = ambiguous_counts[anchor_name]
            summary_anchors[anchor_name] = entry
        indicators.append({"layer": layer, "timeframe": timeframe, "indicator_id": indicator_id, "anchors": summary_anchors})
    return {
        "event_count": len(found_ids),
        "link_count": sum(len(value) for value in links_by_event.values()),
        "indicators": indicators,
    }
