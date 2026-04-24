from __future__ import annotations

from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession


def extract_profile_discovery_thresholds(
    profile_config: dict[str, Any] | None,
) -> tuple[float, float, bool]:
    """Extract pool discovery thresholds from profile filter conditions.

    Returns ``(min_volume, min_market_cap, profile_applied)`` where the first
    two values are the highest supported lower-bound thresholds found in the
    profile filters and ``profile_applied`` indicates whether any discovery
    threshold was derived from the profile at all.
    """
    min_volume = 0.0
    min_market_cap = 0.0
    profile_applied = False

    conditions = (((profile_config or {}).get("filters") or {}).get("conditions") or [])
    for cond in conditions:
        field = cond.get("field", "")
        operator = cond.get("operator", ">")
        value = cond.get("value", 0)

        if field in {"volume_24h", "volume_24h_usd"} and operator in {">", ">="}:
            min_volume = max(min_volume, float(value or 0))
            profile_applied = True
        elif field in {"market_cap", "market_cap_usd"} and operator in {">", ">="}:
            min_market_cap = max(min_market_cap, float(value or 0))
            profile_applied = True

    return min_volume, min_market_cap, profile_applied


async def load_profile_discovery_thresholds(
    db: AsyncSession,
    profile_id: Any,
) -> tuple[float, float, bool]:
    """Load discovery thresholds from the linked profile, if one exists."""
    if not profile_id:
        return 0.0, 0.0, False

    from ..models.profile import Profile

    profile = (await db.execute(
        select(Profile).where(Profile.id == profile_id)
    )).scalars().first()
    if not profile or not profile.config:
        return 0.0, 0.0, False

    return extract_profile_discovery_thresholds(profile.config)


async def load_market_cap_map(
    db: AsyncSession,
    symbols: set[str],
) -> dict[str, float]:
    """Return the latest known market caps for ``symbols`` from ``market_metadata``."""
    if not symbols:
        return {}

    rows = (await db.execute(
        text("""
            SELECT symbol, market_cap
            FROM market_metadata
            WHERE symbol = ANY(:symbols)
        """),
        {"symbols": list(symbols)},
    )).fetchall()
    return {
        row.symbol: float(row.market_cap)
        for row in rows
        if row.market_cap is not None
    }


def apply_pool_discovery_filters(
    universe_symbols: set[str],
    *,
    vol_map: dict[str, float] | None = None,
    market_cap_map: dict[str, float] | None = None,
    min_volume: float = 0.0,
    min_market_cap: float = 0.0,
    max_assets: int = 0,
) -> dict[str, Any]:
    """Apply profile/override-driven pool discovery filters to a symbol universe.

    The returned payload includes the filtered ``symbols`` set together with the
    pre/post counts for the volume, market-cap, and ``max_assets`` stages:
    ``pre_volume_count``, ``post_volume_count``, ``pre_market_cap_count``,
    ``post_market_cap_count``, and ``pre_cap_count``.
    """
    filtered_symbols = set(universe_symbols)
    pre_volume_count = len(filtered_symbols)
    post_volume_count = pre_volume_count
    pre_market_cap_count = post_volume_count
    post_market_cap_count = pre_market_cap_count
    pre_cap_count = post_market_cap_count

    if min_volume > 0 and vol_map:
        filtered_symbols = {
            symbol for symbol in filtered_symbols
            if float(vol_map.get(symbol, 0) or 0) >= min_volume
        }
        post_volume_count = len(filtered_symbols)

    pre_market_cap_count = len(filtered_symbols)
    if min_market_cap > 0 and market_cap_map:
        filtered_symbols = {
            symbol for symbol in filtered_symbols
            if float(market_cap_map.get(symbol, 0) or 0) >= min_market_cap
        }
        post_market_cap_count = len(filtered_symbols)
    else:
        post_market_cap_count = pre_market_cap_count

    pre_cap_count = len(filtered_symbols)
    if max_assets > 0 and len(filtered_symbols) > max_assets:
        filtered_symbols = set(sorted(
            filtered_symbols,
            key=lambda symbol: (
                -float((vol_map or {}).get(symbol, 0) or 0),
                -float((market_cap_map or {}).get(symbol, 0) or 0),
                symbol,
            ),
        )[:max_assets])

    return {
        "symbols": filtered_symbols,
        "pre_volume_count": pre_volume_count,
        "post_volume_count": post_volume_count,
        "pre_market_cap_count": pre_market_cap_count,
        "post_market_cap_count": post_market_cap_count,
        "pre_cap_count": pre_cap_count,
    }
