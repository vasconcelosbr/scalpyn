"""Pipeline Watchlist API — CRUD + live results from pipeline_scan task.

Endpoints:
  GET  /api/pipeline                       → list all pipeline watchlists
  POST /api/pipeline                       → create pipeline watchlist
  GET  /api/pipeline/{wl_id}               → get watchlist details
  PUT  /api/pipeline/{wl_id}               → update watchlist
  DELETE /api/pipeline/{wl_id}             → delete watchlist
  GET  /api/pipeline/{wl_id}/assets        → live assets (from pipeline_watchlist_assets)
  POST /api/pipeline/{wl_id}/refresh       → trigger immediate scan for this watchlist
"""

import logging
from typing import Any, Dict, List, Optional, Set
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models.pipeline_watchlist import PipelineWatchlist, PipelineWatchlistAsset
from ..models.profile import Profile
from ..models.pool import Pool
from .config import get_current_user_id
from ..services.rule_engine import RuleEngine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/watchlists", tags=["Pipeline Watchlists"])

# ─── Field label maps ──────────────────────────────────────────────────────────

_META_FIELDS: Dict[str, Dict[str, str]] = {
    "market_cap":  {"key": "_meta:market_cap",  "label": "Mkt Cap"},
    "volume_24h":  {"key": "_meta:volume_24h",  "label": "Vol 24h"},
    "price":       {"key": "_meta:price",        "label": "Price"},
    "change_24h":  {"key": "_meta:change_24h",   "label": "24h%"},
    "score":       {"key": "_meta:score",         "label": "Alpha"},
}

_INDICATOR_LABELS: Dict[str, str] = {
    "rsi":                    "RSI",
    "adx":                    "ADX",
    "macd":                   "MACD",
    "macd_histogram":         "MACD Hist",
    "stoch_k":                "Stoch %K",
    "stoch_d":                "Stoch %D",
    "bb_width":               "BB Width",
    "atr":                    "ATR",
    "atr_percent":            "ATR%",
    "obv":                    "OBV",
    "vwap_distance_pct":      "VWAP%",
    "zscore":                 "Z-Score",
    "di_plus":                "DI+",
    "di_minus":               "DI-",
    "volume_spike":           "Vol Spike",
    "ema_full_alignment":     "EMA Align",
    "ema9_gt_ema50":          "EMA9>50",
    "ema50_gt_ema200":        "EMA50>200",
    "psar_trend":             "PSAR",
    "macd_signal":            "MACD Sig",
    "liquidity_score":        "Liq Score",
    "momentum_score":         "Mom Score",
    "market_structure_score": "Mkt Str",
    "signal_score":           "Sig Score",
    "spread_pct":             "Spread%",
    "atr_pct":                "ATR% (legacy)",
}


# ─── helpers ──────────────────────────────────────────────────────────────────

def _wl_to_dict(wl: PipelineWatchlist) -> Dict[str, Any]:
    return {
        "id":                   str(wl.id),
        "name":                 wl.name,
        "level":                wl.level,
        "source_pool_id":       str(wl.source_pool_id)       if wl.source_pool_id       else None,
        "source_watchlist_id":  str(wl.source_watchlist_id)  if wl.source_watchlist_id  else None,
        "profile_id":           str(wl.profile_id)           if wl.profile_id           else None,
        "auto_refresh":         wl.auto_refresh,
        "filters_json":         wl.filters_json or {},
        "created_at":           wl.created_at.isoformat()    if wl.created_at           else None,
        "updated_at":           wl.updated_at.isoformat()    if wl.updated_at           else None,
    }


async def _get_own_wl(db: AsyncSession, wl_id: UUID, user_id: UUID) -> PipelineWatchlist:
    result = await db.execute(
        select(PipelineWatchlist).where(
            PipelineWatchlist.id == wl_id,
            PipelineWatchlist.user_id == user_id,
        )
    )
    wl = result.scalars().first()
    if not wl:
        raise HTTPException(status_code=404, detail="Pipeline watchlist not found")
    return wl


