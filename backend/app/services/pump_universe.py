"""Pump-only market-cap subset and bounded price support for existing labels.

Uses the Pool discovery source/semantics. Metadata last_updated is deliberately
not advertised as market-cap freshness: ticker/book writers also update it.
"""
import math
from datetime import timedelta

from sqlalchemy import text

from .pool_selection import apply_pool_discovery_filters, load_market_cap_map
from .pump_research import max_horizon_minutes

DRAIN_ROLE = "label_drain"


def eligible_symbols(symbols, market_caps, minimum):
    valid = {}
    for symbol, value in market_caps.items():
        try:
            cap = float(value)
        except (TypeError, ValueError, OverflowError):
            continue
        if not isinstance(value, bool) and math.isfinite(cap) and cap > 0:
            valid[symbol] = cap
    if minimum <= 0:
        return sorted(symbols), valid
    # Pool helper bypasses an empty map; Pump must fail closed for unknown caps.
    if not valid:
        return [], valid
    result = apply_pool_discovery_filters(set(symbols), market_cap_map=valid,
                                          min_market_cap=minimum)
    return sorted(result["symbols"]), valid


async def select_universe(db, symbols, config, minute):
    spec = config["universe_filter"]
    caps = await load_market_cap_map(db, set(symbols))
    eligible, valid_caps = eligible_symbols(symbols, caps, float(spec["min_market_cap_usd"]))
    drains = []
    research = config["research"]
    if research.get("enabled"):
        labels = research["labels"]
        horizon = max_horizon_minutes(labels)
        # Only observations whose final endpoint still needs collecting. Support
        # rows never extend this frontier; no persistent membership/cache to reset.
        drains = (await db.execute(text("""
            SELECT m.symbol FROM pump_research_minute m
             WHERE m.ts >= :lo AND m.ts <= :minute
               AND COALESCE(m.categorical->>'_research_role', '') <> :role
               AND NOT (m.symbol = ANY(CAST(:eligible AS text[])))
               AND NOT EXISTS (
                   SELECT 1 FROM pump_research_labels l WHERE l.symbol=m.symbol
                    AND l.ts=m.ts AND l.label_set_version=:version)
             GROUP BY m.symbol HAVING max(m.ts) + make_interval(mins => :h) >= :minute
             ORDER BY m.symbol
        """), {"lo": minute - timedelta(minutes=horizon), "minute": minute,
               "role": DRAIN_ROLE, "eligible": eligible, "version": labels["version"],
               "h": horizon})).scalars().all()
    return eligible, list(drains), valid_caps
