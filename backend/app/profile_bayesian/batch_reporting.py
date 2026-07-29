"""Read-only aggregation for persisted Bayesian analysis batches."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Iterable, Mapping


TERMINAL_STATUSES = {
    "COMPLETED",
    "COMPLETED_WITH_WARNINGS",
    "FAILED",
    "CANCELLED",
}
ELIGIBLE_DIAGNOSTICS = {"VALID", "VALID_WITH_WARNINGS"}
EVIDENCE_ORDER = {
    "INSUFFICIENT": 0,
    "WEAK": 1,
    "MODERATE": 2,
    "STRONG": 3,
    "VERY_STRONG": 4,
}


def _weighted_mean(
    rows: Iterable[Mapping[str, Any]],
    field: str,
) -> float | None:
    numerator = 0.0
    denominator = 0
    for row in rows:
        value = row.get(field)
        weight = int(row.get("direct_sample_size") or 0)
        if value is None or weight <= 0:
            continue
        numerator += float(value) * weight
        denominator += weight
    return numerator / denominator if denominator else None


def consolidate_indicator_effects(
    effects: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Create a descriptive synthesis without claiming a pooled posterior."""

    grouped: dict[tuple[str, str | None], list[Mapping[str, Any]]] = defaultdict(
        list
    )
    for effect in effects:
        grouped[(str(effect["indicator"]), effect.get("regime"))].append(effect)

    consolidated: list[dict[str, Any]] = []
    for (indicator, regime), rows in grouped.items():
        directions = Counter(str(row.get("effect_direction") or "NEUTRAL") for row in rows)
        highest_count = max(directions.values())
        leaders = sorted(
            direction
            for direction, count in directions.items()
            if count == highest_count
        )
        consensus_direction = leaders[0] if len(leaders) == 1 else "MIXED"
        evidence_grade = max(
            (str(row.get("evidence_grade") or "INSUFFICIENT") for row in rows),
            key=lambda grade: EVIDENCE_ORDER.get(grade, -1),
        )
        profile_ids = {str(row["profile_id"]) for row in rows}
        consolidated.append(
            {
                "indicator": indicator,
                "regime": regime,
                "profiles_included": len(profile_ids),
                "total_direct_sample_size": sum(
                    int(row.get("direct_sample_size") or 0) for row in rows
                ),
                "direction_counts": {
                    key: directions.get(key, 0)
                    for key in ("POSITIVE", "NEGATIVE", "NEUTRAL")
                },
                "consensus_direction": consensus_direction,
                "weighted_tp_lift": _weighted_mean(rows, "estimated_tp_lift"),
                "weighted_pnl_lift": _weighted_mean(rows, "estimated_pnl_lift"),
                "weighted_probability_positive": _weighted_mean(
                    rows, "probability_positive_effect"
                ),
                "highest_evidence_grade": evidence_grade,
            }
        )

    return sorted(
        consolidated,
        key=lambda row: (
            -int(row["profiles_included"]),
            str(row["indicator"]),
            str(row["regime"] or ""),
        ),
    )


def build_batch_report(
    runs: Iterable[Mapping[str, Any]],
    effects: Iterable[Mapping[str, Any]],
    *,
    batch_id: str,
    legacy_batch: bool,
) -> dict[str, Any]:
    run_rows = [dict(run) for run in runs]
    statuses = Counter(str(run.get("status") or "PENDING") for run in run_rows)
    diagnostics = Counter(
        str(run.get("diagnostic_status") or "PENDING") for run in run_rows
    )
    total = len(run_rows)
    terminal = sum(statuses.get(status, 0) for status in TERMINAL_STATUSES)
    pending = statuses.get("PENDING", 0)
    active = max(total - terminal - pending, 0)
    failed = statuses.get("FAILED", 0) + statuses.get("CANCELLED", 0)
    warning_runs = statuses.get("COMPLETED_WITH_WARNINGS", 0)
    non_converged = diagnostics.get("NOT_CONVERGED", 0)
    valid = sum(diagnostics.get(status, 0) for status in ELIGIBLE_DIAGNOSTICS)

    if total == 0:
        batch_status = "EMPTY"
    elif terminal < total:
        batch_status = "RUNNING"
    elif failed == total:
        batch_status = "FAILED"
    elif failed or warning_runs or non_converged:
        batch_status = "COMPLETED_WITH_WARNINGS"
    else:
        batch_status = "COMPLETED"

    consolidated = consolidate_indicator_effects(effects)
    consensus_counts = Counter(
        str(indicator["consensus_direction"]) for indicator in consolidated
    )
    evidence_counts = Counter(
        str(indicator["highest_evidence_grade"]) for indicator in consolidated
    )
    return {
        "batch_id": batch_id,
        "legacy_batch": legacy_batch,
        "status": batch_status,
        "counts": {
            "total": total,
            "terminal": terminal,
            "pending": pending,
            "active": active,
            "valid": valid,
            "warnings": warning_runs,
            "not_converged": non_converged,
            "failed": failed,
        },
        "progress": (terminal / total) if total else 0.0,
        "profile_runs": run_rows,
        "report": {
            "status": "FINAL" if terminal == total and total else "PARTIAL",
            "eligible_profiles": valid,
            "excluded_profiles": total - valid,
            "indicator_count": len(consolidated),
            "indicators": consolidated,
            "direction_summary": {
                key: consensus_counts.get(key, 0)
                for key in ("POSITIVE", "NEGATIVE", "NEUTRAL", "MIXED")
            },
            "evidence_summary": {
                key: evidence_counts.get(key, 0)
                for key in (
                    "INSUFFICIENT",
                    "WEAK",
                    "MODERATE",
                    "STRONG",
                    "VERY_STRONG",
                )
            },
            "methodology": (
                "Descriptive cross-profile synthesis of persisted point summaries, "
                "weighted by direct sample size. It is not a pooled or joint posterior. "
                "Non-converged, failed, pending and cancelled runs are excluded."
            ),
            "language": "association_not_causation",
        },
    }
