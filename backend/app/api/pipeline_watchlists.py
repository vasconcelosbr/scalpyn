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
from typing import Any, Dict, List, Optional
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
      filters_json        {"min_score": 60, "require_signal": true}
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
    profile_cond_fields: set = set()

    if wl.profile_id:
        prof = (await db.execute(
            select(Profile).where(Profile.id == wl.profile_id)
        )).scalars().first()

        if prof and prof.config:
            cfg = prof.config
            filter_cfg = cfg.get("filters", {}) or {}
            filter_conditions = filter_cfg.get("conditions", [])
            filter_logic = filter_cfg.get("logic", "AND")

            all_conds: List[Dict] = list(filter_conditions)
            all_conds += (cfg.get("signals", {}) or {}).get("conditions", [])
            for cond in all_conds:
                f = cond.get("field", "")
                if f:
                    profile_cond_fields.add(f)

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
    profile_indicators: List[Dict[str, str]] = []
    seen_cols: set = set()

    for field in sorted(profile_cond_fields):
        if field in _META_FIELDS:
            m = _META_FIELDS[field]
            profile_indicators.append({"key": m["key"], "label": m["label"], "field": field})
        else:
            label = _INDICATOR_LABELS.get(field, field.upper())
            profile_indicators.append({"key": field, "label": label, "field": field})
        seen_cols.add(field)

    for field in sorted(all_ind_keys):
        if field in seen_cols or field in _META_FIELDS:
            continue
        label = _INDICATOR_LABELS.get(field, field.replace("_", " ").title())
        profile_indicators.append({"key": field, "label": label, "field": field})
        seen_cols.add(field)

    # ── 3c. Load score engine for per-asset rule breakdown ────────────────────
    # Uses a direct DB query with wl.user_id (same user as pipeline_scan uses)
    # to avoid Redis/cache issues and UUID type-mismatch with config_service.
    se = None
    try:
        from ..models.config_profile import ConfigProfile as _CP
        from ..services.score_engine import ScoreEngine
        from ..services.seed_service import DEFAULT_SCORE
        # Prefer watchlist owner's config (same path as pipeline_scan)
        _cp_row = (await db.execute(
            select(_CP).where(
                _CP.user_id == wl.user_id,
                _CP.pool_id.is_(None),
                _CP.config_type == "score",
            ).order_by(_CP.updated_at.desc()).limit(1)
        )).scalars().first()
        sc = _cp_row.config_json if _cp_row else None
        # Fall back to profile's scoring config if no global config exists
        if not sc or not (sc.get("scoring_rules") or sc.get("rules")):
            if profile_config_for_score:
                scoring_section = profile_config_for_score.get("scoring", {})
                _rules = scoring_section.get("scoring_rules") or scoring_section.get("rules")
                if _rules:
                    sc = {
                        "scoring_rules": _rules,
                        "weights": scoring_section.get("weights", {}),
                    }
        se = ScoreEngine(sc if sc else DEFAULT_SCORE)
    except Exception as exc:
        logger.warning("pipeline assets: score engine init failed [%s]: %s", type(exc).__name__, exc)

    # ── 4. Build response — apply profile filter at query time ────────────────
    rule_engine = RuleEngine() if filter_conditions else None

    assets = []
    for r in rows:
        sym = r.symbol
        ind_data = ind_map.get(sym, {})

        # Build evaluation dict (meta fields + live indicator fields)
        eval_dict: Dict[str, Any] = {
            "market_cap":         float(r.market_cap)       if r.market_cap       is not None else None,
            "volume_24h":         float(r.volume_24h)       if r.volume_24h       is not None else None,
            "price":              float(r.current_price)    if r.current_price    is not None else None,
            "change_24h":         float(r.price_change_24h) if r.price_change_24h is not None else None,
            "price_change_24h":   float(r.price_change_24h) if r.price_change_24h is not None else None,
            "score":              float(r.alpha_score)      if r.alpha_score      is not None else None,
            **ind_data,
        }

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
            "_meta:score":       eval_dict["score"],
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
            "alpha_score":      eval_dict["score"],
            "entered_at":       r.entered_at.isoformat()  if r.entered_at       else None,
            "level_direction":  r.level_direction,
            "previous_level":   r.previous_level,
            "level_change_at":  r.level_change_at.isoformat() if r.level_change_at else None,
            "indicators":       indicators,
            "score_rules":      score_rules,
        })

    return {
        "watchlist_id":      str(wl_id),
        "watchlist_name":    wl.name,
        "level":             wl.level,
        "asset_count":       len(assets),
        "assets":            assets,
        "profile_indicators": profile_indicators,
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
