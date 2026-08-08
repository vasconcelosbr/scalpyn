"""Lossless canonical event reconciliation for analysis datasets.

Conflicting outcomes are never guessed away. Every source observation remains
in history and an explicit, actor-attributed resolution is required before a
proposal/candidate dataset may pass its quality gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from .hashing import canonical_hash


@dataclass(frozen=True)
class CanonicalEventObservation:
    source_event_id: str
    event_identity: str
    outcome: str
    observed_at: datetime


@dataclass(frozen=True)
class HumanEventResolution:
    event_identity: str
    selected_source_event_id: str
    actor_user_id: str
    reason: str
    resolved_at: datetime


def reconcile_canonical_events(
    observations: Iterable[CanonicalEventObservation],
    resolutions: Iterable[HumanEventResolution] = (),
) -> list[dict]:
    grouped: dict[str, list[CanonicalEventObservation]] = {}
    for observation in observations:
        grouped.setdefault(observation.event_identity, []).append(observation)
    resolution_map = {resolution.event_identity: resolution for resolution in resolutions}
    result: list[dict] = []
    for identity in sorted(grouped):
        history = sorted(grouped[identity], key=lambda item: (item.observed_at, item.source_event_id))
        outcomes = sorted({item.outcome for item in history})
        resolution = resolution_map.get(identity)
        chosen = None
        if resolution:
            chosen = next(
                (item for item in history if item.source_event_id == resolution.selected_source_event_id),
                None,
            )
            if chosen is None:
                raise ValueError("CANONICAL_EVENT_RESOLUTION_SOURCE_NOT_FOUND")
        unresolved = len(outcomes) > 1 and chosen is None
        result.append({
            "event_identity": identity,
            "history": [{
                "source_event_id": item.source_event_id,
                "outcome": item.outcome,
                "observed_at": item.observed_at.astimezone(timezone.utc).isoformat(),
            } for item in history],
            "history_hash": canonical_hash([
                (item.source_event_id, item.outcome, item.observed_at.astimezone(timezone.utc).isoformat())
                for item in history
            ]),
            "conflict": len(outcomes) > 1,
            "quality_status": "BLOCK_CONFLICTING_OUTCOMES" if unresolved else "PASS",
            "canonical_outcome": chosen.outcome if chosen else (outcomes[0] if len(outcomes) == 1 else None),
            "resolution": None if resolution is None else {
                "selected_source_event_id": resolution.selected_source_event_id,
                "actor_user_id": resolution.actor_user_id,
                "reason": resolution.reason,
                "resolved_at": resolution.resolved_at.astimezone(timezone.utc).isoformat(),
            },
        })
    return result