# ─── CRUD ─────────────────────────────────────────────────────────────────────

@router.get("/")
async def list_pipeline_watchlists(
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """List all pipeline watchlists for the current user."""
    rows = (await db.execute(
        select(PipelineWatchlist)
        .where(PipelineWatchlist.user_id == user_id)
        .order_by(PipelineWatchlist.level, PipelineWatchlist.created_at)
    )).scalars().all()
    return {"watchlists": [_wl_to_dict(wl) for wl in rows]}


@router.post("/")
async def create_pipeline_watchlist(
    payload: Dict[str, Any],
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """
    Create a pipeline watchlist.

    Body fields:
      name                (str, required)
      level               "L1" | "L2" | "L3"
      source_pool_id      UUID of a Pool   (L1 sources from Pool)
      source_watchlist_id UUID of another PipelineWatchlist (L2/L3)
      profile_id          UUID of a Profile to apply
      auto_refresh        bool (default true)
      filters_json        {} (DEPRECATED — filtering is driven by the profile)
    """
    name = payload.get("name")
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    level = (payload.get("level") or "L1").upper()
    if level not in ("L1", "L2", "L3"):
        raise HTTPException(status_code=400, detail="level must be L1, L2 or L3")

    wl = PipelineWatchlist(
        user_id=user_id,
        name=name,
        level=level,
        source_pool_id=payload.get("source_pool_id"),
        source_watchlist_id=payload.get("source_watchlist_id"),
        profile_id=payload.get("profile_id"),
        auto_refresh=payload.get("auto_refresh", True),
        filters_json=payload.get("filters_json", {}),
    )
    db.add(wl)
    await db.commit()
    await db.refresh(wl)
    return _wl_to_dict(wl)


@router.get("/{wl_id}")
async def get_pipeline_watchlist(
    wl_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    wl = await _get_own_wl(db, wl_id, user_id)
    return _wl_to_dict(wl)


@router.put("/{wl_id}")
async def update_pipeline_watchlist(
    wl_id: UUID,
    payload: Dict[str, Any],
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    wl = await _get_own_wl(db, wl_id, user_id)

    for field in ("name", "level", "source_pool_id", "source_watchlist_id",
                  "profile_id", "auto_refresh", "filters_json"):
        if field in payload:
            setattr(wl, field, payload[field])

    await db.commit()
    await db.refresh(wl)
    return _wl_to_dict(wl)


@router.delete("/{wl_id}")
async def delete_pipeline_watchlist(
    wl_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    wl = await _get_own_wl(db, wl_id, user_id)
    await db.delete(wl)
    await db.commit()
    return {"status": "deleted", "id": str(wl_id)}


# ─── Live assets ──────────────────────────────────────────────────────────────

@router.get("/{wl_id}/assets")
async def get_pipeline_assets(
    wl_id: UUID,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """
    Return the current asset snapshot stored by the pipeline_scan task,
    enriched with live indicator values and profile-derived column definitions.
    """
    wl = await _get_own_wl(db, wl_id, user_id)

    # ── 1. Asset rows ──────────────────────────────────────────────────────────
    rows = (await db.execute(text("""
        SELECT symbol, current_price, price_change_24h,
               volume_24h, market_cap, alpha_score, entered_at,
               level_direction, previous_level, level_change_at
        FROM   pipeline_watchlist_assets
        WHERE  watchlist_id = :wid
        ORDER  BY alpha_score DESC NULLS LAST
        LIMIT  :limit
    """), {"wid": str(wl_id), "limit": limit})).fetchall()

    from ..utils.symbol_filters import is_leveraged_token
    rows = [r for r in rows if not is_leveraged_token(r.symbol)]
    symbols = [r.symbol for r in rows]

    # ── 2. Profile → filter conditions ───────────────────────────────────────
    filter_conditions: List[Dict] = []
    filter_logic: str = "AND"
    profile_config: Optional[Dict[str, Any]] = None
    # Columns come from Filter conditions ONLY (not Signals), per new contract.
    # Signals conditions continue to be used for signal evaluation but not
    # for deriving dynamic watchlist columns.
    profile_filter_fields: List[str] = []  # preserves insertion order for column display

    if wl.profile_id:
        prof = (await db.execute(
            select(Profile).where(Profile.id == wl.profile_id)
        )).scalars().first()

        if prof and prof.config:
            cfg = prof.config
            profile_config = cfg
            filter_cfg = cfg.get("filters", {}) or {}
            filter_conditions = filter_cfg.get("conditions", [])
            filter_logic = filter_cfg.get("logic", "AND")

            # Only use filter fields for column definitions
            seen_filter_fields: Set[str] = set()
            for cond in filter_conditions:
                f = cond.get("field", "")
                if f and f not in seen_filter_fields:
                    profile_filter_fields.append(f)
                    seen_filter_fields.add(f)

    # Alpha Score visibility: show only for L2 and L3 (Stage 2 and Stage 3).
    # POOL/custom (Stage 0) and L1 (Stage 1) are pure filter stages.
    show_score = (wl.level or "").upper() in {"L2", "L3"}

    # ── 3. Fetch FULL indicator data from DB ──────────────────────────────────
    ind_map: Dict[str, Dict] = {}
    all_ind_keys: set = set()
    if symbols:
        try:
            ind_rows = (await db.execute(text("""
                SELECT DISTINCT ON (symbol) symbol, indicators_json
                FROM   indicators
                WHERE  symbol = ANY(:syms)
                  AND  timeframe = '5m'
                ORDER  BY symbol, time DESC
            """), {"syms": symbols})).fetchall()

            found = {r.symbol for r in ind_rows}
            missing_syms = [s for s in symbols if s not in found]
            if missing_syms:
                fb = (await db.execute(text("""
                    SELECT DISTINCT ON (symbol) symbol, indicators_json
                    FROM   indicators
                    WHERE  symbol = ANY(:syms)
                    ORDER  BY symbol, time DESC
                """), {"syms": missing_syms})).fetchall()
                ind_rows = list(ind_rows) + list(fb)

            for r in ind_rows:
                j = r.indicators_json or {}
                # Keep numeric AND boolean indicators (booleans needed for EMA trend display)
                numeric = {k: v for k, v in j.items() if isinstance(v, (int, float, bool))}
                ind_map[r.symbol] = numeric
                all_ind_keys.update(k for k, v in numeric.items() if isinstance(v, (int, float)))
        except Exception as exc:
            logger.warning("pipeline assets: indicator fetch failed: %s", exc)

    # ── 3b. Build dynamic column definitions ─────────────────────────────────
    # Columns are derived from Filter Conditions only (preserving order).
    profile_indicators: List[Dict[str, str]] = []
    seen_cols: set = set()

    for field in profile_filter_fields:
        if field in _META_FIELDS:
            m = _META_FIELDS[field]
            profile_indicators.append({"key": m["key"], "label": m["label"], "field": field})
        else:
            label = _INDICATOR_LABELS.get(field, field.upper())
            profile_indicators.append({"key": field, "label": label, "field": field})
        seen_cols.add(field)

    # ── 3c. Load score engine for per-asset rule breakdown ────────────────────
    # Uses a direct DB query with wl.user_id (same user as pipeline_scan uses)
    # to avoid Redis/cache issues and UUID type-mismatch with config_service.
    se = None
    try:
        from ..models.config_profile import ConfigProfile as _CP
        from ..services.score_engine import ScoreEngine, merge_score_config
        from ..services.seed_service import DEFAULT_SCORE
        # Prefer watchlist owner's config (same path as pipeline_scan)
        _cp_row = (await db.execute(
            select(_CP).where(
                _CP.user_id == wl.user_id,
                _CP.pool_id.is_(None),
                _CP.config_type == "score",
            ).order_by(_CP.updated_at.desc()).limit(1)
        )).scalars().first()
        sc = _cp_row.config_json if _cp_row and _cp_row.config_json else DEFAULT_SCORE
        se = ScoreEngine(merge_score_config(sc, profile_config))
    except Exception as exc:
        logger.warning("pipeline assets: score engine init failed [%s]: %s", type(exc).__name__, exc)

    # ── 4. Build response — apply profile filter at query time ────────────────
    rule_engine = RuleEngine() if filter_conditions else None

    assets = []
    for r in rows:
        sym = r.symbol
        ind_data = ind_map.get(sym, {})
        stored_score = float(r.alpha_score) if r.alpha_score is not None else None

        # Build evaluation dict (meta fields + live indicator fields)
        eval_dict: Dict[str, Any] = {
            "market_cap":         float(r.market_cap)       if r.market_cap       is not None else None,
            "volume_24h":         float(r.volume_24h)       if r.volume_24h       is not None else None,
            "price":              float(r.current_price)    if r.current_price    is not None else None,
            "change_24h":         float(r.price_change_24h) if r.price_change_24h is not None else None,
            "price_change_24h":   float(r.price_change_24h) if r.price_change_24h is not None else None,
            **ind_data,
        }

        score_result = se.compute_alpha_score(eval_dict) if se else None
        fresh_score = (
            float(score_result.get("total_score"))
            if score_result and score_result.get("total_score") is not None
            else stored_score if stored_score is not None else 0.0
        )
        eval_dict["score"] = fresh_score

        if (
            stored_score is not None
            and fresh_score is not None
            and abs(stored_score - fresh_score) >= 0.1
        ):
            logger.debug(
                "pipeline assets: score drift watchlist=%s symbol=%s stored=%.2f fresh=%.2f",
                wl_id,
                sym,
                stored_score,
                fresh_score,
            )

        # Re-evaluate profile filter at query time.
        # STRICT fields (market_cap, volume_24h, etc.): always evaluated — None → FAIL.
        # Technical indicator fields: skipped when None (may not be computed yet).
        _STRICT_META = frozenset({
            "market_cap", "volume_24h", "price",
            "change_24h", "price_change_24h", "spread_pct",
        })
        if rule_engine and filter_conditions:
            applicable = [
                c for c in filter_conditions
                if "group" in c
                or eval_dict.get(c.get("field")) is not None
                or c.get("field", "") in _STRICT_META  # always include strict meta
            ]
            if applicable:
                result = rule_engine.evaluate(applicable, eval_dict, filter_logic)
                if not result["passed"]:
                    continue  # Filter violation — hide asset

        # Flat indicators dict keyed by col.key (for dynamic table columns)
        indicators: Dict[str, Any] = {
            "_meta:market_cap":  eval_dict["market_cap"],
            "_meta:volume_24h":  eval_dict["volume_24h"],
            "_meta:price":       eval_dict["price"],
            "_meta:change_24h":  eval_dict["change_24h"],
            "_meta:score":       fresh_score,
        }
        indicators.update(ind_data)

        # Compute per-rule scoring breakdown for drilldown / transparency
        score_rules = se.get_full_breakdown(eval_dict) if se else []

        assets.append({
            "id":               sym,
            "watchlist_id":     str(wl_id),
            "symbol":           sym,
            "current_price":    eval_dict["price"],
            "price_change_24h": eval_dict["change_24h"],
            "volume_24h":       eval_dict["volume_24h"],
            "market_cap":       eval_dict["market_cap"],
            "alpha_score":      fresh_score,
            "score_classification": (
                score_result.get("classification") if score_result else "no_data"
            ),
            "entered_at":       r.entered_at.isoformat()  if r.entered_at       else None,
            "level_direction":  r.level_direction,
            "previous_level":   r.previous_level,
            "level_change_at":  r.level_change_at.isoformat() if r.level_change_at else None,
            "indicators":       indicators,
            "score_rules":      score_rules,
        })

    assets.sort(
        key=lambda asset: (
            asset["alpha_score"] is None,
            -asset["alpha_score"] if asset["alpha_score"] is not None else 0,
        )
    )

    return {
        "watchlist_id":      str(wl_id),
        "watchlist_name":    wl.name,
        "level":             wl.level,
        "asset_count":       len(assets),
        "assets":            assets,
        "profile_indicators": profile_indicators,
        "score_thresholds":  se.thresholds if se else None,
        # Alpha Score is visible only at L2 (Stage 2) and L3 (Stage 3).
        # POOL/custom (Stage 0) and L1 (Stage 1) are filter-only stages.
        "show_score":        show_score,
    }


# ─── Manual refresh ───────────────────────────────────────────────────────────

@router.post("/{wl_id}/refresh")
async def refresh_pipeline_watchlist(
    wl_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """
    Trigger an immediate pipeline evaluation for this watchlist
    (runs inline, not via Celery — useful for testing and manual refresh).
    """
    from ..tasks.pipeline_scan import _run_pipeline_scan

    wl = await _get_own_wl(db, wl_id, user_id)

    import asyncio
    try:
        # Run only for this specific watchlist by executing the full scan
        # (the scan itself is efficient — it queries all watchlists at once)
        stats = await _run_pipeline_scan()
        assets_result = await get_pipeline_assets(wl_id, 100, db, user_id)
        return {
            "status":   "refreshed",
            "stats":    stats,
            "assets":   assets_result["assets"],
            "count":    assets_result["asset_count"],
        }
    except Exception as exc:
        logger.exception("Manual pipeline refresh failed for %s: %s", wl_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ─── Debug endpoint ────────────────────────────────────────────────────────────

@router.get("/{wl_id}/debug")
async def debug_pipeline_watchlist(
    wl_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """
    Return a full observability report for this pipeline watchlist.
    Shows exactly why assets are dropped at each pipeline stage.

    Returns:
      - pool_coins_total: number of coins in the source pool
      - symbols_after_filter: after filter_real_assets (removes leveraged tokens)
      - symbols_with_market_data: how many pool coins exist in market_metadata
      - symbols_missing_market_data: list of symbols not found in market_metadata
      - symbols_passing_profile_filters: how many pass the profile conditions
      - symbols_in_watchlist: how many are currently in pipeline_watchlist_assets
      - filter_drop_reasons: breakdown per condition of how many assets were dropped
    """
    from ..models.pool import PoolCoin
    from ..utils.symbol_filters import filter_real_assets
    from ..services.profile_engine import ProfileEngine
    from sqlalchemy import select, text

    wl = await _get_own_wl(db, wl_id, user_id)

    report: dict = {
        "watchlist_id":   str(wl_id),
        "watchlist_name": wl.name,
        "level":          wl.level,
        "source_pool_id": str(wl.source_pool_id) if wl.source_pool_id else None,
        "profile_id":     str(wl.profile_id) if wl.profile_id else None,
        "stages": {},
        "filter_drop_reasons": [],
        "symbols_missing_market_data": [],
        "error": None,
    }

    try:
        # ── Stage 1: Pool coins ───────────────────────────────────────────────
        raw_symbols: list[str] = []
        if wl.source_pool_id:
            coin_rows = (await db.execute(
                select(PoolCoin).where(
                    PoolCoin.pool_id == wl.source_pool_id,
                    PoolCoin.is_active == True,
                )
            )).scalars().all()
            raw_symbols = [c.symbol for c in coin_rows]
        elif wl.source_watchlist_id:
            asset_rows = (await db.execute(text("""
                SELECT symbol FROM pipeline_watchlist_assets
                WHERE watchlist_id = :wid
                  AND (level_direction IS NULL OR level_direction = 'up')
            """), {"wid": str(wl.source_watchlist_id)})).fetchall()
            raw_symbols = [r.symbol for r in asset_rows]

        report["stages"]["1_pool_coins_total"] = len(raw_symbols)

        # ── Stage 2: After leveraged-token filter ─────────────────────────────
        filtered_symbols = filter_real_assets(raw_symbols)
        removed_by_filter = [s for s in raw_symbols if s not in set(filtered_symbols)]
        report["stages"]["2_after_filter_real_assets"] = len(filtered_symbols)
        report["stages"]["2_removed_leveraged_tokens"] = removed_by_filter

        # ── Stage 3: Market metadata coverage ────────────────────────────────
        if filtered_symbols:
            meta_rows = (await db.execute(text("""
                SELECT symbol FROM market_metadata
                WHERE symbol = ANY(:syms)
            """), {"syms": filtered_symbols})).fetchall()
            found_in_meta = {r.symbol for r in meta_rows}
            missing_from_meta = [s for s in filtered_symbols if s not in found_in_meta]
            report["stages"]["3_symbols_with_market_data"] = len(found_in_meta)
            report["stages"]["3_symbols_missing_market_data"] = len(missing_from_meta)
            report["symbols_missing_market_data"] = missing_from_meta[:50]  # cap list at 50
        else:
            found_in_meta = set()
            report["stages"]["3_symbols_with_market_data"] = 0
            report["stages"]["3_symbols_missing_market_data"] = 0

        # ── Stage 4: Profile filter pass rate ────────────────────────────────
        profile_config = None
        if wl.profile_id:
            from ..models.profile import Profile as _Prof
            prof = (await db.execute(
                select(_Prof).where(_Prof.id == wl.profile_id)
            )).scalars().first()
            if prof:
                profile_config = prof.config

        symbols_in_meta = list(found_in_meta)
        filter_pass_count = 0
        condition_drop_map: dict[str, int] = {}

        if symbols_in_meta and profile_config:
            # Fetch market data for symbols that have meta
            from ..tasks.pipeline_scan import _fetch_market_data
            assets = await _fetch_market_data(db, symbols_in_meta)
            if assets:
                engine = ProfileEngine(profile_config)
                conditions = (profile_config.get("filters", {}) or {}).get("conditions", [])
                for asset in assets:
                    passed = engine._apply_filters([asset], strict_indicators=True)
                    if passed:
                        filter_pass_count += 1
                    else:
                        # Find which condition failed
                        from ..services.rule_engine import RuleEngine
                        re = RuleEngine()
                        for cond in conditions:
                            field = cond.get("field", "unknown")
                            result = re.evaluate([cond], asset, "AND")
                            if not result.get("passed"):
                                condition_drop_map[field] = condition_drop_map.get(field, 0) + 1

        report["stages"]["4_symbols_passing_profile_filters"] = filter_pass_count
        if condition_drop_map:
            report["filter_drop_reasons"] = [
                {"field": k, "assets_dropped": v}
                for k, v in sorted(condition_drop_map.items(), key=lambda x: -x[1])
            ]

        # ── Stage 5: Current watchlist state ─────────────────────────────────
        asset_count_row = (await db.execute(text("""
            SELECT
                COUNT(*) FILTER (WHERE level_direction IS NULL OR level_direction = 'up') AS active_count,
                COUNT(*) FILTER (WHERE level_direction = 'down') AS down_count
            FROM pipeline_watchlist_assets
            WHERE watchlist_id = :wid
        """), {"wid": str(wl_id)})).fetchone()

        report["stages"]["5_active_in_watchlist"] = asset_count_row.active_count if asset_count_row else 0
        report["stages"]["5_down_in_watchlist"] = asset_count_row.down_count if asset_count_row else 0

        # ── Summary ───────────────────────────────────────────────────────────
        report["summary"] = (
            f"Pool: {report['stages'].get('1_pool_coins_total', 0)} coins → "
            f"filter: {report['stages'].get('2_after_filter_real_assets', 0)} → "
            f"market data: {report['stages'].get('3_symbols_with_market_data', 0)} → "
            f"profile filter: {report['stages'].get('4_symbols_passing_profile_filters', 0)} → "
            f"watchlist: {report['stages'].get('5_active_in_watchlist', 0)} active"
        )

    except Exception as exc:
        logger.exception("debug_pipeline_watchlist failed for %s: %s", wl_id, exc)
        report["error"] = str(exc)

    return report
