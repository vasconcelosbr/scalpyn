"""Out-of-sample validation policy for Profile Intelligence discoveries."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable


PI_MIN_DISCOVERY_TRADES = 30
PI_MIN_VALIDATION_TRADES = 20
PI_MIN_VALIDATION_LIFT = 1.15
PI_MIN_VALIDATION_WINRATE_DELTA = 0.05
PI_MAX_SINGLE_SYMBOL_SHARE = 0.40
PI_MAX_SINGLE_DAY_SHARE = 0.40
PI_MIN_DISTINCT_SYMBOLS = 3
PI_MIN_DISTINCT_DAYS = 3
PI_MIN_ASSOC_SUPPORT_VALIDATION = 0.02
PI_MIN_ASSOC_CONFIDENCE_VALIDATION = 0.55
PI_MIN_VALIDATION_LIFT_RETENTION = 0.70

VALIDATED_SOURCE_TYPES = {
    "counterfactual_dynamic",
    "association_rule",
    "optuna",
}


def temporal_split_valid(
    discovery_start: datetime,
    discovery_end: datetime,
    validation_start: datetime,
    validation_end: datetime,
) -> bool:
    return (
        discovery_start < discovery_end
        and discovery_end < validation_start
        and validation_start < validation_end
    )


def diversity_metrics(trades: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(trades)
    total = len(rows)
    symbol_counts: dict[str, int] = {}
    day_counts: dict[str, int] = {}
    for trade in rows:
        symbol = str(trade.get("symbol") or "UNKNOWN")
        created_at = trade.get("created_at")
        day = (
            created_at.date().isoformat()
            if isinstance(created_at, datetime)
            else "UNKNOWN"
        )
        symbol_counts[symbol] = symbol_counts.get(symbol, 0) + 1
        day_counts[day] = day_counts.get(day, 0) + 1
    return {
        "distinct_symbols": len(symbol_counts),
        "distinct_days": len(day_counts),
        "max_single_symbol_share": (
            max(symbol_counts.values(), default=0) / total if total else 1.0
        ),
        "max_single_day_share": (
            max(day_counts.values(), default=0) / total if total else 1.0
        ),
    }


def classify_validation(
    *,
    discovery_metrics: dict[str, Any],
    validation_metrics: dict[str, Any],
    discovery_start: datetime,
    discovery_end: datetime,
    validation_start: datetime,
    validation_end: datetime,
    missing_count: int = 0,
    association_rule: bool = False,
) -> dict[str, Any]:
    discovery_count = int(discovery_metrics.get("total_cases", 0) or 0)
    validation_count = int(validation_metrics.get("total_cases", 0) or 0)
    validation_lift = float(validation_metrics.get("lift", 0) or 0)
    validation_win_rate = float(validation_metrics.get("win_rate", 0) or 0)
    validation_base = float(validation_metrics.get("base_win_rate", 0) or 0)
    diversity = {
        "distinct_symbols": int(validation_metrics.get("distinct_symbols", 0) or 0),
        "distinct_days": int(validation_metrics.get("distinct_days", 0) or 0),
        "max_single_symbol_share": float(
            validation_metrics.get("max_single_symbol_share", 1) or 1
        ),
        "max_single_day_share": float(
            validation_metrics.get("max_single_day_share", 1) or 1
        ),
    }

    blocked_reason = None
    if not temporal_split_valid(
        discovery_start,
        discovery_end,
        validation_start,
        validation_end,
    ):
        blocked_reason = "blocked_no_validation"
    elif discovery_count < PI_MIN_DISCOVERY_TRADES:
        blocked_reason = "blocked_low_discovery_support"
    elif validation_count == 0:
        blocked_reason = "blocked_no_validation"
    elif validation_count < PI_MIN_VALIDATION_TRADES:
        blocked_reason = "blocked_low_validation_support"
    elif missing_count > 0:
        blocked_reason = "blocked_missing_feature"
    elif validation_lift < PI_MIN_VALIDATION_LIFT:
        blocked_reason = "blocked_validation_lift"
    elif (
        float(discovery_metrics.get("lift", 0) or 0) > 0
        and validation_lift
        / float(discovery_metrics.get("lift", 0) or 1)
        < PI_MIN_VALIDATION_LIFT_RETENTION
    ):
        blocked_reason = "blocked_validation_lift"
    elif validation_win_rate < validation_base + PI_MIN_VALIDATION_WINRATE_DELTA:
        blocked_reason = "blocked_validation_winrate"
    elif (
        diversity["distinct_symbols"] < PI_MIN_DISTINCT_SYMBOLS
        or diversity["max_single_symbol_share"] > PI_MAX_SINGLE_SYMBOL_SHARE
    ):
        blocked_reason = "blocked_single_symbol_dependency"
    elif (
        diversity["distinct_days"] < PI_MIN_DISTINCT_DAYS
        or diversity["max_single_day_share"] > PI_MAX_SINGLE_DAY_SHARE
    ):
        blocked_reason = "blocked_single_day_dependency"
    elif association_rule and (
        float(validation_metrics.get("support", 0) or 0)
        < PI_MIN_ASSOC_SUPPORT_VALIDATION
        or float(validation_metrics.get("confidence", 0) or 0)
        < PI_MIN_ASSOC_CONFIDENCE_VALIDATION
    ):
        blocked_reason = "blocked_low_validation_support"

    validated = blocked_reason is None
    return {
        "validation_status": "validated" if validated else blocked_reason,
        "actionability_status": "validated" if validated else "exploratory_only",
        "blocked_reason": blocked_reason,
        "temporal_split_valid": temporal_split_valid(
            discovery_start,
            discovery_end,
            validation_start,
            validation_end,
        ),
        "no_missing_feature_pass": missing_count == 0,
        "no_single_symbol_dependency": (
            diversity["distinct_symbols"] >= PI_MIN_DISTINCT_SYMBOLS
            and diversity["max_single_symbol_share"] <= PI_MAX_SINGLE_SYMBOL_SHARE
        ),
        "no_single_day_dependency": (
            diversity["distinct_days"] >= PI_MIN_DISTINCT_DAYS
            and diversity["max_single_day_share"] <= PI_MAX_SINGLE_DAY_SHARE
        ),
    }


def suggestion_actionable(
    combination_type: str,
    validation_metrics: dict[str, Any] | None,
) -> tuple[bool, str | None]:
    if combination_type not in VALIDATED_SOURCE_TYPES:
        return True, None
    metrics = validation_metrics or {}
    if metrics.get("validation_status") != "validated":
        return False, str(
            metrics.get("blocked_reason")
            or metrics.get("validation_status")
            or "blocked_no_validation"
        )
    if combination_type == "association_rule":
        actionability = metrics.get("actionability_status")
        if actionability != "positive_signal_candidate":
            return False, str(actionability or "not_actionable")
    return True, None
