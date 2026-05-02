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
