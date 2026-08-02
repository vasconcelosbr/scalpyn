"""Prometheus metrics for canonical L3 profile consolidation.

The dependency is optional in local/test environments; every recorder is a
no-op when ``prometheus_client`` is unavailable.  Label cardinality follows
the operator contract: symbol/profile/reason/rule only, never trade IDs.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

try:
    from prometheus_client import Counter, Histogram  # type: ignore[import-untyped]

    _CANDIDATES = Counter(
        "l3_consolidation_candidates_total",
        "L3 candidates evaluated by profile consolidation.",
        ["symbol", "rule_version"],
    )
    _WINNERS = Counter(
        "l3_consolidation_winners_total",
        "L3 consolidation winners persisted as trades.",
        ["symbol", "winner_profile", "rule_version"],
    )
    _SUPPRESSED = Counter(
        "l3_consolidation_suppressed_total",
        "L3 profile signals suppressed by consolidation.",
        ["symbol", "reason_code", "rule_version"],
    )
    _ACTIVE_BLOCKS = Counter(
        "l3_consolidation_active_trade_blocks_total",
        "L3 candidate groups blocked by an existing active trade.",
        ["symbol", "rule_version"],
    )
    _CONCURRENCY_CONFLICTS = Counter(
        "l3_consolidation_concurrency_conflicts_total",
        "L3 insert races resolved in favour of the already-created trade.",
        ["symbol", "rule_version"],
    )
    _CANDIDATE_COUNT = Histogram(
        "l3_consolidation_candidate_count",
        "Number of approved profiles in one L3 consolidation event.",
        ["symbol", "rule_version"],
        buckets=(1, 2, 3, 4, 5, 7, 10, 15, 25, 50),
    )
except Exception:  # pragma: no cover - optional dependency
    _CANDIDATES = None
    _WINNERS = None
    _SUPPRESSED = None
    _ACTIVE_BLOCKS = None
    _CONCURRENCY_CONFLICTS = None
    _CANDIDATE_COUNT = None


def _safe(callable_obj, *args, **kwargs) -> None:
    if callable_obj is None:
        return
    try:
        callable_obj(*args, **kwargs)
    except Exception as exc:  # pragma: no cover - metrics never affect trading
        logger.debug("L3 consolidation metric failed: %s", exc)


def record_candidates(symbol: str, count: int, rule_version: str) -> None:
    if count <= 0:
        return
    if _CANDIDATES:
        _safe(
            _CANDIDATES.labels(
                symbol=symbol, rule_version=rule_version
            ).inc,
            count,
        )
    if _CANDIDATE_COUNT:
        _safe(
            _CANDIDATE_COUNT.labels(
                symbol=symbol, rule_version=rule_version
            ).observe,
            count,
        )


def record_winner(symbol: str, profile_name: str, rule_version: str) -> None:
    if _WINNERS:
        _safe(
            _WINNERS.labels(
                symbol=symbol,
                winner_profile=profile_name or "_unknown",
                rule_version=rule_version,
            ).inc
        )


def record_suppressed(symbol: str, count: int, reason_code: str, rule_version: str) -> None:
    if count <= 0 or not _SUPPRESSED:
        return
    _safe(
        _SUPPRESSED.labels(
            symbol=symbol,
            reason_code=reason_code,
            rule_version=rule_version,
        ).inc,
        count,
    )


def record_active_block(symbol: str, rule_version: str) -> None:
    if _ACTIVE_BLOCKS:
        _safe(_ACTIVE_BLOCKS.labels(symbol=symbol, rule_version=rule_version).inc)


def record_concurrency_conflict(symbol: str, rule_version: str) -> None:
    if _CONCURRENCY_CONFLICTS:
        _safe(
            _CONCURRENCY_CONFLICTS.labels(
                symbol=symbol, rule_version=rule_version
            ).inc
        )
