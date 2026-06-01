"""Central service for pool-based symbol resolution.

``pool_coins`` is the single source of truth for the symbol universe.
Gate.io (or any other exchange) is a data source only — never a universe source.

Every pipeline stage (collect, ohlcv, indicators, scores, decisions) MUST
obtain its symbol list exclusively via :func:`get_pool_symbols` (raw pool)
or :func:`get_active_pool_symbols` (post-Task #232 ingestion gate).

Task #232 — semantic split
---------------------------
* ``is_active`` gates **ingestion** (collect, indicators, scoring,
  pipeline_scan, WS subscription resolver). Default ``true``.
* ``is_tradable`` gates **execution** only. Default ``false``. Read
  exclusively by ``evaluate_signals`` and ``execute_buy``.

The legacy helper :func:`get_approved_pool_symbols` is kept as a thin
:class:`DeprecationWarning`-emitting alias of :func:`get_active_pool_symbols`
so an unmerged caller cannot silently change its symbol universe semantics
during the transition.
"""

import logging
import warnings

from sqlalchemy import text

from ..utils.symbol_filters import filter_real_assets

_log = logging.getLogger(__name__)


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


async def get_active_pool_symbols(db, market_type: str = None) -> list[str]:
    """Return symbols from ``pool_coins`` where ``is_active = true``.

    Task #232 — this is the INGESTION universe (collector, indicators,
    pipeline_scan entry, WS subscription resolver). The execution path
    (``evaluate_signals`` / ``execute_buy``) must additionally filter on
    ``is_tradable = true``.

    Args:
        db: SQLAlchemy async session.
        market_type: Optional ``'spot'`` or ``'futures'`` filter.

    Returns:
        Deduplicated, normalized list of active symbols.
    """
    if market_type:
        rows = (await db.execute(
            text("""
                SELECT DISTINCT symbol
                FROM pool_coins
                WHERE is_active = true
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
            """),
        )).fetchall()

    # set() handles normalize_pool_symbol collisions (e.g. "BTCUSDT" + "BTC_USDT" → same key)
    return list(set(filter_real_assets([normalize_pool_symbol(r.symbol) for r in rows])))


async def get_active_pool_symbols_with_market_type(db) -> dict[str, str]:
    """Return a ``{symbol: market_type}`` mapping for every active pool coin.

    Task #232 ingestion-side helper — same shape as the historical
    ``get_approved_pool_symbols_with_market_type`` but gated on
    ``is_active`` only. The execution path uses a separate query.
    """
    rows = (await db.execute(
        text("""
            SELECT DISTINCT symbol, market_type
            FROM pool_coins
            WHERE is_active = true
        """),
    )).fetchall()

    return {normalize_pool_symbol(r.symbol): r.market_type for r in rows}


# ── Backwards-compatibility shims (Task #232 transition) ─────────────────────
# Removed in deploy N+2 once no caller imports these names anymore.

async def get_approved_pool_symbols(db, market_type: str = None) -> list[str]:
    """DEPRECATED — use :func:`get_active_pool_symbols`.

    Kept as a thin alias so unmerged callers do not silently change semantics
    while the rolling deploy is in flight. The ingestion gate is now
    ``is_active`` (operator added the symbol to the pool); the execution gate
    moved to ``is_tradable`` and is read directly inside ``evaluate_signals``
    and ``execute_buy``.
    """
    warnings.warn(
        "get_approved_pool_symbols() is deprecated — use get_active_pool_symbols() "
        "(ingestion gate). For the execution gate, query is_tradable directly "
        "inside evaluate_signals/execute_buy.",
        DeprecationWarning,
        stacklevel=2,
    )
    return await get_active_pool_symbols(db, market_type)


async def get_approved_pool_symbols_with_market_type(db) -> dict[str, str]:
    """DEPRECATED — use :func:`get_active_pool_symbols_with_market_type`."""
    warnings.warn(
        "get_approved_pool_symbols_with_market_type() is deprecated — "
        "use get_active_pool_symbols_with_market_type().",
        DeprecationWarning,
        stacklevel=2,
    )
    return await get_active_pool_symbols_with_market_type(db)


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


async def apply_structural_pool_filter(
    symbols: list[str],
    db,
    config: dict,
) -> list[str]:
    """Filter pool symbols by STRUCTURAL (operability) criteria only.

    Reads volume_24h, spread_pct, and orderbook_depth_usdt from
    market_metadata.  NEVER filters by setup-quality signals (RSI, ADX,
    momentum, score) — those would leak signal into the ML training dataset
    and recreate the constant-target problem the new architecture exists to fix.

    Config keys (all under pool_structural_filter in pool_config):
        min_volume_24h_usdt       — minimum 24h quote volume
        max_spread_pct            — maximum bid/ask spread (percent)
        min_orderbook_depth_usdt  — minimum orderbook depth

    Returns the original list unchanged when:
    - ``new_arch_capture_enabled`` is False (caller's responsibility)
    - ``pool_structural_filter`` section is absent or has no thresholds
    - ``symbols`` is empty
    """
    f = config.get("pool_structural_filter", {})
    min_vol   = f.get("min_volume_24h_usdt")
    max_sprd  = f.get("max_spread_pct")
    min_depth = f.get("min_orderbook_depth_usdt")

    if not symbols or not any(v is not None for v in [min_vol, max_sprd, min_depth]):
        return symbols

    rows = (await db.execute(
        text("""
            SELECT symbol,
                   COALESCE(volume_24h, 0)  AS volume_24h,
                   spread_pct,
                   orderbook_depth_usdt
            FROM market_metadata
            WHERE symbol = ANY(:symbols)
        """),
        {"symbols": list(symbols)},
    )).fetchall()

    meta_map = {r.symbol: r for r in rows}

    kept: list[str] = []
    removed: list[tuple[str, list[str]]] = []
    for s in symbols:
        meta = meta_map.get(s)
        reasons: list[str] = []
        if meta is None:
            reasons.append("no_metadata")
        else:
            if min_vol is not None and (meta.volume_24h or 0) < min_vol:
                reasons.append("low_volume")
            if max_sprd is not None and meta.spread_pct is not None and meta.spread_pct > max_sprd:
                reasons.append("high_spread")
            if min_depth is not None and meta.orderbook_depth_usdt is not None and meta.orderbook_depth_usdt < min_depth:
                reasons.append("low_depth")
        if reasons:
            removed.append((s, reasons))
        else:
            kept.append(s)

    _log.info(
        "POOL_STRUCTURAL_FILTER|input=%d|kept=%d|removed=%d",
        len(symbols), len(kept), len(removed),
    )
    return kept
