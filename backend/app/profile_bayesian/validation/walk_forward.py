"""Deterministic expanding-window walk-forward generator."""

from __future__ import annotations

from typing import Iterator, Sequence

from ..data_contract import CanonicalObservation


def walk_forward_windows(
    observations: Sequence[CanonicalObservation],
    *,
    initial_train_size: int,
    validation_size: int,
    step_size: int,
) -> Iterator[tuple[tuple[CanonicalObservation, ...], tuple[CanonicalObservation, ...]]]:
    ordered = sorted(observations, key=lambda item: (item.occurred_at, item.observation_id))
    train_end = initial_train_size
    while train_end + validation_size <= len(ordered):
        yield (
            tuple(ordered[:train_end]),
            tuple(ordered[train_end : train_end + validation_size]),
        )
        train_end += step_size
