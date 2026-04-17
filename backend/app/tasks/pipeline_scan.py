"""Celery Task — Pipeline Scan (L1 → L2 → L3).

Runs every 5 minutes (triggered by compute_5m chain or beat schedule).
For each active user:
  1. Fetch all PipelineWatchlists (POOL / L1 / L2 / L3)
  2. Resolve the symbol universe per watchlist (from Pool or parent watchlist)
  3. Fetch market data (indicators + alpha_scores + market_metadata)
  4. Apply ProfileEngine filters/scoring per level
  5. Persist results in pipeline_watchlist_assets (upsert)
  6. Compare with prior snapshot in Redis → detect new L3 signals
  7. Broadcast new signals via WebSocket (channel "signals" + "pipeline")
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from ..tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

_REDIS_PREFIX = "spe:pipeline:"   # Redis key prefix per watchlist


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ─── helpers ─────────────────────────────────────────────────────────────────

def _get_redis():
    """Return a Redis client (soft dependency — returns None if unavailable)."""
    try:
        import redis as redis_lib
        from ..config import settings
        return redis_lib.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=2,
        )
    except Exception as exc:
        logger.warning("Pipeline scan: Redis unavailable: %s", exc)
        return None


def _prior_signals(redis, watchlist_id: str) -> set:
    """Load the set of symbols that triggered L3 in the last scan."""
    if not redis:
        return set()
    try:
        raw = redis.get(f"{_REDIS_PREFIX}{watchlist_id}:signals")
        return set(json.loads(raw)) if raw else set()
    except Exception:
        return set()


def _save_signals(redis, watchlist_id: str, symbols: set, ttl: int = 300):
    """Persist the current signal set for the next comparison (TTL 5 min)."""
    if not redis:
        return
    try:
        redis.setex(f"{_REDIS_PREFIX}{watchlist_id}:signals", ttl, json.dumps(list(symbols)))
    except Exception:
        pass


# ─── market data loader ───────────────────────────────────────────────────────

async def _fetch_market_data(db, symbols: list) -> list:
    """
    Return a list of asset dicts for the given symbols,
    joining market_metadata + indicators + alpha_scores.
    Mirrors _get_assets_with_indicators from custom_watchlists.py.
    """
    from sqlalchemy import text

    if not symbols:
        return []

    syms_list = list(symbols)

    # Step 1: Fetch market metadata — try with new liquidity columns, fall back if absent
    try:
        meta_rows = (await db.execute(
            text("""
                SELECT
                    m.symbol, m.name,
                    COALESCE(m.market_cap,  pwa.market_cap)  AS market_cap,
                    COALESCE(m.volume_24h,  pwa.volume_24h)  AS volume_24h,
                    m.price,
                    m.price_change_24h,
                    m.spread_pct,
                    m.orderbook_depth_usdt
                FROM market_metadata m
                LEFT JOIN (
                    SELECT DISTINCT ON (symbol)
                           symbol, market_cap, volume_24h
                    FROM   pipeline_watchlist_assets
                    WHERE  symbol = ANY(:symbols)
                    ORDER  BY symbol, entered_at DESC
                ) pwa ON pwa.symbol = m.symbol
                WHERE  m.symbol = ANY(:symbols)
            """),
            {"symbols": syms_list},
        )).fetchall()
    except Exception:
        # Fallback: columns spread_pct / orderbook_depth_usdt may not exist yet
        meta_rows = (await db.execute(
            text("""
                SELECT
                    m.symbol, m.name,
                    COALESCE(m.market_cap, pwa.market_cap) AS market_cap,
                    COALESCE(m.volume_24h, pwa.volume_24h) AS volume_24h,
                    m.price,
                    m.price_change_24h,
                    NULL AS spread_pct,
                    NULL AS orderbook_depth_usdt
                FROM market_metadata m
                LEFT JOIN (
                    SELECT DISTINCT ON (symbol)
                           symbol, market_cap, volume_24h
                    FROM   pipeline_watchlist_assets
                    WHERE  symbol = ANY(:symbols)
                    ORDER  BY symbol, entered_at DESC
                ) pwa ON pwa.symbol = m.symbol
                WHERE  m.symbol = ANY(:symbols)
            """),
            {"symbols": syms_list},
        )).fetchall()

    # Step 2: Fetch indicators — always runs regardless of which meta path was taken
    try:
        # Prefer 5m indicators (fresh, 5-min cadence); fall back to any timeframe
        ind_rows = (await db.execute(
            text("""
                SELECT DISTINCT ON (symbol) symbol, indicators_json
                FROM indicators
                WHERE symbol = ANY(:symbols)
                  AND timeframe = '5m'
                ORDER BY symbol, time DESC
            """),
            {"symbols": syms_list},
        )).fetchall()

        found_syms = {r.symbol for r in ind_rows}
        missing = [s for s in symbols if s not in found_syms]
        if missing:
            fallback_rows = (await db.execute(
                text("""
                    SELECT DISTINCT ON (symbol) symbol, indicators_json
                    FROM indicators
                    WHERE symbol = ANY(:symbols)
                    ORDER BY symbol, time DESC
                """),
                {"symbols": missing},
            )).fetchall()
            ind_rows = list(ind_rows) + list(fallback_rows)

        score_rows = (await db.execute(
            text("""
                SELECT DISTINCT ON (symbol)
                    symbol, score,
                    liquidity_score, market_structure_score,
                    momentum_score, signal_score
                FROM alpha_scores
                WHERE symbol = ANY(:symbols)
                  AND time > now() - interval '2 hours'
                ORDER BY symbol, time DESC
            """),
            {"symbols": syms_list},
        )).fetchall()

    except Exception as exc:
        logger.warning("Pipeline scan: market data fetch failed: %s", exc)
        return None

    ind_map   = {r.symbol: (r.indicators_json or {}) for r in ind_rows}
    score_map = {r.symbol: r for r in score_rows}

    assets = []
    for row in meta_rows:
        sym = row.symbol
        indicators = ind_map.get(sym, {})
        score_row  = score_map.get(sym)

        asset = {
            "symbol":    sym,
            "name":      row.name or sym,
            "price":     float(row.price)            if row.price            else 0.0,
            "change_24h": float(row.price_change_24h) if row.price_change_24h else 0.0,
            # Keep None when market_cap / volume_24h are truly unknown — the lenient filter
            # evaluation in ProfileEngine will skip conditions on None fields.
            # COALESCE in the SQL above already gives the best available value from both
            # market_metadata and pipeline_watchlist_assets, so None here is rare.
            "market_cap":           float(row.market_cap)             if row.market_cap             is not None else None,
            "volume_24h":           float(row.volume_24h)             if row.volume_24h             is not None else None,
            "spread_pct":           float(row.spread_pct)             if row.spread_pct             is not None else None,
            "orderbook_depth_usdt": float(row.orderbook_depth_usdt)   if row.orderbook_depth_usdt   is not None else None,
            "indicators": indicators,
            # Flatten numeric indicators for ProfileEngine filter evaluation
            **{k: v for k, v in indicators.items() if isinstance(v, (int, float, bool, str))},
        }

        # Add field-name aliases so profile conditions written with the GUI's
        # field names ("atr_percent") match the feature-engine output ("atr_pct").
        if "atr_pct" in asset and "atr_percent" not in asset:
            asset["atr_percent"] = asset["atr_pct"]

        # di_trend: True when DI+ > DI- (real directional confirmation).
        # Used in filter conditions ("di_trend = true") and scoring rules.
        di_plus  = asset.get("di_plus")
        di_minus = asset.get("di_minus")
        if di_plus is not None and di_minus is not None:
            try:
                asset["di_trend"] = float(di_plus) > float(di_minus)
            except (TypeError, ValueError):
                pass

        if score_row:
            asset["score"]                  = float(score_row.score)                  if score_row.score                  else 0.0
            asset["liquidity_score"]        = float(score_row.liquidity_score)        if score_row.liquidity_score        else 0.0
            asset["market_structure_score"] = float(score_row.market_structure_score) if score_row.market_structure_score else 0.0
            asset["momentum_score"]         = float(score_row.momentum_score)         if score_row.momentum_score         else 0.0
            asset["signal_score"]           = float(score_row.signal_score)           if score_row.signal_score           else 0.0

        assets.append(asset)

    return assets


# ─── level evaluators ─────────────────────────────────────────────────────────

def _apply_level_filter(assets: list, profile_config: Optional[dict], level: str, score_config: Optional[dict] = None) -> tuple[list, list]:
    """
    Apply ProfileEngine filters for a given level.
    Returns (passed, all_scored).

    score_config: when provided, overrides the ProfileEngine's internal score engine
    with the user's global /settings/score configuration.  Profile-level
    Alpha Score Weights are merged in so they are respected.
    """
    from ..services.profile_engine import ProfileEngine
    from ..services.score_engine import ScoreEngine, merge_score_config

    engine = ProfileEngine(profile_config)

    # Merge global scoring rules with profile weights so both are respected
    if score_config:
        merged = merge_score_config(score_config, profile_config)
        engine.score_engine = ScoreEngine(merged)

    min_score = 0.0

    # L2: min alpha score gate
    if level == "L2":
        min_score = float((profile_config or {}).get("filters", {}).get("min_score", 0))

    # Apply structural filters
    filtered = engine._apply_filters(assets)

    # Compute scores for all passing assets
    scored = []
    for asset in filtered:
        processed = engine._process_single_asset(asset, include_details=True)
        total = processed.get("score", {}).get("total_score", 0)
        if total >= min_score:
            scored.append({**asset, "_score": total, "_processed": processed})

    return scored, filtered


def _evaluate_l3_signals(assets: list, profile_config: Optional[dict], score_config: Optional[dict] = None) -> list:
    """
    Apply L3 signal conditions and return triggered assets.

    If the profile has NO signal conditions configured, fall back to scoring-only
    mode: return all assets that passed the profile filters, sorted by score.
    This prevents L3 from being permanently empty just because no signal conditions
    have been set up yet.

    score_config: when provided, overrides the ProfileEngine's internal score engine
    with the user's global /settings/score configuration.  Profile-level
    Alpha Score Weights are merged in so they are respected.
    """
    from ..services.profile_engine import ProfileEngine
    from ..services.score_engine import ScoreEngine, merge_score_config

    engine = ProfileEngine(profile_config)

    # Merge global scoring rules with profile weights so both are respected
    if score_config:
        merged = merge_score_config(score_config, profile_config)
        engine.score_engine = ScoreEngine(merged)

    # Check if the profile has any signal conditions at all.
    # Signal conditions may be stored under 'entry_triggers' OR 'signals'.
    sig_conditions = (
        (profile_config or {}).get("entry_triggers", {}).get("conditions") or
        (profile_config or {}).get("signals", {}).get("conditions") or
        []
    )
    has_signal_conditions = bool(sig_conditions)

    result = engine.process_watchlist(assets, include_details=True)

    if has_signal_conditions:
        # Signal evaluation mode: only return assets with triggered signals
        signals = []
        for asset in result.get("assets", []):
            sig = asset.get("signal", {})
            if sig.get("triggered"):
                signals.append({
                    "symbol":             asset["symbol"],
                    "score":              asset.get("score", {}).get("total_score", 0),
                    "price":              asset.get("price", 0),
                    "change_24h":         asset.get("change_24h", 0),
                    "volume_24h":         asset.get("volume_24h"),
                    "market_cap":         asset.get("market_cap"),
                    "matched_conditions": sig.get("matched_conditions", []),
                })
        signals.sort(key=lambda x: x["score"], reverse=True)
        return signals
    else:
        # No signal conditions — fall back to scoring mode: return all filtered
        # assets sorted by score (same as L2 behavior)
        logger.info(
            "[PipelineScan] L3: no signal conditions in profile — using scoring fallback (%d assets)",
            len(result.get("assets", [])),
        )
        fallback = []
        for asset in result.get("assets", []):
            total = asset.get("score", {}).get("total_score", 0)
            fallback.append({
                "symbol":             asset["symbol"],
                "score":              total,
                "price":              asset.get("price", 0),
                "change_24h":         asset.get("change_24h", 0),
                "volume_24h":         asset.get("volume_24h"),
                "market_cap":         asset.get("market_cap"),
                "matched_conditions": [],
            })
        fallback.sort(key=lambda x: x["score"], reverse=True)
        return fallback


# ─── DB upsert ────────────────────────────────────────────────────────────────

async def _upsert_assets(db, watchlist_id: str, assets: list, filters_json: dict | None = None):
    """Upsert current pipeline_watchlist_assets snapshot for a watchlist.

    Symbols in `assets` → INSERT or UPDATE (level_direction stays/becomes NULL).
    Symbols previously saved but not in `assets` → UPDATE level_direction = 'down'.
    Records with level_direction = 'down' older than 2h are cleaned up.
    If filters_json contains max_stay_minutes, assets older than that are expired.
    """
    from sqlalchemy import text

    now = datetime.now(timezone.utc)

    if assets:
        # Upsert active symbols (preserve entered_at on conflict)
        for a in assets:
            await db.execute(text("""
                INSERT INTO pipeline_watchlist_assets
                    (id, watchlist_id, symbol, current_price, price_change_24h,
                     volume_24h, market_cap, alpha_score, entered_at, level_direction)
                VALUES
                    (gen_random_uuid(), :wid, :sym, :price, :chg,
                     :vol, :mc, :score, :now, NULL)
                ON CONFLICT (watchlist_id, symbol)
                DO UPDATE SET
                    current_price    = EXCLUDED.current_price,
                    price_change_24h = EXCLUDED.price_change_24h,
                    volume_24h       = EXCLUDED.volume_24h,
                    market_cap       = EXCLUDED.market_cap,
                    alpha_score      = EXCLUDED.alpha_score,
                    level_direction  = NULL
            """), {
                "wid":   watchlist_id,
                "sym":   a["symbol"],
                "price": a.get("price"),
                "chg":   a.get("change_24h"),
                "vol":   a.get("volume_24h"),
                "mc":    a.get("market_cap"),
                "score": a.get("_score", a.get("score")),
                "now":   now,
            })

        # Mark symbols that are no longer passing as 'down'
        active_syms = [a["symbol"] for a in assets]
        await db.execute(
            text("""
                UPDATE pipeline_watchlist_assets
                SET level_direction = 'down',
                    level_change_at = :now
                WHERE watchlist_id = :wid
                  AND NOT (symbol = ANY(:active_syms))
                  AND (level_direction IS NULL OR level_direction != 'down')
            """),
            {"wid": watchlist_id, "now": now, "active_syms": active_syms},
        )

    else:
        # No assets passed — mark all as 'down'
        await db.execute(text("""
            UPDATE pipeline_watchlist_assets
            SET level_direction = 'down',
                level_change_at = :now
            WHERE watchlist_id = :wid
              AND (level_direction IS NULL OR level_direction != 'down')
        """), {"wid": watchlist_id, "now": now})

    # Expire assets that have exceeded max_stay_minutes (GUI-configurable per watchlist)
    max_stay = (filters_json or {}).get("max_stay_minutes")
    if max_stay:
        cutoff = now - timedelta(minutes=int(max_stay))
        await db.execute(text("""
            UPDATE pipeline_watchlist_assets
            SET level_direction = 'down',
                level_change_at = :now
            WHERE watchlist_id = :wid
              AND level_direction IS NULL
              AND entered_at < :cutoff
        """), {"wid": watchlist_id, "now": now, "cutoff": cutoff})

    # Cleanup: remove 'down' records older than 2h to keep the table lean
    await db.execute(text("""
        DELETE FROM pipeline_watchlist_assets
        WHERE watchlist_id = :wid
          AND level_direction = 'down'
          AND level_change_at < now() - interval '2 hours'
    """), {"wid": watchlist_id})

    await db.commit()


# ─── WebSocket broadcast ──────────────────────────────────────────────────────

async def _broadcast_pipeline_update(
    watchlist_id: str,
    watchlist_name: str,
    level: str,
    new_symbols: list,
    all_signals: list,
):
    """Broadcast new L3 signals via the 'signals' WebSocket channel."""
    try:
        from ..api.websocket import manager
        from datetime import datetime, timezone

        payload = {
            "type":           "pipeline_signal",
            "level":          level,
            "watchlist_id":   watchlist_id,
            "watchlist_name": watchlist_name,
            "new_signals":    new_symbols,
            "all_signals":    all_signals[:20],  # cap at 20 for WS payload
            "ts":             datetime.now(timezone.utc).isoformat(),
        }

        await manager.broadcast("signals", payload)
        logger.info(
            "[PipelineScan] Broadcast %d new L3 signals for watchlist %s",
            len(new_symbols), watchlist_name,
        )
    except Exception as exc:
        logger.warning("[PipelineScan] WebSocket broadcast failed: %s", exc)


# ─── core async pipeline ──────────────────────────────────────────────────────

async def _run_pipeline_scan():
    from ..database import CeleryAsyncSessionLocal as AsyncSessionLocal
    from ..models.pipeline_watchlist import PipelineWatchlist
    from ..models.pool import Pool, PoolCoin
    from ..models.profile import Profile
    from sqlalchemy import select

    redis = _get_redis()
    stats = {"watchlists": 0, "new_signals": 0, "errors": 0}

    async with AsyncSessionLocal() as db:
        # Load all pipeline watchlists with auto_refresh=true
        wl_rows = (await db.execute(
            select(PipelineWatchlist).where(PipelineWatchlist.auto_refresh == True)
        )).scalars().all()

        if not wl_rows:
            logger.debug("[PipelineScan] No pipeline watchlists with auto_refresh — skipping.")
            return stats

        logger.info("[PipelineScan] Processing %d pipeline watchlists…", len(wl_rows))

        for wl in wl_rows:
            try:
                stats["watchlists"] += 1
                wl_id = str(wl.id)
                level = (wl.level or "L1").upper()
                filters_json = wl.filters_json or {}

                # ── 1. Resolve symbol universe ────────────────────────────────
                symbols: list[str] = []

                def _normalize_sym(s: str) -> str:
                    """Normalize symbol to BTC_USDT format (add underscore if missing)."""
                    s = s.upper().strip()
                    if "_" not in s and s.endswith("USDT"):
                        return s[:-4] + "_USDT"
                    return s

                if wl.source_pool_id:
                    # Pool origin: use pool_coins
                    from ..utils.symbol_filters import filter_real_assets
                    coin_rows = (await db.execute(
                        select(PoolCoin).where(
                            PoolCoin.pool_id == wl.source_pool_id,
                            PoolCoin.is_active == True,
                        )
                    )).scalars().all()
                    # Normalize symbols to BTC_USDT format (market_metadata uses underscores)
                    raw_syms = [_normalize_sym(c.symbol) for c in coin_rows]
                    symbols = filter_real_assets(raw_syms)
                    logger.debug(
                        "[PipelineScan] %s (%s): pool %s → %d raw coins → %d after filter",
                        wl.name, level, wl.source_pool_id, len(raw_syms), len(symbols),
                    )

                elif wl.source_watchlist_id:
                    # Upstream watchlist: use only ACTIVE pipeline_watchlist_assets
                    # ('down' assets are stale — excluded to prevent ghost data in L2/L3)
                    from sqlalchemy import text
                    from ..utils.symbol_filters import filter_real_assets
                    asset_rows = (await db.execute(text("""
                        SELECT symbol FROM pipeline_watchlist_assets
                        WHERE watchlist_id = :wid
                          AND (level_direction IS NULL OR level_direction = 'up')
                        ORDER BY alpha_score DESC NULLS LAST
                    """), {"wid": str(wl.source_watchlist_id)})).fetchall()
                    symbols = filter_real_assets([_normalize_sym(r.symbol) for r in asset_rows])
                    logger.debug(
                        "[PipelineScan] %s (%s): upstream watchlist %s → %d symbols",
                        wl.name, level, wl.source_watchlist_id, len(symbols),
                    )

                if not symbols:
                    # L1 fallback: when no source is configured, use all
                    # active coins across the user's pools as the scan universe.
                    if level == "L1" and not wl.source_pool_id and not wl.source_watchlist_id:
                        from ..utils.symbol_filters import filter_real_assets as _filt
                        all_coins = (await db.execute(
                            select(PoolCoin)
                            .join(Pool, PoolCoin.pool_id == Pool.id)
                            .where(
                                Pool.user_id == wl.user_id,
                                Pool.is_active == True,
                                PoolCoin.is_active == True,
                            )
                        )).scalars().all()
                        symbols = _filt([c.symbol for c in all_coins])
                        if symbols:
                            logger.info(
                                "[PipelineScan] %s (L1): no source configured — using "
                                "%d coins from all user pools as fallback universe.",
                                wl.name, len(symbols),
                            )

                if not symbols:
                    logger.debug("[PipelineScan] %s (%s): no symbols — skipping.", wl.name, level)
                    continue

                # ── 2. Fetch market data ──────────────────────────────────────
                assets = await _fetch_market_data(db, symbols)
                if assets is None:
                    logger.warning("[PipelineScan] %s (%s): market data fetch error — skipping.", wl.name, level)
                    continue
                if not assets:
                    logger.warning(
                        "[PipelineScan] %s (%s): no market data found for %d requested symbols "
                        "(sample: %s) — skipping to preserve existing watchlist entries.",
                        wl.name, level, len(symbols), symbols[:5],
                    )
                    continue  # Don't wipe the watchlist when market data is temporarily unavailable

                # ── 3. Load profile config ────────────────────────────────────
                profile_config: Optional[dict] = None
                if wl.profile_id:
                    prof = (await db.execute(
                        select(Profile).where(Profile.id == wl.profile_id)
                    )).scalars().first()
                    if prof:
                        # .config always holds filters/signals conditions; preset_ia_config is IA metadata only
                        profile_config = prof.config

                # ── 3b. Load global score config (/settings/score) ────────────
                # This ensures Alpha Score respects the user's configured scoring rules.
                score_config: Optional[dict] = None
                try:
                    from ..services.config_service import config_service
                    from ..services.seed_service import DEFAULT_SCORE
                    score_config = await config_service.get_config(db, "score", wl.user_id)
                    if not score_config:
                        score_config = DEFAULT_SCORE
                except Exception:
                    from ..services.seed_service import DEFAULT_SCORE
                    score_config = DEFAULT_SCORE

                # ── 4. Per-level evaluation ───────────────────────────────────
                # Treat 'CUSTOM' level same as L1 (filters + scoring, no signal requirement)
                # NOTE: filters_json on the watchlist is IGNORED. All filtering
                # (including min_score, require_signal) comes from the profile.
                effective_level = level if level in ("L1", "L2", "L3") else "L1"

                # Remove assets blocked by anti-bad-entry rules (shared utility)
                from ..utils.blocking_rules import is_blocked as _is_blocked

                if effective_level in ("L1", "L2"):
                    passed, _ = _apply_level_filter(assets, profile_config, effective_level, score_config=score_config)

                    before_block = len(passed)
                    passed = [a for a in passed if not _is_blocked(a)]
                    if before_block != len(passed):
                        logger.info(
                            "[PipelineScan] %s (%s): anti-bad-entry removed %d/%d assets",
                            wl.name, level, before_block - len(passed), before_block,
                        )

                    await _upsert_assets(db, wl_id, passed, filters_json)

                elif effective_level == "L3":
                    signals = _evaluate_l3_signals(assets, profile_config, score_config=score_config)

                    before_block = len(signals)
                    signals = [s for s in signals if not _is_blocked(s)]
                    if before_block != len(signals):
                        logger.info(
                            "[PipelineScan] %s (L3): anti-bad-entry removed %d/%d signals",
                            wl.name, before_block - len(signals), before_block,
                        )

                    # ── 5. Detect new signals ─────────────────────────────────
                    current_set = {s["symbol"] for s in signals}
                    prior_set   = _prior_signals(redis, wl_id)
                    new_syms    = sorted(current_set - prior_set)

                    _save_signals(redis, wl_id, current_set)
                    await _upsert_assets(db, wl_id, signals, filters_json)

                    if new_syms:
                        stats["new_signals"] += len(new_syms)
                        logger.info(
                            "[PipelineScan] 🚨 New L3 signals in %s: %s",
                            wl.name, new_syms,
                        )
                        await _broadcast_pipeline_update(
                            watchlist_id=wl_id,
                            watchlist_name=wl.name,
                            level="L3",
                            new_symbols=new_syms,
                            all_signals=signals,
                        )

            except Exception as exc:
                logger.exception("[PipelineScan] Error processing watchlist %s: %s", wl.name, exc)
                stats["errors"] += 1
                continue

    logger.info(
        "[PipelineScan] Done — watchlists=%d  new_signals=%d  errors=%d",
        stats["watchlists"], stats["new_signals"], stats["errors"],
    )
    return stats


# ─── Celery task ──────────────────────────────────────────────────────────────

@celery_app.task(name="app.tasks.pipeline_scan.scan", bind=True, max_retries=0)
def scan(self):
    """Periodic pipeline scan — L1 filter → L2 ranking → L3 signals (5 min)."""
    logger.info("[PipelineScan] Starting pipeline scan…")
    try:
        result = _run_async(_run_pipeline_scan())
        logger.info("[PipelineScan] Result: %s", result)
        return result
    except Exception as exc:
        logger.exception("[PipelineScan] Fatal error: %s", exc)
        raise
