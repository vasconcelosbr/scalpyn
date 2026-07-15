"""Grouped temporal split with purge at both boundaries and final embargo."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Hashable, Sequence


@dataclass(frozen=True)
class TemporalObservation:
    row_id: Hashable
    group_id: Hashable
    observed_at: datetime
    label_resolved_at: datetime
    trade_id: Hashable | None = None


@dataclass(frozen=True)
class PurgedSplit:
    train: tuple[TemporalObservation, ...]
    validation: tuple[TemporalObservation, ...]
    test: tuple[TemporalObservation, ...]
    diagnostics: dict[str, int]


def grouped_purged_split(
    rows: Sequence[TemporalObservation],
    *,
    validation_start: datetime,
    test_start: datetime,
    label_horizon: timedelta,
    embargo: timedelta,
) -> PurgedSplit:
    if validation_start >= test_start:
        raise ValueError("validation_start_must_precede_test_start")
    ordered = sorted(rows, key=lambda row: (row.observed_at, str(row.row_id)))
    raw_train = [row for row in ordered if row.observed_at < validation_start]
    raw_validation = [row for row in ordered if validation_start <= row.observed_at < test_start]
    raw_test = [row for row in ordered if row.observed_at >= test_start + embargo]

    # Purge label horizons crossing either future boundary.
    label_safe_train = [
        row for row in raw_train if row.label_resolved_at < validation_start
    ]
    label_safe_validation = [
        row for row in raw_validation if row.label_resolved_at < test_start
    ]
    test = list(raw_test)

    # Groups belong to the latest split in which they occur; earlier copies are removed.
    test_groups = {row.group_id for row in test}
    validation = [
        row for row in label_safe_validation if row.group_id not in test_groups
    ]
    validation_groups = {row.group_id for row in validation}
    train = [
        row
        for row in label_safe_train
        if row.group_id not in test_groups | validation_groups
    ]

    diagnostics = split_diagnostics(train, validation, test)
    diagnostics.update({
        "purged_train": len(raw_train) - len(train),
        "purged_validation": len(raw_validation) - len(validation),
        "label_purged_train": len(raw_train) - len(label_safe_train),
        "label_purged_validation": len(raw_validation) - len(label_safe_validation),
        "group_purged_train": len(label_safe_train) - len(train),
        "group_purged_validation": len(label_safe_validation) - len(validation),
        "embargoed_test": len([row for row in ordered if test_start <= row.observed_at < test_start + embargo]),
    })
    if any(diagnostics[key] for key in ("group_overlap", "trade_overlap", "label_horizon_overlap")):
        raise AssertionError(f"invalid_purged_split:{diagnostics}")
    return PurgedSplit(tuple(train), tuple(validation), tuple(test), diagnostics)


def split_diagnostics(train, validation, test) -> dict[str, int]:
    group_sets = [{row.group_id for row in split} for split in (train, validation, test)]
    trade_sets = [{row.trade_id for row in split if row.trade_id is not None} for split in (train, validation, test)]
    group_overlap = sum(len(group_sets[a] & group_sets[b]) for a, b in ((0, 1), (0, 2), (1, 2)))
    trade_overlap = sum(len(trade_sets[a] & trade_sets[b]) for a, b in ((0, 1), (0, 2), (1, 2)))
    label_overlap = 0
    if train and validation:
        label_overlap += sum(row.label_resolved_at >= min(x.observed_at for x in validation) for row in train)
    if validation and test:
        label_overlap += sum(row.label_resolved_at >= min(x.observed_at for x in test) for row in validation)
    return {"group_overlap": group_overlap, "trade_overlap": trade_overlap, "label_horizon_overlap": label_overlap}
