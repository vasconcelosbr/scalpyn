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

    symbols_sql = ",".join(f"'{s}'" for s in symbols)

    try:
        meta_rows = (await db.execute(text(f"""
            SELECT symbol, name, market_cap, volume_24h, price, price_change_24h
            FROM market_metadata
            WHERE symbol IN ({symbols_sql})
        """))).fetchall()

        # Prefer 5m indicators (fresh, 5-min cadence); fall back to any timeframe
        ind_rows = (await db.execute(text(f"""
            SELECT DISTINCT ON (symbol) symbol, indicators_json
            FROM indicators
            WHERE symbol IN ({symbols_sql})
              AND timeframe = '5m'
            ORDER BY symbol, time DESC
        """))).fetchall()

        found_syms = {r.symbol for r in ind_rows}
        missing = [s for s in symbols if s not in found_syms]
        if missing:
            missing_sql = ",".join(f"'{s}'" for s in missing)
            fallback_rows = (await db.execute(text(f"""
                SELECT DISTINCT ON (symbol) symbol, indicators_json
                FROM indicators
                WHERE symbol IN ({missing_sql})
                ORDER BY symbol, time DESC
            """))).fetchall()
            ind_rows = list(ind_rows) + list(fallback_rows)

        score_rows = (await db.execute(text(f"""
            SELECT DISTINCT ON (symbol)
                symbol, score,
                liquidity_score, market_structure_score,
                momentum_score, signal_score
            FROM alpha_scores
            WHERE symbol IN ({symbols_sql})
              AND time > now() - interval '2 hours'
            ORDER BY symbol, time DESC
        """))).fetchall()

    except Exception as exc:
        logger.warning("Pipeline scan: market data fetch failed: %s", exc)
        return []

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
            "market_cap": float(row.market_cap)       if row.market_cap       else 0.0,
            "volume_24h": float(row.volume_24h)       if row.volume_24h       else 0.0,
            "indicators": indicators,
            # Flatten numeric indicators for ProfileEngine filter evaluation
            **{k: v for k, v in indicators.items() if isinstance(v, (int, float, bool, str))},
        }

        if score_row:
            asset["score"]                  = float(score_row.score)                  if score_row.score                  else 0.0
            asset["liquidity_score"]        = float(score_row.liquidity_score)        if score_row.liquidity_score        else 0.0
            asset["market_structure_score"] = float(score_row.market_structure_score) if score_row.market_structure_score else 0.0
            asset["momentum_score"]         = float(score_row.momentum_score)         if score_row.momentum_score         else 0.0
            asset["signal_score"]           = float(score_row.signal_score)           if score_row.signal_score           else 0.0

        assets.append(asset)

    return assets


# ─── level evaluators ─────────────────────────────────────────────────────────

def _apply_level_filter(assets: list, profile_config: Optional[dict], level: str) -> tuple[list, list]:
    """
    Apply ProfileEngine filters for a given level.
    Returns (passed, all_scored).
    """
    from ..services.profile_engine import ProfileEngine

    engine = ProfileEngine(profile_config)
    filters_config = (profile_config or {}).get("filters", {})
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


def _evaluate_l3_signals(assets: list, profile_config: Optional[dict]) -> list:
    """
    Apply L3 signal conditions and return only assets with triggered signals.
    """
    from ..services.profile_engine import ProfileEngine

    engine = ProfileEngine(profile_config)
    result = engine.process_watchlist(assets, include_details=True)

    signals = []
    for asset in result.get("assets", []):
        sig = asset.get("signal", {})
        if sig.get("triggered"):
            signals.append({
                "symbol":             asset["symbol"],
                "score":              asset.get("score", {}).get("total_score", 0),
                "price":              asset.get("price", 0),
                "change_24h":         asset.get("change_24h", 0),
                "matched_conditions": sig.get("matched_conditions", []),
            })

    signals.sort(key=lambda x: x["score"], reverse=True)
    return signals


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
        active_syms_sql = ",".join(f"'{a['symbol']}'" for a in assets)
        await db.execute(text(f"""
            UPDATE pipeline_watchlist_assets
            SET level_direction = 'down',
                level_change_at = :now
            WHERE watchlist_id = :wid
              AND symbol NOT IN ({active_syms_sql})
              AND (level_direction IS NULL OR level_direction != 'down')
        """), {"wid": watchlist_id, "now": now})

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
    from ..database import AsyncSessionLocal
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

                if wl.source_pool_id:
                    # Pool origin: use pool_coins
                    coin_rows = (await db.execute(
                        select(PoolCoin).where(
                            PoolCoin.pool_id == wl.source_pool_id,
                            PoolCoin.is_active == True,
                        )
                    )).scalars().all()
                    symbols = [c.symbol for c in coin_rows]

                elif wl.source_watchlist_id:
                    # Upstream watchlist: use pipeline_watchlist_assets
                    from sqlalchemy import text
                    asset_rows = (await db.execute(text("""
                        SELECT symbol FROM pipeline_watchlist_assets
                        WHERE watchlist_id = :wid
                        ORDER BY alpha_score DESC NULLS LAST
                    """), {"wid": str(wl.source_watchlist_id)})).fetchall()
                    symbols = [r.symbol for r in asset_rows]

                if not symbols:
                    logger.debug("[PipelineScan] %s (%s): no symbols — skipping.", wl.name, level)
                    continue

                # ── 2. Fetch market data ──────────────────────────────────────
                assets = await _fetch_market_data(db, symbols)
                if not assets:
                    logger.debug("[PipelineScan] %s (%s): no market data.", wl.name, level)
                    continue

                # ── 3. Load profile config ────────────────────────────────────
                profile_config: Optional[dict] = None
                if wl.profile_id:
                    prof = (await db.execute(
                        select(Profile).where(Profile.id == wl.profile_id)
                    )).scalars().first()
                    if prof:
                        # preset_ia_config holds filter/signal conditions; fallback to config
                        profile_config = prof.preset_ia_config or prof.config

                # ── 4. Per-level evaluation ───────────────────────────────────
                if level in ("L1", "L2"):
                    passed, _ = _apply_level_filter(assets, profile_config, level)

                    # Apply level-specific min_score gate
                    min_score = float(filters_json.get("min_score", 0))
                    if min_score > 0:
                        passed = [a for a in passed if a.get("_score", 0) >= min_score]

                    await _upsert_assets(db, wl_id, passed, filters_json)

                elif level == "L3":
                    # L3: signals + optional min_score gate
                    min_score = float(filters_json.get("min_score", 0))
                    require_signal = filters_json.get("require_signal", True)

                    signals = _evaluate_l3_signals(assets, profile_config)

                    if min_score > 0:
                        signals = [s for s in signals if s.get("score", 0) >= min_score]

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
                            new_signals=new_syms,
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
