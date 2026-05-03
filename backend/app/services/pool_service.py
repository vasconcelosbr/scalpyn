"""Central service for pool-based symbol resolution.

``pool_coins`` is the single source of truth for the symbol universe.
Gate.io (or any other exchange) is a data source only — never a universe source.

Every pipeline stage (collect, ohlcv, indicators, scores, decisions) MUST
obtain its symbol list exclusively via :func:`get_pool_symbols`.  No symbol
outside this set should enter any stage.
"""

from sqlalchemy import text

from ..utils.symbol_filters import filter_real_assets


def normalize_pool_symbol(symbol: str) -> str:
    """Normalize a symbol to canonical BTC_USDT format (underscore-separated).

    Examples::
        "BTCUSDT"  -> "BTC_USDT"
        "BTC_USDT" -> "BTC_USDT"
        "btc_usdt" -> "BTC_USDT"
    """
    s = symbol.upper().strip()
    if "_" not in s and s.endswith("USDT"):
        return s[:-4] + "_USDT"
    return s


async def get_pool_symbols(db, market_type: str) -> list[str]:
    """Return active pool_coins symbols for the given market_type.

    This is the **authoritative universe function**.  No symbol outside the
    returned set should enter any pipeline stage (collect, ohlcv, indicators,
    scores, decisions).

    Args:
        db: SQLAlchemy async session.
        market_type: ``'spot'`` or ``'futures'``.

    Returns:
        Deduplicated, normalized list of symbols in ``BTC_USDT`` format,
        with leveraged tokens and stablecoins already removed.
    """
    rows = (await db.execute(
        text("""
            SELECT DISTINCT symbol
            FROM pool_coins
            WHERE is_active = true
              AND market_type = :market_type
        """),
        {"market_type": market_type},
    )).fetchall()

    return filter_real_assets([normalize_pool_symbol(r.symbol) for r in rows])


async def get_approved_symbols(db, market_type: str) -> list[str]:
    """Return symbols currently approved (L3-level, direction up or NULL) for *market_type*.

    "Approved" means the symbol has passed all pipeline filter levels and is
    currently sitting in an L3 watchlist with an active (up) direction.  This
    is the **decision-driven** universe — a strict subset of pool_coins.

    Args:
        db: SQLAlchemy async session.
        market_type: ``'spot'`` or ``'futures'``.

    Returns:
        Deduplicated, normalized list of approved symbols in ``BTC_USDT`` format.
    """
    rows = (await db.execute(
        text("""
            SELECT DISTINCT pwa.symbol
            FROM pipeline_watchlist_assets pwa
            JOIN pipeline_watchlists pw ON pw.id = pwa.watchlist_id
            WHERE UPPER(pw.level) = 'L3'
              AND pw.market_mode = :market_type
              AND (pwa.level_direction IS NULL OR pwa.level_direction = 'up')
        """),
        {"market_type": market_type},
    )).fetchall()

    return filter_real_assets([normalize_pool_symbol(r.symbol) for r in rows])


async def get_approved_symbols_with_market_type(db) -> dict[str, str]:
    """Return a mapping of normalized symbol → market_type for all approved symbols.

    Same semantics as :func:`get_approved_symbols` but covers all market types in
    a single round-trip.  Used by the 5m collector which processes spot + futures
    in one pass.

    Returns:
        Dict of ``{ "BTC_USDT": "spot", "ETH_USDT": "futures", ... }`` for every
        symbol currently approved in any L3 pipeline watchlist.
    """
    rows = (await db.execute(
        text("""
            SELECT DISTINCT pwa.symbol, pw.market_mode
            FROM pipeline_watchlist_assets pwa
            JOIN pipeline_watchlists pw ON pw.id = pwa.watchlist_id
            WHERE UPPER(pw.level) = 'L3'
              AND (pwa.level_direction IS NULL OR pwa.level_direction = 'up')
        """),
    )).fetchall()

    return {normalize_pool_symbol(r.symbol): r.market_mode for r in rows}


async def get_approved_pool_symbols(db, market_type: str = None) -> list[str]:
    """Return symbols from pool_coins where is_approved = true.

    Uses ``pool_coins.is_approved`` as the single source of truth — bypasses
    the pipeline watchlist (L3) so that symbols approved directly in the DB
    are collected even before they reach an L3 watchlist.

    Args:
        db: SQLAlchemy async session.
        market_type: Optional ``'spot'`` or ``'futures'`` filter.

    Returns:
        Deduplicated, normalized list of approved symbols.
    """
    if market_type:
        rows = (await db.execute(
            text("""
                SELECT DISTINCT symbol
                FROM pool_coins
                WHERE is_active = true
                  AND is_approved = true
                  AND market_type = :market_type
            """),
            {"market_type": market_type},
        )).fetchall()
    else:
        rows = (await db.execute(
            text("""
                SELECT DISTINCT symbol
                FROM pool_coins
                WHERE is_active = true
                  AND is_approved = true
            """),
        )).fetchall()

    return list(set(filter_real_assets([normalize_pool_symbol(r.symbol) for r in rows])))


async def get_approved_pool_symbols_with_market_type(db) -> dict[str, str]:
    """Return a mapping of normalized symbol → market_type for all approved pool coins.

    Uses ``pool_coins.is_approved`` directly.  Covers all market types in a
    single round-trip; used by the 5m collector.

    Returns:
        Dict of ``{ "BTC_USDT": "spot", "ETH_USDT": "futures", ... }``.
    """
    rows = (await db.execute(
        text("""
            SELECT DISTINCT symbol, market_type
            FROM pool_coins
            WHERE is_active = true
              AND is_approved = true
        """),
    )).fetchall()

    return {normalize_pool_symbol(r.symbol): r.market_type for r in rows}


async def get_pool_symbols_with_market_type(db) -> dict[str, str]:
    """Return a mapping of normalized symbol → market_type for all active pool coins.

    Used by collectors that need to tag each ohlcv/indicator row with the
    correct market_type without making one DB query per symbol.

    Returns:
        Dict of ``{ "BTC_USDT": "spot", "ETH_USDT": "futures", ... }``.
        When the same symbol appears in both spot and futures pools,
        the last-seen value wins (ambiguous by design — such a symbol
        should appear in both pipelines and callers must decide which
        market_type context is active at call time).
    """
    rows = (await db.execute(
        text("""
            SELECT DISTINCT symbol, market_type
            FROM pool_coins
            WHERE is_active = true
        """),
    )).fetchall()

    return {normalize_pool_symbol(r.symbol): r.market_type for r in rows}
