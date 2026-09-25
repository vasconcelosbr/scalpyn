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


# ── Immediate cross-layer invalidation on pool removal (2026-09-23) ──────────
# ``pool_coins`` deletion (radar_auto_discover.sync, auto_discover_assets.py)
# is instant, but L1/L2/L3 pipeline_watchlist_assets rows and the public L3
# Consolidado feed each have their own independent scan cadence — without
# this, a symbol removed from the pool could still show "Approved" or
# "Aprovado para execução" for minutes, until each layer's next cycle (or,
# for L3 Consolidado, the authorization contract's own TTL) catches up.

async def cascade_invalidate_removed_symbols(db, pool_id, symbols) -> int:
    """Mark ``symbols`` as ``level_direction='down'`` in every pipeline
    watchlist descending from ``pool_id`` (POOL -> L1 -> L2 -> L3, any
    branch depth) — call this immediately after deleting the matching
    ``pool_coins`` rows so Pool/L1/L2/L3 UIs never lag behind pool
    membership. Cheap and surgical: a direct UPDATE, no re-evaluation.

    Returns the number of (watchlist, symbol) rows actually flipped.
    """
    symbols = list(symbols)
    if not symbols:
        return 0
    result = await db.execute(
        text("""
            WITH RECURSIVE descendant_watchlists AS (
                SELECT id FROM pipeline_watchlists WHERE source_pool_id = :pool_id
                UNION ALL
                SELECT pw.id
                FROM pipeline_watchlists pw
                JOIN descendant_watchlists dw ON pw.source_watchlist_id = dw.id
            )
            UPDATE pipeline_watchlist_assets
            SET level_direction = 'down', level_change_at = now()
            WHERE watchlist_id IN (SELECT id FROM descendant_watchlists)
              AND symbol = ANY(:symbols)
              AND (level_direction IS NULL OR level_direction != 'down')
            RETURNING symbol
        """),
        {"pool_id": pool_id, "symbols": symbols},
    )
    return len(result.fetchall())


async def resolve_root_pool_ids(db, watchlist_ids) -> dict:
    """Return ``{watchlist_id: pool_id}`` for every id in ``watchlist_ids``
    that descends from a pool (walking UP via ``source_watchlist_id`` to the
    ``POOL``-level row). A watchlist with no pool ancestor (a standalone L3
    profile, a manual watchlist) is simply absent from the result — callers
    must treat that as "no pool-membership constraint applies".
    """
    watchlist_ids = list(watchlist_ids)
    if not watchlist_ids:
        return {}
    rows = (await db.execute(
        text("""
            WITH RECURSIVE ancestry AS (
                SELECT id AS start_id, id, source_pool_id, source_watchlist_id
                FROM pipeline_watchlists
                WHERE id = ANY(:ids)
                UNION ALL
                SELECT a.start_id, pw.id, pw.source_pool_id, pw.source_watchlist_id
                FROM pipeline_watchlists pw
                JOIN ancestry a ON pw.id = a.source_watchlist_id
            )
            SELECT start_id, source_pool_id
            FROM ancestry
            WHERE source_pool_id IS NOT NULL
        """),
        {"ids": watchlist_ids},
    )).fetchall()
    return {r.start_id: r.source_pool_id for r in rows}


async def symbols_with_open_shadow_trades(db, user_id, symbols) -> set:
    """Return the subset of ``symbols`` that have a PENDING/RUNNING shadow
    trade for ``user_id``.

    2026-09-25 (operator request): a symbol whose radar/discovery signal
    drops must stop being eligible for NEW L1/L2/L3 candidacy immediately
    (see :func:`set_held_for_open_position` -- ``cascade_invalidate_removed_
    symbols`` alone is NOT durable for this: pipeline_scan's own upsert
    flips ``level_direction`` back to NULL on the very next cycle for any
    symbol still ``is_active=true`` and still structurally passing L1/L2),
    but must NOT lose live data collection while a shadow trade on it is
    still open (PENDING/RUNNING, e.g. trailing) -- indicators/alpha_scores
    would go stale mid-trade otherwise (see the SUI_USDT/LIT_USDT
    features_snapshot gap this fixes). Callers use this to decide which
    symbols in a to-be-removed set must keep their ``pool_coins`` row
    (collection alive) instead of being deleted.
    """
    symbols = list(symbols)
    if not symbols:
        return set()
    rows = (await db.execute(
        text("""
            SELECT DISTINCT symbol
            FROM shadow_trades
            WHERE user_id = :user_id
              AND symbol = ANY(:symbols)
              AND status IN ('PENDING', 'RUNNING')
        """),
        {"user_id": user_id, "symbols": symbols},
    )).fetchall()
    return {r.symbol for r in rows}


async def set_held_for_open_position(db, pool_id, symbols, *, held: bool) -> int:
    """Set ``pool_coins.held_for_open_position`` for ``symbols`` in ``pool_id``.

    ``held=True`` — the symbol dropped out of the radar/discovery selection
    but has an open shadow trade; ``pipeline_scan``'s POOL-level query
    excludes ``held_for_open_position=true`` rows from L1/L2/L3 propagation
    (no new decision/trade can form on it) while ``is_active`` stays true
    so collectors (indicators, alpha_scores) keep it fresh.

    ``held=False`` — the symbol is back in the radar/discovery selection
    (or the caller is about to delete the row outright); clears a stale
    flag so it resumes normal candidacy.
    """
    symbols = list(symbols)
    if not symbols:
        return 0
    result = await db.execute(
        text("""
            UPDATE pool_coins
               SET held_for_open_position = :held
             WHERE pool_id = :pool_id
               AND symbol = ANY(:symbols)
               AND held_for_open_position != :held
            RETURNING symbol
        """),
        {"pool_id": pool_id, "symbols": symbols, "held": held},
    )
    return len(result.fetchall())
