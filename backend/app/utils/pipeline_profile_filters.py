from datetime import datetime, timezone
from typing import Any


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

    Explicit L1/L2/L3 levels are always respected.
    Source-pool watchlists stored as "custom" are promoted to L1 only when
    their associated profile actually defines filter conditions. This preserves
    pure monitoring boards while ensuring pool/profile selection criteria are
    honored.
    """
    normalized = (level or "").upper()
    if normalized in {"L1", "L2", "L3"}:
        return normalized

    filter_conditions = (
        ((profile_config or {}).get("filters") or {}).get("conditions") or []
    )
    if source_pool_id and filter_conditions:
        return "L1"

    return "custom"


def order_pipeline_watchlists_for_scan(
    watchlists: list[Any] | None,
) -> list[Any]:
    """Sort watchlists so upstream sources are scanned before dependents."""
    items = list(watchlists or [])
    if len(items) < 2:
        return items

    by_id = {getattr(wl, "id", None): wl for wl in items}
    visiting: set[Any] = set()
    memo: dict[Any, int] = {}

    def _depth(wl: Any) -> int:
        wl_id = getattr(wl, "id", None)
        if wl_id in memo:
            return memo[wl_id]
        if wl_id in visiting:
            return 0

        visiting.add(wl_id)
        parent_id = getattr(wl, "source_watchlist_id", None)
        parent = by_id.get(parent_id)
        depth = _depth(parent) + 1 if parent is not None else 0
        visiting.discard(wl_id)
        memo[wl_id] = depth
        return depth

    def _created_at_value(wl: Any) -> datetime:
        created_at = getattr(wl, "created_at", None)
        if isinstance(created_at, datetime):
            return created_at
        return datetime.min.replace(tzinfo=timezone.utc)

    def _level_rank(wl: Any) -> int:
        normalized = (getattr(wl, "level", "") or "").upper()
        return {"CUSTOM": 0, "L1": 1, "L2": 2, "L3": 3}.get(normalized, 99)

    return sorted(
        items,
        key=lambda wl: (
            _depth(wl),
            _level_rank(wl),
            _created_at_value(wl),
            str(getattr(wl, "id", "")),
        ),
    )
