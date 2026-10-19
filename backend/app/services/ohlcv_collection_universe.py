"""Collection dependencies are independent of eligibility for a new entry."""
from sqlalchemy import text
from .pool_service import get_active_pool_symbols, normalize_pool_symbol


async def open_shadow_symbols(db) -> set[str]:
    rows = (await db.execute(text(
        "SELECT DISTINCT symbol FROM shadow_trades WHERE status IN ('PENDING','RUNNING')"
    ))).scalars()
    return {normalize_pool_symbol(raw) for raw in rows if raw and raw.strip()}


async def collection_universe(db) -> dict[str, list[str]]:
    """Gate spot universe; Shadow is spot-only, across all users and sources.

    Resolve on every collection round, without a cache. Removing a pool coin
    remains an entry exclusion but cannot strand an existing Shadow position.
    The final terminal transition releases the extra collection dependency.
    """
    reasons = {s: {"active_pool"} for s in await get_active_pool_symbols(db, "spot")}
    for symbol in await open_shadow_symbols(db):
        reasons.setdefault(symbol, set()).add("open_shadow")
    return {symbol: sorted(why) for symbol, why in sorted(reasons.items())}
