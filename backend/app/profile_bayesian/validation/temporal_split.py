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
    embargo_seconds: int


def derive_embargo_seconds(
    observations: Sequence[CanonicalObservation],
    *,
    minimum_embargo_seconds: int,
    max_feature_lookback_seconds: int,
) -> int:
    """Derive purge/embargo from the observed target horizon and feature lookback."""

    if minimum_embargo_seconds < 0 or max_feature_lookback_seconds < 0:
        raise ValueError("embargo inputs cannot be negative")
    horizons = [
        int(item.target_horizon_seconds)
        for item in observations
        if item.target_horizon_seconds is not None
        and item.target_horizon_seconds > 0
    ]
    if not horizons:
        raise ValueError("target horizon is unavailable for embargo derivation")
    return max(
        minimum_embargo_seconds,
        max(horizons) + max_feature_lookback_seconds,
    )


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
    if len(ordered) < 3:
        raise ValueError("temporal split requires at least three observations")
    first_at = ordered[0].occurred_at
    last_at = ordered[-1].occurred_at
    duration = last_at - first_at
    if duration.total_seconds() <= 0:
        raise ValueError("temporal split requires a positive time span")
    first_cut_at = first_at + duration * discovery_fraction
    second_cut_at = first_at + duration * (
        discovery_fraction + validation_fraction
    )
    embargo = timedelta(seconds=embargo_seconds)
    discovery = tuple(item for item in ordered if item.occurred_at < first_cut_at)
    if not discovery:
        raise ValueError("discovery window is empty")
    validation_start = first_cut_at + embargo
    validation_candidates = [
        item
        for item in ordered
        if validation_start <= item.occurred_at < second_cut_at
    ]
    if not validation_candidates:
        raise ValueError("embargo removed the validation window")
    holdout_start = second_cut_at + embargo
    holdout = [item for item in ordered if item.occurred_at >= holdout_start]
    if not holdout:
        raise ValueError("embargo removed the final holdout")
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
        embargo_seconds=embargo_seconds,
    )
