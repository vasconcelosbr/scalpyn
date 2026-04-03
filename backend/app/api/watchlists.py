"""Pipeline Watchlists API — 4-level institutional funnel (NEW endpoints).

Prefix: /api/watchlists  (plural — separate from existing /api/watchlist)

Routes:
  GET    /api/watchlists                → list user's pipeline watchlists
  POST   /api/watchlists                → create watchlist
  PUT    /api/watchlists/{id}           → update watchlist
  DELETE /api/watchlists/{id}           → delete watchlist
  GET    /api/watchlists/{id}/assets    → resolved assets with live data + scores
  POST   /api/watchlists/{id}/refresh   → force re-resolve pipeline
  POST   /api/watchlists/{id}/default-setup → create L1/L2/L3 defaults for a pool
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select, text, func
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db, AsyncSessionLocal
from ..api.config import get_current_user_id
from ..models.pipeline_watchlist import PipelineWatchlist, PipelineWatchlistAsset

logger = logging.getLogger(__name__)

GATE_TICKERS_URL = "https://api.gateio.ws/api/v4/spot/tickers"

router = APIRouter(prefix="/api/watchlists", tags=["Pipeline Watchlists"])


async def _seed_market_metadata_bg(symbols: List[str]) -> None:
    """Background task: fetch Gate.io tickers and upsert into market_metadata.

    Runs after the HTTP response is sent — does not block the request.
    Opens its own DB session so it is independent of the request session.
    """
    symbol_set = set(symbols)
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(GATE_TICKERS_URL)
            resp.raise_for_status()
            tickers = resp.json()

        now = datetime.now(timezone.utc)
        rows = []
        for ticker in tickers:
            pair = ticker.get("currency_pair", "")
            if pair not in symbol_set:
                continue
            price = float(ticker.get("last", 0) or 0)
            change = float(ticker.get("change_percentage", 0) or 0)
            volume = float(ticker.get("quote_volume", 0) or 0)
            if price <= 0:
                continue
            rows.append({"symbol": pair, "price": price, "change": change, "volume": volume, "updated": now})

        if not rows:
            return

        async with AsyncSessionLocal() as db:
            for row in rows:
                try:
                    await db.execute(
                        text("""
                            INSERT INTO market_metadata (symbol, price, price_change_24h, volume_24h, last_updated)
                            VALUES (:symbol, :price, :change, :volume, :updated)
                            ON CONFLICT (symbol) DO UPDATE SET
                                price = :price, price_change_24h = :change,
                                volume_24h = :volume, last_updated = :updated
                        """),
                        row,
                    )
                except Exception:
                    pass
            await db.commit()
        logger.info("[Pipeline] Background seed: upserted market data for %d symbols", len(rows))
    except Exception as e:
        logger.warning("[Pipeline] Background market seed failed: %s", e)


# ── Profile indicator helpers ──────────────────────────────────────────────────

# Map: profile config field name → key in indicators_json (or special source)
FIELD_MAP: Dict[str, str] = {
    # Market metadata (sourced from market_metadata table, not indicators_json)
    "volume_24h":          "_meta:volume_24h",
    "market_cap":          "_meta:market_cap",
    "change_24h":          "_meta:price_change_24h",
    "price_change_24h":    "_meta:price_change_24h",
    # Indicator fields
    "atr_pct":             "atr_pct",
    "rsi":                 "rsi",
    "adx":                 "adx",
    "di_plus":             "di_plus",
    "macd_histogram":      "macd_histogram",
    "bb_width":            "bb_width",
    "zscore":              "zscore",
    "ema_full_alignment":  "ema_full_alignment",
    "ema9_gt_ema50":       "ema9_gt_ema50",
    "ema50_gt_ema200":     "ema50_gt_ema200",
    "ema9":                "ema9",
    "ema50":               "ema50",
    "ema200":              "ema200",
    "ema_align_label":     "ema_align_label",
    "volume_spike":        "volume_spike",
    "macd":                "macd",
    "macd_signal":         "macd_signal",
    "stoch_k":             "stoch_k",
    "vwap":                "vwap",
}

# Human-readable labels
FIELD_LABELS: Dict[str, str] = {
    "_meta:volume_24h":    "Volume 24h",
    "_meta:market_cap":    "Market Cap",
    "_meta:price_change_24h": "24h%",
    "atr_pct":             "ATR%",
    "rsi":                 "RSI",
    "adx":                 "ADX",
    "di_plus":             "DI+",
    "macd_histogram":      "MACD Hist",
    "bb_width":            "BB Width",
    "zscore":              "Z-Score",
    "ema_full_alignment":  "EMA Full",
    "ema9_gt_ema50":       "EMA 9>50",
    "ema50_gt_ema200":     "EMA 50>200",
    "ema9":                "EMA 9",
    "ema50":               "EMA 50",
    "ema200":              "EMA 200",
    "ema_align_label":     "EMA Align",
    "volume_spike":        "Vol Spike",
    "macd":                "MACD",
    "macd_signal":         "MACD Sig",
    "stoch_k":             "Stoch K",
    "vwap":                "VWAP",
}


def _extract_profile_indicator_fields(profile_config: Optional[Dict[str, Any]]) -> List[Dict[str, str]]:
    """
    Extract the ordered list of unique indicator fields referenced in a profile's
    filters + signals conditions.
    Returns [{"key": "_meta:volume_24h", "label": "Volume 24h", "field": "volume_24h"}, ...]
    """
    if not profile_config:
        # Default columns when no profile is assigned
        return [
            {"key": "_meta:price_change_24h", "label": "24h%",       "field": "price_change_24h"},
            {"key": "_meta:volume_24h",       "label": "Volume 24h", "field": "volume_24h"},
            {"key": "_meta:market_cap",       "label": "Market Cap", "field": "market_cap"},
        ]

    seen: Dict[str, bool] = {}   # key → already_added (ordered dedup)
    result: List[Dict[str, str]] = []

    def _add(field: str):
        mapped = FIELD_MAP.get(field)
        if mapped and mapped not in seen:
            seen[mapped] = True
            result.append({
                "key":   mapped,
                "label": FIELD_LABELS.get(mapped, field),
                "field": field,
            })

    # Collect from filters
    for cond in profile_config.get("filters", {}).get("conditions", []):
        _add(cond.get("field", ""))

    # Collect from signals
    for cond in profile_config.get("signals", {}).get("conditions", []):
        _add(cond.get("field", ""))

    # Auto-expand EMA columns: if any EMA field is referenced in the profile,
    # inject ema9 / ema50 / ema200 + alignment badge in logical order.
    EMA_KEYS = {"ema_full_alignment", "ema9_gt_ema50", "ema50_gt_ema200", "ema9", "ema50", "ema200"}
    if any(k in seen for k in EMA_KEYS):
        result = [r for r in result if r["key"] not in {
            "ema9", "ema50", "ema200", "ema_align_label",
            "ema_full_alignment", "ema9_gt_ema50", "ema50_gt_ema200",
        }]
        seen = {r["key"]: True for r in result}
        for field in ["ema9", "ema50", "ema200", "ema_align_label"]:
            _add(field)

    return result


async def _fetch_indicators_map(db: AsyncSession, symbols: List[str]) -> Dict[str, Dict[str, Any]]:
    """Fetch latest indicators_json per symbol from the indicators table."""
    if not symbols:
        return {}
    placeholders = ", ".join(f"'{s}'" for s in symbols)
    try:
        rows = (await db.execute(text(f"""
            SELECT DISTINCT ON (symbol) symbol, indicators_json
            FROM indicators
            WHERE symbol IN ({placeholders})
            ORDER BY symbol, time DESC
        """))).fetchall()
        return {r.symbol: (r.indicators_json or {}) for r in rows}
    except Exception as exc:
        logger.warning("[Pipeline] indicators fetch failed: %s", exc)
        return {}


# ── Serializers ────────────────────────────────────────────────────────────────

def _wl_to_dict(wl: PipelineWatchlist) -> Dict[str, Any]:
    return {
        "id":                   str(wl.id),
        "name":                 wl.name,
        "level":                wl.level,
        "source_pool_id":       str(wl.source_pool_id) if wl.source_pool_id else None,
        "source_watchlist_id":  str(wl.source_watchlist_id) if wl.source_watchlist_id else None,
        "profile_id":           str(wl.profile_id) if wl.profile_id else None,
        "auto_refresh":         wl.auto_refresh,
        "filters_json":         wl.filters_json or {},
        "created_at":           wl.created_at.isoformat() if wl.created_at else None,
        "updated_at":           wl.updated_at.isoformat() if wl.updated_at else None,
    }


def _asset_to_dict(a: PipelineWatchlistAsset, indicators: Optional[Dict[str, Any]] = None, meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    ind = indicators or {}
    mt  = meta or {}
    ind_out = {
        "_meta:volume_24h":        float(a.volume_24h)       if a.volume_24h       else mt.get("volume_24h"),
        "_meta:market_cap":        float(a.market_cap)       if a.market_cap       else mt.get("market_cap"),
        "_meta:price_change_24h":  float(a.price_change_24h) if a.price_change_24h else mt.get("price_change_24h"),
        **{k: v for k, v in ind.items() if k in FIELD_MAP.values()},
    }
    try:
        e9   = float(ind_out.get("ema9")   or 0)
        e50  = float(ind_out.get("ema50")  or 0)
        e200 = float(ind_out.get("ema200") or 0)
        if e9 > 0 and e50 > 0 and e200 > 0:
            if e9 > e50 > e200:
                ind_out["ema_align_label"] = "9>50>200"
            elif e9 > e50 and e50 <= e200:
                ind_out["ema_align_label"] = "9>50"
            elif e9 < e50 < e200:
                ind_out["ema_align_label"] = "9<50<200"
            else:
                ind_out["ema_align_label"] = "mix"
    except (TypeError, ValueError):
        pass
    return {
        "id":               str(a.id),
        "watchlist_id":     str(a.watchlist_id),
        "symbol":           a.symbol,
        "current_price":    float(a.current_price) if a.current_price else None,
        "price_change_24h": float(a.price_change_24h) if a.price_change_24h else None,
        "volume_24h":       float(a.volume_24h) if a.volume_24h else None,
        "market_cap":       float(a.market_cap) if a.market_cap else None,
        "alpha_score":      float(a.alpha_score) if a.alpha_score else None,
        "entered_at":       a.entered_at.isoformat() if a.entered_at else None,
        "previous_level":   a.previous_level,
        "level_change_at":  a.level_change_at.isoformat() if a.level_change_at else None,
        "level_direction":  a.level_direction,
        "indicators": ind_out,
    }


# ── CRUD ───────────────────────────────────────────────────────────────────────

@router.get("/")
async def list_watchlists(
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """List all pipeline watchlists for the authenticated user."""
    result = await db.execute(
        select(PipelineWatchlist)
        .where(PipelineWatchlist.user_id == user_id)
        .order_by(PipelineWatchlist.created_at)
    )
    wls = result.scalars().all()

    # Fetch asset counts for all watchlists in one query
    if wls:
        wl_ids = [w.id for w in wls]
        count_result = await db.execute(
            select(
                PipelineWatchlistAsset.watchlist_id,
                func.count(PipelineWatchlistAsset.id).label("cnt"),
            )
            .where(
                PipelineWatchlistAsset.watchlist_id.in_(wl_ids),
                (PipelineWatchlistAsset.level_direction.is_(None)) |
                (PipelineWatchlistAsset.level_direction == "up"),
            )
            .group_by(PipelineWatchlistAsset.watchlist_id)
        )
        counts: Dict[UUID, int] = {row.watchlist_id: row.cnt for row in count_result.fetchall()}
    else:
        counts = {}

    def _with_count(w: PipelineWatchlist) -> Dict[str, Any]:
        d = _wl_to_dict(w)
        d["asset_count"] = counts.get(w.id, 0)
        return d

    return {"watchlists": [_with_count(w) for w in wls], "total": len(wls)}


@router.post("/")
async def create_watchlist(
    payload: Dict[str, Any],
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Create a new pipeline watchlist."""
    name = payload.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    def _to_uuid(val: Any) -> Optional[UUID]:
        if not val:
            return None
        try:
            return UUID(str(val))
        except ValueError:
            return None

    level = payload.get("level", "custom")
    # Apply sensible default filters when none provided for L2/L3
    filters = payload.get("filters_json") or {}
    if not filters:
        if level == "L2":
            filters = {"min_score": 55}
        elif level == "L3":
            filters = {"min_score": 60, "require_signal": True}

    wl = PipelineWatchlist(
        user_id=user_id,
        name=name,
        level=level,
        source_pool_id=_to_uuid(payload.get("source_pool_id")),
        source_watchlist_id=_to_uuid(payload.get("source_watchlist_id")),
        profile_id=_to_uuid(payload.get("profile_id")),
        auto_refresh=payload.get("auto_refresh", True),
        filters_json=filters,
    )
    db.add(wl)
    await db.commit()
    await db.refresh(wl)
    return _wl_to_dict(wl)


