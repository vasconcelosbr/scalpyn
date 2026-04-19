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
