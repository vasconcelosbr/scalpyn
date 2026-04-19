"""Pipeline Watchlists API — 4-level institutional funnel (NEW endpoints).

Prefix: /api/watchlists  (plural — separate from existing /api/watchlist)

Routes:
  GET    /api/watchlists                → list user's pipeline watchlists
  POST   /api/watchlists                → create watchlist
  PUT    /api/watchlists/{id}           → update watchlist
  DELETE /api/watchlists/{id}           → delete watchlist
  GET    /api/watchlists/{id}/assets    → resolved assets with livhe data + scores
  POST   /api/watchlists/{id}/refresh   → force re-resolve pipeline
  POST   /api/watchlists/{id}/default-setup → create L1/L2/L3 defaults for a pool
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

import httpx
from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException
from sqlalchemy import select, text, func
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db, AsyncSessionLocal
from ..api.config import get_current_user_id
from ..models.pipeline_watchlist import PipelineWatchlist, PipelineWatchlistAsset
from ..services.market_data_service import _is_etf_pair

logger = logging.getLogger(__name__)

GATE_TICKERS_URL = "https://api.gateio.ws/api/v4/spot/tickers"


def _passes_profile_filters(asset: Dict[str, Any], conditions: list, logic: str = "AND") -> bool:
    """Evaluate profile filter conditions against a pipeline asset.

    Meta fields (market_cap, volume_24h, price, change %, spread, depth) are
    STRICT: if the value is None the condition FAILS.  This prevents assets with
    unknown market-cap from slipping through a 'market_cap >= 500M' gate.

    Indicator fields (rsi, adx, atr_pct, …) are LENIENT: if the indicator is not
    present in the asset dict the condition is SKIPPED (not counted as a fail).
    This mirrors ProfileEngine._apply_filters behaviour so manual-refresh and
    Celery pipeline produce consistent results.
    """
    _STRICT_META = frozenset({
        "volume_24h", "market_cap", "price", "current_price",
        "change_24h", "price_change_24h", "change_24h_pct",
        "spread_pct", "orderbook_depth_usdt",
    })

    if not conditions:
        return True
    results = []
    for cond in conditions:
        field = cond.get("field") or cond.get("indicator", "")
        operator = cond.get("operator", ">")
        threshold = cond.get("value")
        if not field:
            continue

        actual = asset.get(field)

        # Alias: profile may store 'change_24h' but asset uses 'price_change_24h'
        if actual is None and field == "change_24h":
            actual = asset.get("price_change_24h")
        if actual is None and field == "price_change_24h":
            actual = asset.get("change_24h")

        if actual is None:
            # Strict meta fields (market_cap, volume_24h, etc.) FAIL when None to prevent
            # assets with unknown values from bypassing filters (e.g., "market_cap >= 5M").
            # Indicator fields (RSI, ADX, etc.) are SKIPPED when None since they may not
            # be computed yet and should not block the asset.
            if field in _STRICT_META:
                results.append(False)
            continue

        # Between operator
        if operator == "between":
            try:
                min_v = float(cond.get("min", float("-inf")))
                max_v = float(cond.get("max", float("inf")))
                results.append(min_v <= float(actual) <= max_v)
            except (TypeError, ValueError):
                results.append(False)
            continue

        if threshold is None:
            continue
        try:
            actual_f = float(actual)
            threshold_f = float(threshold)
        except (TypeError, ValueError):
            results.append(False)
            continue
        if operator in (">", "gt"):
            results.append(actual_f > threshold_f)
        elif operator in (">=", "gte"):
            results.append(actual_f >= threshold_f)
        elif operator in ("<", "lt"):
            results.append(actual_f < threshold_f)
        elif operator in ("<=", "lte"):
            results.append(actual_f <= threshold_f)
        elif operator in ("==", "=", "eq"):
            results.append(actual_f == threshold_f)
        elif operator in ("!=", "ne"):
            results.append(actual_f != threshold_f)
        else:
            results.append(True)

    if not results:
        return True
    return all(results) if logic.upper() == "AND" else any(results)

router = APIRouter(prefix="/api/watchlists", tags=["Pipeline Watchlists"])


async def _seed_market_metadata_bg(symbols: List[str]) -> None:
    """Background task: fetch Gate.io tickers and upsert into market_metadata.

    Runs after the HTTP response is sent — does not block the request.
    Opens its own DB session so it is independent of the request session.
    Symbols are normalized to BTC_USDT format before comparison with tickers.
    """
    # Normalize symbols to BTC_USDT format (Gate.io uses underscores)
    def _norm(s: str) -> str:
        s = s.upper().strip()
        if "_" not in s and s.endswith("USDT"):
            return s[:-4] + "_USDT"
        return s
    symbol_set = {_norm(s) for s in symbols}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(GATE_TICKERS_URL)
            resp.raise_for_status()
            tickers = resp.json()

        now = datetime.now(timezone.utc)
        rows = []
        for ticker in tickers:
            pair = ticker.get("currency_pair", "")
            if _is_etf_pair(pair) or ticker.get("etf_net_value") is not None:
                continue
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
    "di_minus":            "di_minus",
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
    "taker_ratio":         "taker_ratio",
    "ema9_distance_pct":   "ema9_distance_pct",
    "spread_pct":          "spread_pct",
    "orderbook_depth_usdt": "orderbook_depth_usdt",
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
    "di_minus":            "DI-",
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
    "taker_ratio":         "Taker Ratio",
    "ema9_distance_pct":   "EMA 9%",
    "spread_pct":          "Spread%",
    "orderbook_depth_usdt": "Depth",
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
    try:
        rows = (await db.execute(
            text("""
                SELECT DISTINCT ON (symbol) symbol, indicators_json
                FROM indicators
                WHERE symbol = ANY(:symbols)
                ORDER BY symbol, time DESC
            """),
            {"symbols": list(symbols)},
        )).fetchall()
        return {r.symbol: (r.indicators_json or {}) for r in rows}
    except Exception as exc:
        logger.warning("[Pipeline] indicators fetch failed: %s", exc)
        return {}


async def _compute_indicators_on_demand(
    db: AsyncSession,
    symbols: List[str],
) -> Dict[str, Dict[str, Any]]:
    """
    Fetch OHLCV from Gate.io and compute technical indicators on-demand
    for symbols that have no stored indicators in the DB.
    Results are cached in the indicators table for subsequent requests.
    """
    from ..services.market_data_service import market_data_service
    from ..services.feature_engine import FeatureEngine
    from ..services.seed_service import DEFAULT_INDICATORS
    import asyncio
    import json

    if not symbols:
        return {}

    engine = FeatureEngine(DEFAULT_INDICATORS)
    result: Dict[str, Dict[str, Any]] = {}

    # Semaphore to avoid overwhelming Gate.io public API
    sem = asyncio.Semaphore(8)

    async def _fetch_and_compute(symbol: str):
        async with sem:
            try:
                df = await market_data_service.fetch_ohlcv(symbol, "1h", limit=200)
                if df is None or len(df) < 14:
                    return symbol, {}
                indicators = engine.calculate(df)
                return symbol, indicators
            except Exception as exc:
                logger.debug("[Pipeline] On-demand compute failed for %s: %s", symbol, exc)
                return symbol, {}

    # Cap at 40 to keep response time reasonable (~2-3s max with parallel fetches)
    compute_syms = symbols[:40]
    tasks = [_fetch_and_compute(s) for s in compute_syms]
    computed = await asyncio.gather(*tasks)

    now = datetime.now(timezone.utc)
    cached_count = 0
    for symbol, indicators in computed:
        if not indicators:
            continue
        result[symbol] = indicators
        try:
            await db.execute(
                text("""
                    INSERT INTO indicators (time, symbol, timeframe, indicators_json)
                    VALUES (:time, :symbol, :timeframe, :indicators)
                """),
                {
                    "time":       now,
                    "symbol":     symbol,
                    "timeframe":  "1h",
                    "indicators": json.dumps(indicators),
                },
            )
            cached_count += 1
        except Exception:
            pass

    if cached_count:
        try:
            await db.commit()
            logger.info(
                "[Pipeline] On-demand indicators computed and cached for %d/%d symbols",
                cached_count, len(compute_syms),
            )
        except Exception as exc:
            logger.warning("[Pipeline] Failed to cache on-demand indicators: %s", exc)
            await db.rollback()

    return result


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
        "last_scanned_at":      wl.last_scanned_at.isoformat() if getattr(wl, "last_scanned_at", None) else None,
        "created_at":           wl.created_at.isoformat() if wl.created_at else None,
        "updated_at":           wl.updated_at.isoformat() if wl.updated_at else None,
    }


def _asset_to_dict(a: PipelineWatchlistAsset, indicators: Optional[Dict[str, Any]] = None, meta: Optional[Dict[str, Any]] = None, override_score: Optional[float] = None) -> Dict[str, Any]:
    ind = indicators or {}
    mt  = meta or {}
    ind_out = {
        "_meta:volume_24h":        float(a.volume_24h)       if a.volume_24h       else mt.get("volume_24h"),
        "_meta:market_cap":        float(a.market_cap)       if a.market_cap       else mt.get("market_cap"),
        "_meta:price_change_24h":  float(a.price_change_24h) if a.price_change_24h else mt.get("price_change_24h"),
        **{k: v for k, v in ind.items() if k in FIELD_MAP.values()},
    }
    # Inject spread_pct and orderbook_depth_usdt from market_metadata if not already
    # present from indicators (these come from the market_metadata table, not feature engine)
    if "spread_pct" not in ind_out or ind_out.get("spread_pct") is None:
        ind_out["spread_pct"] = mt.get("spread_pct")
    if "orderbook_depth_usdt" not in ind_out or ind_out.get("orderbook_depth_usdt") is None:
        ind_out["orderbook_depth_usdt"] = mt.get("orderbook_depth_usdt")

    # Derive di_trend (DI+ > DI-) for the frontend, matching pipeline_scan logic
    if "di_trend" not in ind_out or ind_out.get("di_trend") is None:
        di_p = ind_out.get("di_plus")
        di_m = ind_out.get("di_minus")
        if di_p is not None and di_m is not None:
            try:
                ind_out["di_trend"] = float(di_p) > float(di_m)
            except (TypeError, ValueError):
                pass

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

    # ── Anti-bad-entry blocking rules (shared utility) ───────────────────────────
    from ..utils.blocking_rules import check_anti_bad_entry
    _blocked, block_reasons = check_anti_bad_entry({"indicators": ind_out})

    return {
        "id":               str(a.id),
        "watchlist_id":     str(a.watchlist_id),
        "symbol":           a.symbol,
        "current_price":    float(a.current_price) if a.current_price else None,
        "price_change_24h": float(a.price_change_24h) if a.price_change_24h else None,
        "volume_24h":       float(a.volume_24h) if a.volume_24h else None,
        "market_cap":       float(a.market_cap) if a.market_cap else None,
        "alpha_score":      override_score if override_score is not None else (float(a.alpha_score) if a.alpha_score is not None else None),
        "entered_at":       a.entered_at.isoformat() if a.entered_at else None,
        "refreshed_at":     a.refreshed_at.isoformat() if getattr(a, "refreshed_at", None) else None,
        "previous_level":   a.previous_level,
        "level_change_at":  a.level_change_at.isoformat() if a.level_change_at else None,
        "level_direction":  a.level_direction,
        "blocked":          _blocked,
        "block_reasons":    block_reasons,
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
    payload: Dict[str, Any] = Body(...),
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
    # filters_json is kept for backward compatibility but IGNORED at runtime.
    # All filtering is driven exclusively by the associated profile.
    filters = payload.get("filters_json") or {}

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
    d = _wl_to_dict(wl)
    d["asset_count"] = 0
    return d


@router.put("/{watchlist_id}")
async def update_watchlist(
    watchlist_id: UUID,
    payload: Dict[str, Any] = Body(...),
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

    logger.info(
        "[Watchlist] update %s: source_pool_id=%s, source_watchlist_id=%s, profile_id=%s",
        watchlist_id,
        wl.source_pool_id,
        wl.source_watchlist_id,
        wl.profile_id,
    )

    await db.commit()
    await db.refresh(wl)

    # Include asset_count in response so the frontend list stays consistent
    cnt_result = await db.execute(
        select(func.count(PipelineWatchlistAsset.id)).where(
            PipelineWatchlistAsset.watchlist_id == wl.id,
            (PipelineWatchlistAsset.level_direction.is_(None))
            | (PipelineWatchlistAsset.level_direction == "up"),
        )
    )
    asset_count = cnt_result.scalar() or 0

    d = _wl_to_dict(wl)
    d["asset_count"] = asset_count
    return d


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
    """Recursively resolve base symbols for a watchlist from its source.

    Cascade logic:
    - Pool (source_pool_id): always returns raw pool_coins — filters are applied later
    - Levels (source_watchlist_id): reads ACTIVE assets from the immediate parent.
      If the parent has ACTIVE assets → use them (pipeline is up to date).
      If the parent has NO active assets but WAS populated before → return [] (don't
        bypass the pipeline; the parent is temporarily empty after a filter tightening).
      If the parent was NEVER populated → cascade up (initial bootstrap scenario).
    """
    if depth > 5:
        logger.warning("Pipeline resolution depth exceeded for wl %s", wl.id)
        return []

    def _normalize_sym(s: str) -> str:
        """Normalize symbol to BTC_USDT format (Gate.io format with underscore).
        Ensures market_metadata lookups succeed since market_metadata uses underscores.
        e.g.: BTCUSDT → BTC_USDT, BTC_USDT → BTC_USDT (unchanged)
        """
        s = s.upper().strip()
        if "_" not in s and s.endswith("USDT"):
            return s[:-4] + "_USDT"
        return s

    if wl.source_pool_id:
        # Terminal source: return ALL pool coins. Filtering happens in _resolve_and_persist.
        result = await db.execute(
            text("""
                SELECT symbol FROM pool_coins
                WHERE pool_id = :pool_id AND is_active = TRUE
            """),
            {"pool_id": str(wl.source_pool_id)},
        )
        raw_symbols = [row.symbol for row in result.fetchall()]
        # Normalize symbols to BTC_USDT format so market_metadata lookups work
        normalized = list(dict.fromkeys(_normalize_sym(s) for s in raw_symbols))
        logger.debug(
            "[GetBaseSymbols] Pool %s: %d raw coins → %d normalized symbols",
            wl.source_pool_id, len(raw_symbols), len(normalized),
        )
        return normalized

    if wl.source_watchlist_id:
        result = await db.execute(
            select(PipelineWatchlist).where(
                PipelineWatchlist.id == wl.source_watchlist_id,
                PipelineWatchlist.user_id == user_id,
            )
        )
        parent = result.scalars().first()
        if parent:
            # Active (in or neutral direction) assets from the parent
            active_assets = (await db.execute(
                select(PipelineWatchlistAsset).where(
                    PipelineWatchlistAsset.watchlist_id == parent.id,
                    (PipelineWatchlistAsset.level_direction.is_(None)) |
                    (PipelineWatchlistAsset.level_direction == "up"),
                )
            )).scalars().all()

            if active_assets:
                return [_normalize_sym(a.symbol) for a in active_assets]

            # No active assets right now. Check if parent was EVER populated.
            # If yes → parent filtered everything out legitimately → return [].
            # If no  → parent never ran → cascade up to bootstrap.
            ever_populated = (await db.execute(
                text("SELECT 1 FROM pipeline_watchlist_assets WHERE watchlist_id = :wid LIMIT 1"),
                {"wid": str(parent.id)},
            )).fetchone()

            if ever_populated:
                # Parent was populated before but all assets exited — respect the filter
                logger.info(
                    "[GetBaseSymbols] Parent %s was populated before but has 0 active assets. "
                    "Returning [] to preserve cascade integrity.",
                    parent.name,
                )
                return []

            # Parent never populated → recurse (first-run bootstrap)
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
        # No upstream symbols — mark all existing active assets as 'down'
        # so the watchlist properly reflects the empty upstream state.
        now = datetime.now(timezone.utc)
        await db.execute(
            text("""
                UPDATE pipeline_watchlist_assets
                SET level_direction = 'down',
                    level_change_at = :now
                WHERE watchlist_id = :wid
                  AND (level_direction IS NULL OR level_direction NOT IN ('down'))
            """),
            {"wid": str(wl.id), "now": now},
        )
        await db.commit()
        return []

    # NOTE: filters_json on the watchlist is IGNORED at runtime.
    # All filtering criteria (min_score, require_signal, market_cap, volume, etc.)
    # come exclusively from the associated Profile (profile.config.filters.conditions).
    # The watchlist is purely an organisational grouping (L1/L2/L3) + profile reference.

    # Fetch market metadata for these symbols
    try:
        meta_rows = await db.execute(
            text("""
                SELECT symbol, price, price_change_24h, volume_24h, market_cap
                FROM market_metadata
                WHERE symbol = ANY(:symbols)
            """),
            {"symbols": list(base_symbols)},
        )
        meta_map = {
            r.symbol: {
                "price":            float(r.price) if r.price else 0.0,
                "price_change_24h": float(r.price_change_24h) if r.price_change_24h else 0.0,
                "volume_24h":       float(r.volume_24h) if r.volume_24h is not None else None,
                "market_cap":       float(r.market_cap) if r.market_cap is not None else None,
            }
            for r in meta_rows.fetchall()
        }
    except Exception:
        meta_map = {}

    # Load indicators for scoring + signal evaluation
    ind_map: Dict[str, Dict] = {}
    try:
        ind_rows = (await db.execute(
            text("""
                SELECT DISTINCT ON (symbol) symbol, indicators_json
                FROM indicators WHERE symbol = ANY(:symbols)
                ORDER BY symbol, time DESC
            """),
            {"symbols": list(base_symbols)},
        )).fetchall()
        ind_map = {r.symbol: (r.indicators_json or {}) for r in ind_rows}
    except Exception:
        pass

    # Load pre-computed alpha scores (used as fallback when no rules configured)
    try:
        score_rows = (await db.execute(
            text("""
                SELECT DISTINCT ON (symbol) symbol, score
                FROM alpha_scores
                WHERE symbol = ANY(:symbols)
                ORDER BY symbol, time DESC
            """),
            {"symbols": list(base_symbols)},
        )).fetchall()
        precomp_score_map = {r.symbol: float(r.score) if r.score else 0.0 for r in score_rows}
    except Exception:
        precomp_score_map = {}

    # Compute live alpha scores using the user's global score config
    from ..services.score_engine import ScoreEngine as _SE, merge_score_config
    from ..services.seed_service import DEFAULT_SCORE
    from ..services.config_service import config_service as _cs

    global_score_config = DEFAULT_SCORE
    try:
        cfg = await _cs.get_config(db, "score", user_id)
        if cfg and cfg.get("scoring_rules"):
            global_score_config = cfg
    except Exception:
        pass

    # Load profile config — the SINGLE source of truth for all filtering
    profile_config_full = None
    if wl.profile_id:
        try:
            from ..models.profile import Profile
            prof_res = await db.execute(select(Profile).where(Profile.id == wl.profile_id))
            prof = prof_res.scalars().first()
            if prof and prof.config:
                profile_config_full = prof.config
        except Exception as e:
            logger.debug("Failed to load profile %s: %s", wl.profile_id, e)

    # Merge global scoring rules with profile weights so both are respected
    merged_score_config = merge_score_config(global_score_config, profile_config_full)
    _score_engine = _SE(merged_score_config)
    live_score_map: Dict[str, float] = {}
    for sym in base_symbols:
        ind = ind_map.get(sym, {})
        meta = meta_map.get(sym, {})
        if ind:
            eval_data = {
                **ind,
                "price":      meta.get("price", 0),
                "volume_24h": meta.get("volume_24h", 0),
                "market_cap": meta.get("market_cap", 0),
                "change_24h": meta.get("price_change_24h", 0),
            }
            r = _score_engine.compute_alpha_score(eval_data)
            live_score_map[sym] = round(r.get("total_score", 0), 1)
        else:
            live_score_map[sym] = precomp_score_map.get(sym, 0.0)

    # Only consider scoring data available if we have REAL indicator data,
    # pre-computed scores, or market metadata — not just fallback zeros from
    # the live_score_map dict comprehension.  This prevents the min_score gate
    # from filtering out all assets on the first refresh when no data exists.
    scoring_data_available = bool(ind_map) or bool(precomp_score_map) or bool(meta_map)

    # Extract profile-level filter settings
    pf_cfg = (profile_config_full or {}).get("filters", {})
    p_conditions = pf_cfg.get("conditions", [])
    p_logic = pf_cfg.get("logic", "AND")
    profile_min_score: float = float(pf_cfg.get("min_score", 0))
    profile_require_signal: bool = bool(pf_cfg.get("require_signal", False))

    # Level-aware gating: match pipeline_scan behaviour where min_score is
    # only applied for L2+ and signal requirement only for L3.  Custom and L1
    # levels receive structural filters only (market_cap, volume, etc.).
    effective_level = wl.level if wl.level in ("L1", "L2", "L3") else "L1"
    should_apply_min_score = effective_level in ("L2", "L3")
    should_require_signal = effective_level == "L3"

    # Evaluate signals when the PROFILE requires them
    sig_conditions = []
    if profile_config_full:
        sig_conditions = (
            profile_config_full.get("entry_triggers", {}).get("conditions") or
            profile_config_full.get("signals", {}).get("conditions") or
            []
        )

    signal_status: Dict[str, bool] = {}
    if profile_require_signal and sig_conditions:
        from ..services.signal_engine import SignalEngine as _SigE
        sig_cfg = (profile_config_full or {}).get("signals", {})
        _sig_engine = _SigE(sig_cfg)
        for sym in base_symbols:
            ind = ind_map.get(sym, {})
            alpha = live_score_map.get(sym, 0.0)
            sig_result = _sig_engine.evaluate(ind, alpha)
            signal_status[sym] = sig_result.get("signal", False)
    elif profile_require_signal and not sig_conditions:
        # No signal conditions configured in profile — treat all as passing
        for sym in base_symbols:
            signal_status[sym] = True

    now = datetime.now(timezone.utc)
    assets_out: List[Dict[str, Any]] = []

    for symbol in base_symbols:
        alpha = live_score_map.get(symbol, 0.0)

        # Apply profile-level min_score gate (only when scoring data exists
        # AND the level warrants it — L1/custom never gate on min_score).
        if scoring_data_available:
            if should_apply_min_score and profile_min_score and alpha < profile_min_score:
                continue
            # Signal check from profile (only for L3)
            if should_require_signal and profile_require_signal and not signal_status.get(symbol, True):
                continue

        meta = meta_map.get(symbol, {})
        ind = ind_map.get(symbol, {})
        # Build asset dict with BOTH meta AND indicator data so that profile
        # filter conditions on indicator fields (atr_pct, rsi, etc.) can be
        # evaluated properly in _passes_profile_filters.
        asset_entry: Dict[str, Any] = {
            "symbol":           symbol,
            "current_price":    meta.get("price"),
            "price_change_24h": meta.get("price_change_24h"),
            "change_24h":       meta.get("price_change_24h"),   # alias for conditions
            "volume_24h":       meta.get("volume_24h"),
            "market_cap":       meta.get("market_cap"),
            "alpha_score":      alpha if scoring_data_available else None,
        }
        # Merge indicator values (skip non-scalar) for profile filter evaluation
        for k, v in ind.items():
            if isinstance(v, (int, float, bool)) and k not in asset_entry:
                asset_entry[k] = v
        assets_out.append(asset_entry)

    # Apply profile filter conditions (market_cap, volume_24h, Change 24h%, etc.)
    # IMPORTANT: Only apply meta-based filters when market data is actually available.
    # If meta_map is empty (no market data in DB yet), skipping strict meta conditions
    # prevents the watchlist from being wiped on first run / before data collection.
    if wl.profile_id and assets_out and profile_config_full and p_conditions:
        _STRICT_META_FIELDS = frozenset({
            "volume_24h", "market_cap", "price", "current_price",
            "change_24h", "price_change_24h", "change_24h_pct",
            "spread_pct", "orderbook_depth_usdt",
        })
        # Check how many symbols actually have market data
        symbols_with_meta = sum(1 for s in base_symbols if meta_map.get(s))
        # Only apply strict meta conditions when at least 10% of symbols have market data.
        # If we have no meta data at all, skip meta conditions to prevent wiping the watchlist.
        if symbols_with_meta == 0:
            # No market data available — skip meta conditions, keep indicator-only conditions
            non_meta_conds = [c for c in p_conditions if c.get("field") not in _STRICT_META_FIELDS]
            if non_meta_conds:
                before = len(assets_out)
                assets_out = [a for a in assets_out if _passes_profile_filters(a, non_meta_conds, p_logic)]
                logger.info(
                    "Pipeline profile filter [%s / %s]: no meta data — applying indicator-only "
                    "conditions (%d conditions): %d → %d assets",
                    wl.name, wl.level, len(non_meta_conds), before, len(assets_out),
                )
            else:
                logger.info(
                    "Pipeline profile filter [%s / %s]: no market data available yet — "
                    "skipping all meta conditions to preserve %d assets.",
                    wl.name, wl.level, len(assets_out),
                )
        else:
            before = len(assets_out)
            assets_out = [a for a in assets_out if _passes_profile_filters(a, p_conditions, p_logic)]
            logger.info(
                "Pipeline profile filter [%s / %s]: %d → %d assets (removed %d) "
                "[%d/%d symbols had market data]",
                wl.name, wl.level, before, len(assets_out), before - len(assets_out),
                symbols_with_meta, len(base_symbols),
            )

    # ── Anti-bad-entry blocking: remove assets that violate microstructure rules ──
    # These assets should NOT persist in any watchlist level.
    if assets_out:
        from ..utils.blocking_rules import is_blocked as _is_blocked
        before_block = len(assets_out)
        assets_out = [a for a in assets_out if not _is_blocked(a)]
        if before_block != len(assets_out):
            logger.info(
                "[Pipeline] %s (%s): anti-bad-entry removed %d/%d assets on refresh",
                wl.name, wl.level, before_block - len(assets_out), before_block,
            )

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
            row.refreshed_at     = now
            # Re-activate asset if it was previously marked as "down"
            if row.level_direction == "down":
                row.level_direction = "up"
                row.level_change_at = now
                asset_data["level_direction"] = "up"
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
                refreshed_at=now,
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

    # Track when this watchlist was last refreshed
    wl.last_scanned_at = now

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

    # On-demand computation: if some symbols have no indicators in DB OR are
    # missing key indicator fields (stale cache), fetch OHLCV and recompute.
    _KEY_INDICATOR_FIELDS = {"taker_ratio", "ema9_distance_pct", "rsi"}
    if symbols:
        missing = [
            s for s in symbols
            if not ind_map.get(s) or not _KEY_INDICATOR_FIELDS.issubset(ind_map[s].keys())
        ]
        if missing:
            on_demand = await _compute_indicators_on_demand(db, missing)
            ind_map.update(on_demand)

    # ── Compute alpha scores on-demand using global /settings/score config ──────
    # Uses global scoring rules merged with the profile's Alpha Score Weights
    # so watchlist scores respect both configurations.
    score_override: Dict[str, Optional[float]] = {}
    if ind_map:
        try:
            from ..services.score_engine import ScoreEngine as _SE, merge_score_config
            from ..services.seed_service import DEFAULT_SCORE
            from ..services.config_service import config_service

            global_score_config = None
            try:
                global_score_config = await config_service.get_config(db, "score", user_id)
            except Exception:
                pass
            merged = merge_score_config(global_score_config or DEFAULT_SCORE, profile_config)
            _score_engine = _SE(merged)

            _to_update: list = []
            for a in assets:
                ind = ind_map.get(a.symbol)
                if not ind:
                    continue
                eval_data = {
                    "symbol":     a.symbol,
                    "price":      float(a.current_price)    if a.current_price    else 0.0,
                    "volume_24h": float(a.volume_24h)       if a.volume_24h       else 0.0,
                    "market_cap": float(a.market_cap)       if a.market_cap       else 0.0,
                    "change_24h": float(a.price_change_24h) if a.price_change_24h else 0.0,
                    **ind,
                }
                result = _score_engine.compute_alpha_score(eval_data)
                fresh_score = result.get("total_score")
                if fresh_score is not None:
                    score_override[a.symbol] = round(float(fresh_score), 1)
                    # Only update DB when score changed (avoids noisy writes)
                    stored = float(a.alpha_score) if a.alpha_score is not None else None
                    if stored != score_override[a.symbol]:
                        _to_update.append((score_override[a.symbol], str(a.watchlist_id), a.symbol))

            if _to_update:
                for sc, wid, sym in _to_update:
                    await db.execute(
                        text("UPDATE pipeline_watchlist_assets SET alpha_score = :sc "
                             "WHERE watchlist_id = :wid AND symbol = :sym"),
                        {"sc": sc, "wid": wid, "sym": sym},
                    )
                await db.commit()
        except Exception as _e:
            logger.debug("[Pipeline] On-demand scoring error: %s", _e)

    # Fresh meta (some values may be missing from pipeline_watchlist_assets)
    meta_map: Dict[str, Dict[str, Any]] = {}
    if symbols:
        try:
            meta_rows = (await db.execute(
                text("""
                    SELECT symbol, price_change_24h, volume_24h, market_cap,
                           spread_pct, orderbook_depth_usdt
                    FROM market_metadata WHERE symbol = ANY(:symbols)
                """),
                {"symbols": list(symbols)},
            )).fetchall()
            meta_map = {
                r.symbol: {
                    "price_change_24h":     float(r.price_change_24h)     if r.price_change_24h     else None,
                    "volume_24h":           float(r.volume_24h)           if r.volume_24h           else None,
                    "market_cap":           float(r.market_cap)           if r.market_cap           else None,
                    "spread_pct":           float(r.spread_pct)           if r.spread_pct           else None,
                    "orderbook_depth_usdt": float(r.orderbook_depth_usdt) if r.orderbook_depth_usdt else None,
                }
                for r in meta_rows
            }
        except Exception:
            # Fallback: spread_pct / orderbook_depth_usdt columns may not exist yet
            try:
                meta_rows = (await db.execute(
                    text("""
                        SELECT symbol, price_change_24h, volume_24h, market_cap
                        FROM market_metadata WHERE symbol = ANY(:symbols)
                    """),
                    {"symbols": list(symbols)},
                )).fetchall()
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

    # ── On-demand orderbook metrics for symbols missing depth data ──────────────
    # When spread_pct / orderbook_depth_usdt are NULL in market_metadata, fetch
    # orderbook data from Gate.io and update both meta_map and the DB.
    if symbols:
        need_orderbook = [
            s for s in symbols
            if not meta_map.get(s, {}).get("orderbook_depth_usdt")
        ]
        if need_orderbook:
            try:
                from ..services.market_data_service import market_data_service
                import asyncio

                sem = asyncio.Semaphore(8)

                async def _fetch_ob(sym: str):
                    async with sem:
                        return sym, await market_data_service.fetch_orderbook_metrics(sym, depth=10)

                ob_results = await asyncio.gather(
                    *[_fetch_ob(s) for s in need_orderbook[:40]],
                    return_exceptions=True,
                )
                _now = datetime.now(timezone.utc)
                for item in ob_results:
                    if isinstance(item, Exception):
                        continue
                    sym, ob = item
                    if not ob:
                        continue
                    # Update meta_map so _asset_to_dict will use these values
                    if sym not in meta_map:
                        meta_map[sym] = {}
                    meta_map[sym]["spread_pct"] = ob.get("spread_pct")
                    meta_map[sym]["orderbook_depth_usdt"] = ob.get("orderbook_depth_usdt")
                    # Persist to DB for subsequent requests
                    try:
                        await db.execute(
                            text("""
                                INSERT INTO market_metadata (symbol, spread_pct, orderbook_depth_usdt, last_updated)
                                VALUES (:sym, :spread, :depth, :ts)
                                ON CONFLICT (symbol) DO UPDATE SET
                                    spread_pct = COALESCE(:spread, market_metadata.spread_pct),
                                    orderbook_depth_usdt = COALESCE(:depth, market_metadata.orderbook_depth_usdt),
                                    last_updated = :ts
                            """),
                            {
                                "sym":    sym,
                                "spread": ob.get("spread_pct"),
                                "depth":  ob.get("orderbook_depth_usdt"),
                                "ts":     _now,
                            },
                        )
                    except Exception:
                        pass
                try:
                    await db.commit()
                except Exception:
                    pass
            except Exception as _e:
                logger.debug("[Pipeline] On-demand orderbook fetch error: %s", _e)

    enriched = [
        _asset_to_dict(
            a,
            indicators=ind_map.get(a.symbol),
            meta=meta_map.get(a.symbol),
            override_score=score_override.get(a.symbol),
        )
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


@router.get("/{watchlist_id}/signals")
async def get_watchlist_signals(
    watchlist_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """
    L3 SIGNALS — Return pipeline watchlist assets that have active entry signals.

    Pipeline:
    1. Load assets from pipeline_watchlist_assets (already pre-filtered through L1/L2/L3)
    2. Load the watchlist's Profile (via profile_id)
    3. Fetch latest indicators from the indicators table for each symbol
    4. Apply the Profile's entry_triggers via SignalEngine
    5. Return all assets with their signal status; triggered=True assets appear first
    """
    from ..services.signal_engine import SignalEngine
    from ..models.profile import Profile

    wl_result = await db.execute(
        select(PipelineWatchlist).where(
            PipelineWatchlist.id == watchlist_id,
            PipelineWatchlist.user_id == user_id,
        )
    )
    wl = wl_result.scalars().first()
    if not wl:
        raise HTTPException(status_code=404, detail="Watchlist not found")

    # Load pipeline assets
    assets_result = await db.execute(
        select(PipelineWatchlistAsset).where(PipelineWatchlistAsset.watchlist_id == watchlist_id)
    )
    pipeline_assets = assets_result.scalars().all()

    if not pipeline_assets:
        return {
            "watchlist": wl.name,
            "watchlist_id": str(watchlist_id),
            "level": wl.level,
            "profile": None,
            "profile_id": None,
            "total_assets": 0,
            "signals_count": 0,
            "signals": [],
        }

    symbols = [a.symbol for a in pipeline_assets]

    # Fetch latest indicators
    try:
        ind_rows = await db.execute(
            text("""
                SELECT DISTINCT ON (symbol) symbol, indicators_json
                FROM indicators
                WHERE symbol = ANY(:symbols)
                ORDER BY symbol, time DESC
            """),
            {"symbols": symbols},
        )
        indicators_map = {r.symbol: r.indicators_json or {} for r in ind_rows.fetchall()}
    except Exception:
        indicators_map = {}

    # Load profile for signal evaluation
    profile_name = None
    profile_id_str = None
    signal_engine: Optional[SignalEngine] = None

    if wl.profile_id:
        prof_res = await db.execute(select(Profile).where(Profile.id == wl.profile_id))
        prof = prof_res.scalars().first()
        if prof:
            profile_name = prof.name
            profile_id_str = str(prof.id)
            cfg = prof.config or {}
            sig_cfg = cfg.get("entry_triggers") or cfg.get("signals")
            if sig_cfg and sig_cfg.get("conditions"):
                signal_engine = SignalEngine(sig_cfg)

    # Evaluate each asset
    triggered_signals = []
    all_signals = []

    for pa in pipeline_assets:
        indicators = indicators_map.get(pa.symbol, {})
        alpha = float(pa.alpha_score) if pa.alpha_score else 0.0

        signal_result = {"signal": False, "direction": None, "matched": [], "failed_required": []}
        if signal_engine:
            signal_result = signal_engine.evaluate(indicators, alpha)

        asset_out = {
            "symbol":         pa.symbol,
            "price":          float(pa.current_price) if pa.current_price else None,
            "change_24h":     float(pa.price_change_24h) if pa.price_change_24h else None,
            "volume_24h":     float(pa.volume_24h) if pa.volume_24h else None,
            "market_cap":     float(pa.market_cap) if pa.market_cap else None,
            "alpha_score":    alpha,
            "signal":         signal_result.get("signal", False),
            "direction":      signal_result.get("direction"),
            "matched":        signal_result.get("matched", []),
            "failed_required": signal_result.get("failed_required", []),
        }
        all_signals.append(asset_out)
        if asset_out["signal"]:
            triggered_signals.append(asset_out)

    # Sort: triggered first, then by alpha_score descending
    all_signals.sort(key=lambda x: (not x["signal"], -(x["alpha_score"] or 0)))

    return {
        "watchlist":     wl.name,
        "watchlist_id":  str(watchlist_id),
        "level":         wl.level,
        "profile":       profile_name,
        "profile_id":    profile_id_str,
        "total_assets":  len(all_signals),
        "signals_count": len(triggered_signals),
        "signals":       all_signals,
    }


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
    payload: Dict[str, Any] = Body(...),
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
        filters_json={},
    )
    await _get_or_create(
        "L3 Signals", "L3",
        source_watchlist_id=l2.id,
        filters_json={},
    )

    await db.commit()
    return {"created": created, "total_created": len(created)}


# ── Debug endpoint ────────────────────────────────────────────────────────────

@router.get("/{watchlist_id}/debug")
async def debug_watchlist_pipeline(
    watchlist_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """
    Pipeline observability report — shows exactly how many assets are dropped at each stage.

    Returns:
      - stages.1_pool_coins_total: raw coins in the source pool
      - stages.2_after_format_normalize: after symbol normalization (BTC_USDT format)
      - stages.3_symbols_with_market_data: how many have entries in market_metadata
      - stages.4_symbols_passing_profile_filters: how many pass the profile conditions
      - stages.5_active_in_watchlist: how many are active in pipeline_watchlist_assets
      - symbols_missing_market_data: list of symbols not found in market_metadata (first 50)
      - filter_drop_reasons: breakdown per condition of how many assets were dropped
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

    report: dict = {
        "watchlist_id":   str(watchlist_id),
        "watchlist_name": wl.name,
        "level":          wl.level,
        "source_pool_id": str(wl.source_pool_id) if wl.source_pool_id else None,
        "profile_id":     str(wl.profile_id) if wl.profile_id else None,
        "stages": {},
        "filter_drop_reasons": [],
        "symbols_missing_market_data": [],
        "error": None,
    }

    def _norm(s: str) -> str:
        s = s.upper().strip()
        if "_" not in s and s.endswith("USDT"):
            return s[:-4] + "_USDT"
        return s

    try:
        # ── Stage 1: Raw pool coins ────────────────────────────────────────────
        raw_symbols: List[str] = []
        if wl.source_pool_id:
            coin_rows = (await db.execute(text("""
                SELECT symbol FROM pool_coins
                WHERE pool_id = :pool_id AND is_active = TRUE
            """), {"pool_id": str(wl.source_pool_id)})).fetchall()
            raw_symbols = [r.symbol for r in coin_rows]
        elif wl.source_watchlist_id:
            asset_rows = (await db.execute(text("""
                SELECT symbol FROM pipeline_watchlist_assets
                WHERE watchlist_id = :wid
                  AND (level_direction IS NULL OR level_direction = 'up')
            """), {"wid": str(wl.source_watchlist_id)})).fetchall()
            raw_symbols = [r.symbol for r in asset_rows]

        report["stages"]["1_pool_coins_total"] = len(raw_symbols)

        # ── Stage 2: After format normalization ──────────────────────────────
        normalized = list(dict.fromkeys(_norm(s) for s in raw_symbols))
        removed_by_norm = [s for s in raw_symbols if s != _norm(s)]
        report["stages"]["2_after_format_normalize"] = len(normalized)
        report["stages"]["2_symbols_auto_normalized"] = removed_by_norm[:20]

        # ── Stage 3: Market metadata coverage ────────────────────────────────
        found_in_meta: set = set()
        if normalized:
            meta_rows = (await db.execute(text("""
                SELECT symbol FROM market_metadata WHERE symbol = ANY(:syms)
            """), {"syms": normalized})).fetchall()
            found_in_meta = {r.symbol for r in meta_rows}
        missing_from_meta = [s for s in normalized if s not in found_in_meta]
        report["stages"]["3_symbols_with_market_data"] = len(found_in_meta)
        report["stages"]["3_symbols_missing_market_data"] = len(missing_from_meta)
        report["symbols_missing_market_data"] = missing_from_meta[:50]

        # ── Stage 4: Profile filter pass rate ─────────────────────────────────
        profile_config = None
        if wl.profile_id:
            from ..models.profile import Profile as _Prof
            prof = (await db.execute(
                select(_Prof).where(_Prof.id == wl.profile_id)
            )).scalars().first()
            if prof:
                profile_config = prof.config

        filter_pass_count = len(normalized)  # default: all pass when no profile
        condition_drop_map: dict = {}

        if profile_config:
            pf = profile_config.get("filters", {}) or {}
            p_conditions = pf.get("conditions", [])
            if p_conditions and found_in_meta:
                # Fetch meta for found symbols
                meta_rows2 = (await db.execute(text("""
                    SELECT symbol, price, price_change_24h, volume_24h, market_cap
                    FROM market_metadata WHERE symbol = ANY(:syms)
                """), {"syms": list(found_in_meta)})).fetchall()
                meta_map = {r.symbol: {
                    "price": float(r.price) if r.price else None,
                    "price_change_24h": float(r.price_change_24h) if r.price_change_24h else None,
                    "change_24h": float(r.price_change_24h) if r.price_change_24h else None,
                    "volume_24h": float(r.volume_24h) if r.volume_24h else None,
                    "market_cap": float(r.market_cap) if r.market_cap else None,
                } for r in meta_rows2}

                filter_pass_count = 0
                for sym in found_in_meta:
                    asset = {"symbol": sym, **meta_map.get(sym, {})}
                    passed = _passes_profile_filters(asset, p_conditions)
                    if passed:
                        filter_pass_count += 1
                    else:
                        # Find which condition failed
                        for cond in p_conditions:
                            field = cond.get("field", "unknown")
                            single_result = _passes_profile_filters(asset, [cond])
                            if not single_result:
                                condition_drop_map[field] = condition_drop_map.get(field, 0) + 1

        report["stages"]["4_symbols_passing_profile_filters"] = filter_pass_count
        if condition_drop_map:
            report["filter_drop_reasons"] = [
                {"field": k, "assets_dropped": v}
                for k, v in sorted(condition_drop_map.items(), key=lambda x: -x[1])
            ]

        # ── Stage 5: Current watchlist state ─────────────────────────────────
        count_row = (await db.execute(text("""
            SELECT
                COUNT(*) FILTER (WHERE level_direction IS NULL OR level_direction = 'up') AS active_count,
                COUNT(*) FILTER (WHERE level_direction = 'down') AS down_count
            FROM pipeline_watchlist_assets WHERE watchlist_id = :wid
        """), {"wid": str(watchlist_id)})).fetchone()

        report["stages"]["5_active_in_watchlist"] = count_row.active_count if count_row else 0
        report["stages"]["5_down_in_watchlist"]   = count_row.down_count   if count_row else 0

        # ── Summary ───────────────────────────────────────────────────────────
        report["summary"] = (
            f"Pool: {report['stages'].get('1_pool_coins_total', 0)} coins → "
            f"normalize: {report['stages'].get('2_after_format_normalize', 0)} → "
            f"market_data: {report['stages'].get('3_symbols_with_market_data', 0)} → "
            f"profile_filter: {report['stages'].get('4_symbols_passing_profile_filters', 0)} → "
            f"watchlist: {report['stages'].get('5_active_in_watchlist', 0)} active"
        )

    except Exception as exc:
        logger.exception("debug_watchlist_pipeline failed for %s: %s", watchlist_id, exc)
        report["error"] = str(exc)

    return report
