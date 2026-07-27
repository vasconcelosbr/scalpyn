"""Purged temporal discovery/validation/final-holdout split."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Sequence

from ..data_contract import CanonicalObservation


@dataclass(frozen=True)
class TemporalSplit:
    discovery: tuple[CanonicalObservation, ...]
    validation: tuple[CanonicalObservation, ...]
    final_holdout: tuple[CanonicalObservation, ...]
    windows: dict[str, tuple[datetime, datetime]]


def purged_temporal_split(
    observations: Sequence[CanonicalObservation],
    *,
    discovery_fraction: float,
    validation_fraction: float,
    embargo_seconds: int,
) -> TemporalSplit:
    ordered = sorted(observations, key=lambda item: (item.occurred_at, item.observation_id))
    if not 0 < discovery_fraction < 1 or not 0 < validation_fraction < 1:
        raise ValueError("fractions must be between zero and one")
    if discovery_fraction + validation_fraction >= 1:
        raise ValueError("final holdout must remain non-empty")
    first_cut = max(1, int(len(ordered) * discovery_fraction))
    second_cut = max(first_cut + 1, int(len(ordered) * (discovery_fraction + validation_fraction)))
    embargo = timedelta(seconds=embargo_seconds)
    discovery_end = ordered[first_cut - 1].occurred_at
    validation_start = discovery_end + embargo
    validation_candidates = [item for item in ordered[first_cut:second_cut] if item.occurred_at >= validation_start]
    if not validation_candidates:
        raise ValueError("embargo removed the validation window")
    validation_end = validation_candidates[-1].occurred_at
    holdout_start = validation_end + embargo
    holdout = [item for item in ordered[second_cut:] if item.occurred_at >= holdout_start]
    if not holdout:
        raise ValueError("embargo removed the final holdout")
    discovery = tuple(ordered[:first_cut])
    validation = tuple(validation_candidates)
    final_holdout = tuple(holdout)
    return TemporalSplit(
        discovery=discovery,
        validation=validation,
        final_holdout=final_holdout,
        windows={
            "discovery": (discovery[0].occurred_at, discovery[-1].occurred_at),
            "validation": (validation[0].occurred_at, validation[-1].occurred_at),
            "final_holdout": (final_holdout[0].occurred_at, final_holdout[-1].occurred_at),
        },
    )
