"""Deterministic Pareto-like ordering for persisted valid trials."""

from __future__ import annotations

from typing import Any, Iterable, Mapping


def rank_candidates(
    trials: Iterable[Mapping[str, Any]], *, max_candidates: int
) -> list[Mapping[str, Any]]:
    valid = [item for item in trials if item.get("is_valid") is True]
    valid.sort(
        key=lambda item: (
            -float(item["metrics"]["robust_score"]),
            int(item["metrics"].get("changed_parameters", 0)),
            str(item.get("id") or ""),
        )
    )
    return valid[:max_candidates]