@router.put("/{watchlist_id}")
async def update_watchlist(
    watchlist_id: UUID,
    payload: Dict[str, Any],
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Update an existing pipeline watchlist."""
    result = await db.execute(
        select(PipelineWatchlist).where(
            PipelineWatchlist.id == watchlist_id,
            PipelineWatchlist.user_id == user_id,
        )
    )
    wl = result.scalars().first()
    if not wl:
        raise HTTPException(status_code=404, detail="Watchlist not found")

    def _to_uuid(val: Any) -> Optional[UUID]:
        if not val:
            return None
        try:
            return UUID(str(val))
        except ValueError:
            return None

    if "name" in payload:
        wl.name = payload["name"].strip() or wl.name
    if "level" in payload:
        wl.level = payload["level"]
    if "source_pool_id" in payload:
        wl.source_pool_id = _to_uuid(payload["source_pool_id"])
    if "source_watchlist_id" in payload:
        wl.source_watchlist_id = _to_uuid(payload["source_watchlist_id"])
    if "profile_id" in payload:
        wl.profile_id = _to_uuid(payload["profile_id"])
    if "auto_refresh" in payload:
        wl.auto_refresh = bool(payload["auto_refresh"])
    if "filters_json" in payload:
        wl.filters_json = payload["filters_json"]
    wl.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(wl)
    return _wl_to_dict(wl)


@router.delete("/{watchlist_id}")
async def delete_watchlist(
    watchlist_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Delete a pipeline watchlist and all its assets."""
    result = await db.execute(
        select(PipelineWatchlist).where(
            PipelineWatchlist.id == watchlist_id,
            PipelineWatchlist.user_id == user_id,
        )
    )
    wl = result.scalars().first()
    if not wl:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    await db.delete(wl)
    await db.commit()
    return {"deleted": str(watchlist_id)}


# ── Pipeline resolution ────────────────────────────────────────────────────────

async def _get_base_symbols(
    wl: PipelineWatchlist,
    user_id: UUID,
    db: AsyncSession,
    depth: int = 0,
) -> List[str]:
    """Recursively resolve base symbols for a watchlist from its source."""
    if depth > 5:
        logger.warning("Pipeline resolution depth exceeded for wl %s", wl.id)
        return []

    if wl.source_pool_id:
        # Source is a Pool → get pool's coins
        result = await db.execute(
            text("""
                SELECT symbol FROM pool_coins
                WHERE pool_id = :pool_id AND is_active = TRUE
            """),
            {"pool_id": str(wl.source_pool_id)},
        )
        return [row.symbol for row in result.fetchall()]

    if wl.source_watchlist_id:
        # Source is another PipelineWatchlist → get its saved assets
        result = await db.execute(
            select(PipelineWatchlist).where(
                PipelineWatchlist.id == wl.source_watchlist_id,
                PipelineWatchlist.user_id == user_id,
            )
        )
        parent = result.scalars().first()
        if parent:
            assets = await db.execute(
                select(PipelineWatchlistAsset).where(
                    PipelineWatchlistAsset.watchlist_id == parent.id,
                    (PipelineWatchlistAsset.level_direction.is_(None)) |
                    (PipelineWatchlistAsset.level_direction == "up"),
                )
            )
            saved = assets.scalars().all()
            if saved:
                return [a.symbol for a in saved]
            # Parent has no saved assets — resolve it first
            return await _get_base_symbols(parent, user_id, db, depth + 1)

    return []


async def _resolve_and_persist(
    wl: PipelineWatchlist,
    user_id: UUID,
    db: AsyncSession,
) -> List[Dict[str, Any]]:
    """
    Resolve pipeline, apply filters, upsert pipeline_watchlist_assets,
    detect level transitions, and return enriched asset list.
    """
    base_symbols = await _get_base_symbols(wl, user_id, db)
    if not base_symbols:
        return []

    filters = wl.filters_json or {}
    min_score: float = float(filters.get("min_score", 0))
    require_signal: bool = bool(filters.get("require_signal", False))
    require_no_blocks: bool = bool(filters.get("require_no_blocks", False))

    # Fetch market metadata + latest scores for these symbols
    placeholders = ", ".join(f"'{s}'" for s in base_symbols)

    try:
        meta_rows = await db.execute(
            text(f"""
                SELECT symbol, price, price_change_24h, volume_24h, market_cap
                FROM market_metadata
                WHERE symbol IN ({placeholders})
            """)
        )
        meta_map = {
            r.symbol: {
                "price":            float(r.price) if r.price else 0.0,
                "price_change_24h": float(r.price_change_24h) if r.price_change_24h else 0.0,
                "volume_24h":       float(r.volume_24h) if r.volume_24h else 0.0,
                "market_cap":       float(r.market_cap) if r.market_cap else 0.0,
            }
            for r in meta_rows.fetchall()
        }
    except Exception:
        meta_map = {}

    try:
        score_rows = await db.execute(
            text(f"""
                SELECT DISTINCT ON (symbol) symbol, score, signal_score
                FROM alpha_scores
                WHERE symbol IN ({placeholders})
                ORDER BY symbol, time DESC
            """)
        )
        score_map = {
            r.symbol: {
                "score":        float(r.score) if r.score else 0.0,
                "signal_score": float(r.signal_score) if r.signal_score else 0.0,
            }
            for r in score_rows.fetchall()
        }
    except Exception:
        score_map = {}

    # When no real alpha scores are available, derive scores from market_metadata
    # using price_change_24h as momentum proxy: score = clamp(50 + pct*2, 0, 100).
    # Real alpha_scores take priority when present.
    use_derived_scores = len(score_map) == 0
    scoring_data_available = bool(score_map) or bool(meta_map)

    if use_derived_scores and meta_map:
        for symbol, meta in meta_map.items():
            pct = meta.get("price_change_24h", 0.0)
            derived = max(0.0, min(100.0, 50.0 + pct * 2.0))
            signal = 100.0 if derived >= 60.0 else 0.0
            score_map[symbol] = {"score": derived, "signal_score": signal}

    now = datetime.now(timezone.utc)
    assets_out: List[Dict[str, Any]] = []

    for symbol in base_symbols:
        scores = score_map.get(symbol, {})
        alpha = scores.get("score", 0.0)
        signal = scores.get("signal_score", 0.0)

        # Apply score filter only when we actually HAVE scoring data.
        # If market_metadata + alpha_scores are both empty (first run, pipeline
        # not yet populated), we let all symbols through for L1 so the user can
        # see their pool assets immediately even before the Celery pipeline runs.
        if scoring_data_available:
            if min_score and alpha < min_score:
                continue
            if require_signal and signal < 50:
                continue

        meta = meta_map.get(symbol, {})
        assets_out.append({
            "symbol":           symbol,
            "current_price":    meta.get("price"),
            "price_change_24h": meta.get("price_change_24h"),
            "volume_24h":       meta.get("volume_24h"),
            "market_cap":       meta.get("market_cap"),
            "alpha_score":      round(alpha, 1) if scoring_data_available else None,
        })

    # Detect level transitions & upsert
    existing_result = await db.execute(
        select(PipelineWatchlistAsset).where(
            PipelineWatchlistAsset.watchlist_id == wl.id
        )
    )
    existing_map = {a.symbol: a for a in existing_result.scalars().all()}
    new_symbols = {a["symbol"] for a in assets_out}
    prev_symbols = set(existing_map.keys())

    for asset_data in assets_out:
        sym = asset_data["symbol"]
        if sym in existing_map:
            row = existing_map[sym]
            row.current_price    = asset_data["current_price"]
            row.price_change_24h = asset_data["price_change_24h"]
            row.volume_24h       = asset_data["volume_24h"]
            row.market_cap       = asset_data["market_cap"]
            row.alpha_score      = asset_data["alpha_score"]
        else:
            # New asset entered this watchlist level
            row = PipelineWatchlistAsset(
                watchlist_id=wl.id,
                symbol=sym,
                current_price=asset_data["current_price"],
                price_change_24h=asset_data["price_change_24h"],
                volume_24h=asset_data["volume_24h"],
                market_cap=asset_data["market_cap"],
                alpha_score=asset_data["alpha_score"],
                entered_at=now,
                level_direction="up",
                level_change_at=now,
            )
            db.add(row)
            asset_data["level_direction"] = "up"

    # Assets that left this level
    for sym in prev_symbols - new_symbols:
        row = existing_map[sym]
        row.level_direction = "down"
        row.level_change_at = now

    await db.commit()
    return assets_out


# ── Assets endpoint ────────────────────────────────────────────────────────────

@router.get("/{watchlist_id}/assets")
async def get_watchlist_assets(
    watchlist_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Return resolved and filtered assets for this watchlist level.

    Auto-resolves the pipeline on first access when auto_refresh=True and
    no assets have been persisted yet — this propagates L1→L2→L3 automatically.
    """
    result = await db.execute(
        select(PipelineWatchlist).where(
            PipelineWatchlist.id == watchlist_id,
            PipelineWatchlist.user_id == user_id,
        )
    )
    wl = result.scalars().first()
    if not wl:
        raise HTTPException(status_code=404, detail="Watchlist not found")

    # Active assets: level_direction IS NULL (set by Celery scan) or 'up' (manual refresh)
    # 'down' means the asset no longer passes the pipeline filter
    assets_result = await db.execute(
        select(PipelineWatchlistAsset)
        .where(
            PipelineWatchlistAsset.watchlist_id == watchlist_id,
            (PipelineWatchlistAsset.level_direction.is_(None)) |
            (PipelineWatchlistAsset.level_direction == "up"),
        )
        .order_by(PipelineWatchlistAsset.alpha_score.desc().nullslast())
    )

    assets = assets_result.scalars().all()

    # Auto-resolve on first open when there are no saved assets
    if not assets and wl.auto_refresh:
        try:
            await _resolve_and_persist(wl, user_id, db)
            assets_result2 = await db.execute(
                select(PipelineWatchlistAsset)
                .where(
                    PipelineWatchlistAsset.watchlist_id == watchlist_id,
                    (PipelineWatchlistAsset.level_direction.is_(None)) |
                    (PipelineWatchlistAsset.level_direction == "up"),
                )
                .order_by(PipelineWatchlistAsset.alpha_score.desc().nullslast())
            )
            assets = assets_result2.scalars().all()
        except Exception as e:
            logger.warning("[Pipeline] Auto-resolve failed for %s: %s", watchlist_id, e)

    # ── Load profile to derive dynamic indicator column schema ────────────────
    profile_config: Optional[Dict[str, Any]] = None
    if wl.profile_id:
        from ..models.profile import Profile
        prof_result = await db.execute(
            select(Profile).where(Profile.id == wl.profile_id)
        )
        prof = prof_result.scalars().first()
        if prof:
            # .config always holds filters/signals conditions; preset_ia_config is IA metadata only
            # (regime, macro_risk, analysis_summary) — never use it for indicator extraction
            profile_config = prof.config

    profile_indicators = _extract_profile_indicator_fields(profile_config)

    # ── Fetch live indicator values for all asset symbols ─────────────────────
    symbols = [a.symbol for a in assets]
    ind_map = await _fetch_indicators_map(db, symbols) if symbols else {}

    # Fresh meta (some values may be missing from pipeline_watchlist_assets)
    meta_map: Dict[str, Dict[str, Any]] = {}
    if symbols:
        try:
            placeholders = ", ".join(f"'{s}'" for s in symbols)
            meta_rows = (await db.execute(text(f"""
                SELECT symbol, price_change_24h, volume_24h, market_cap
                FROM market_metadata WHERE symbol IN ({placeholders})
            """))).fetchall()
            meta_map = {
                r.symbol: {
                    "price_change_24h": float(r.price_change_24h) if r.price_change_24h else None,
                    "volume_24h":       float(r.volume_24h)       if r.volume_24h       else None,
                    "market_cap":       float(r.market_cap)       if r.market_cap       else None,
                }
                for r in meta_rows
            }
        except Exception:
            pass

    enriched = [
        _asset_to_dict(a, indicators=ind_map.get(a.symbol), meta=meta_map.get(a.symbol))
        for a in assets
    ]

    return {
        "assets":             enriched,
        "total":              len(enriched),
        "profile_indicators": profile_indicators,  # [{key, label, field}, ...]
    }


async def _cascade_refresh(wl_id: UUID, user_id: UUID, db: AsyncSession, depth: int = 0) -> None:
    """Cascade refresh to all watchlists that use this one as their source."""
    if depth > 3:
        return
    children_result = await db.execute(
        select(PipelineWatchlist).where(
            PipelineWatchlist.source_watchlist_id == wl_id,
            PipelineWatchlist.user_id == user_id,
            PipelineWatchlist.auto_refresh == True,
        )
    )
    children = children_result.scalars().all()
    for child in children:
        try:
            await _resolve_and_persist(child, user_id, db)
            await _cascade_refresh(child.id, user_id, db, depth + 1)
        except Exception as e:
            logger.warning("[Pipeline] Cascade refresh failed for child %s: %s", child.id, e)


@router.post("/{watchlist_id}/refresh")
async def refresh_watchlist(
    watchlist_id: UUID,
    background_tasks: BackgroundTasks,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Force re-resolve the pipeline and cascade to all downstream watchlists."""
    result = await db.execute(
        select(PipelineWatchlist).where(
            PipelineWatchlist.id == watchlist_id,
            PipelineWatchlist.user_id == user_id,
        )
    )
    wl = result.scalars().first()
    if not wl:
        raise HTTPException(status_code=404, detail="Watchlist not found")

    # Get base symbols first so we can seed market data if needed
    base_symbols = await _get_base_symbols(wl, user_id, db)

    # Schedule background market-data seed when the source is a pool.
    # This runs after the response is sent, so it never blocks the request.
    # On the next refresh the fresh scores will be used for filtering.
    if wl.source_pool_id and base_symbols:
        background_tasks.add_task(_seed_market_metadata_bg, base_symbols)

    assets = await _resolve_and_persist(wl, user_id, db)

    # Cascade refresh to downstream watchlists (L1 → L2 → L3)
    await _cascade_refresh(watchlist_id, user_id, db)

    return {"refreshed": True, "asset_count": len(assets)}


# ── Default setup helper ───────────────────────────────────────────────────────

@router.post("/default-setup")
async def create_default_pipeline(
    payload: Dict[str, Any],
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """
    Auto-create L1 / L2 / L3 watchlists linked to a given pool_id.
    Called when user creates their first pool and clicks 'Discover Assets'.
    Idempotent: skips creation if same-named watchlist already exists for this pool.
    """
    pool_id_str = payload.get("pool_id")
    if not pool_id_str:
        raise HTTPException(status_code=400, detail="pool_id is required")
    try:
        pool_uuid = UUID(pool_id_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid pool_id")

    created: List[Dict[str, Any]] = []

    async def _get_or_create(name: str, level: str, **kwargs) -> PipelineWatchlist:
        existing = await db.execute(
            select(PipelineWatchlist).where(
                PipelineWatchlist.user_id == user_id,
                PipelineWatchlist.name == name,
                PipelineWatchlist.level == level,
            )
        )
        wl = existing.scalars().first()
        if wl:
            return wl
        wl = PipelineWatchlist(user_id=user_id, name=name, level=level, **kwargs)
        db.add(wl)
        await db.flush()  # get id before commit
        created.append(_wl_to_dict(wl))
        return wl

    l1 = await _get_or_create(
        "L1 Assets", "L1",
        source_pool_id=pool_uuid,
        filters_json={},
    )
    l2 = await _get_or_create(
        "L2 Ranking", "L2",
        source_watchlist_id=l1.id,
        filters_json={"min_score": 0},
    )
    await _get_or_create(
        "L3 Signals", "L3",
        source_watchlist_id=l2.id,
        filters_json={"min_score": 75, "require_signal": True, "require_no_blocks": True},
    )

    await db.commit()
    return {"created": created, "total_created": len(created)}
