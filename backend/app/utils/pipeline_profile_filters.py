from typing import Any


WATCHLIST_LEVELS = ("POOL", "L1", "L2", "L3", "custom")
PIPELINE_FILTER_LEVELS = frozenset({"POOL", "L1", "L2", "L3"})
WATCHLIST_STAGE_ORDER = {
    "POOL": 0,
    "L1": 1,
    "L2": 2,
    "L3": 3,
    "custom": 4,
}
UPSTREAM_WATCHLIST_LEVEL = {
    "L1": "POOL",
    "L2": "L1",
    "L3": "L2",
}


STRICT_META_FIELDS = frozenset({
    "volume_24h",
    "market_cap",
    "price",
    "current_price",
    "change_24h",
    "change_24h_pct",
    "price_change_24h",
    "spread_pct",
    "orderbook_depth_usdt",
})

STRICT_META_MIN_COVERAGE_RATIO = 0.10


def uses_pipeline_filters(level: str | None) -> bool:
    """True when the level is a real pipeline filter stage."""
    return (level or "").upper() in PIPELINE_FILTER_LEVELS


def normalize_watchlist_level(level: Any, *, default: str = "custom") -> str:
    """Normalize persisted/user-provided watchlist levels."""
    raw_level = str(level or default).strip()
    normalized = "custom" if raw_level.lower() == "custom" else raw_level.upper()
    if normalized not in WATCHLIST_LEVELS:
        raise ValueError(f"Invalid watchlist level: {level}")
    return normalized


def expected_upstream_watchlist_level(level: Any) -> str | None:
    """Return the required upstream stage for a pipeline watchlist."""
    normalized = normalize_watchlist_level(level)
    return UPSTREAM_WATCHLIST_LEVEL.get(normalized)


def resolve_pipeline_dependency(
    *,
    level: Any,
    source_pool_id: Any = None,
    source_watchlist_id: Any = None,
    source_watchlist_level: Any = None,
    pool_gate_watchlist_id: Any = None,
) -> dict[str, str | None]:
    """Normalize the effective upstream dependency for a pipeline stage."""
    normalized = normalize_watchlist_level(level)
    expected_level = expected_upstream_watchlist_level(normalized)
    pool_id = str(source_pool_id) if source_pool_id else None
    watchlist_id = str(source_watchlist_id) if source_watchlist_id else None
    watchlist_level = (
        normalize_watchlist_level(source_watchlist_level)
        if source_watchlist_level is not None
        else None
    )
    gatekeeper_id = str(pool_gate_watchlist_id) if pool_gate_watchlist_id else None

    if normalized == "POOL":
        return {
            "source_pool_id": pool_id,
            "source_watchlist_id": None,
            "expected_upstream_level": None,
            "resolution": "pool_source",
            "error": None,
        }

    if expected_level is None:
        return {
            "source_pool_id": pool_id,
            "source_watchlist_id": watchlist_id,
            "expected_upstream_level": None,
            "resolution": "passthrough",
            "error": None,
        }

    if watchlist_id and watchlist_level == expected_level:
        return {
            "source_pool_id": None,
            "source_watchlist_id": watchlist_id,
            "expected_upstream_level": expected_level,
            "resolution": "direct_watchlist",
            "error": None,
        }

    if normalized == "L1" and gatekeeper_id:
        return {
            "source_pool_id": None,
            "source_watchlist_id": gatekeeper_id,
            "expected_upstream_level": expected_level,
            "resolution": "implicit_pool_gate",
            "error": None,
        }

    error = "missing_upstream_watchlist"
    if watchlist_id and watchlist_level != expected_level:
        error = "invalid_upstream_level"

    return {
        "source_pool_id": None,
        "source_watchlist_id": None,
        "expected_upstream_level": expected_level,
        "resolution": "missing_dependency",
        "error": error,
    }


def select_profile_filter_conditions(
    conditions: list[dict[str, Any]] | None,
    *,
    total_symbols: int,
    symbols_with_meta: int,
) -> dict[str, Any]:
    """Relax strict meta conditions while market-data coverage is still sparse."""
    all_conditions = list(conditions or [])
    strict_meta_conditions = [
        cond for cond in all_conditions if cond.get("field") in STRICT_META_FIELDS
    ]
    non_meta_conditions = [
        cond for cond in all_conditions if cond.get("field") not in STRICT_META_FIELDS
    ]
    coverage_ratio = (symbols_with_meta / total_symbols) if total_symbols > 0 else 0.0
    relaxed_strict_meta = (
        bool(strict_meta_conditions)
        and coverage_ratio < STRICT_META_MIN_COVERAGE_RATIO
    )

    return {
        "conditions": non_meta_conditions if relaxed_strict_meta else all_conditions,
        "coverage_ratio": coverage_ratio,
        "relaxed_strict_meta": relaxed_strict_meta,
        "strict_meta_conditions": strict_meta_conditions,
        "non_meta_conditions": non_meta_conditions,
    }


def effective_pipeline_level(
    level: str | None,
    *,
    source_pool_id: Any = None,
    profile_config: dict[str, Any] | None = None,
) -> str:
    """Resolve the effective pipeline level for filtering behaviour.

    Explicit POOL/L1/L2/L3 levels are always respected.
    Source-pool watchlists stored as "custom" are promoted to POOL only when
    their associated profile actually defines filter conditions. This preserves
    pure monitoring boards while ensuring pool/profile selection criteria are
    honored.
    """
    normalized = (level or "").upper()
    if normalized in PIPELINE_FILTER_LEVELS:
        return normalized

    filter_conditions = (
        ((profile_config or {}).get("filters") or {}).get("conditions") or []
    )
    if source_pool_id and filter_conditions:
        return "POOL"

    return "custom"
