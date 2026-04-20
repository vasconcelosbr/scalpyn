"""Watchlist API — symbols with live scores, indicators, and prices."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from typing import Any, Dict, Optional
from uuid import UUID
import logging

from ..database import get_db
from .config import get_current_user_id
from ..services.config_service import config_service
from ..services.score_engine import ScoreEngine
from ..services.seed_service import DEFAULT_SCORE

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/watchlist", tags=["Watchlist"])


async def _load_score_engine(db: AsyncSession, user_id: UUID) -> ScoreEngine:
    score_config = DEFAULT_SCORE
    try:
        cfg = await config_service.get_config(db, "score", user_id)
        if cfg and (cfg.get("scoring_rules") or cfg.get("rules")):
            score_config = cfg
    except Exception as exc:
        logger.debug("watchlist: unable to load score config: %s", exc)
    return ScoreEngine(score_config)


def _build_eval_data(metadata_row: Any | None, indicators: Dict[str, Any]) -> Dict[str, Any]:
    if metadata_row is None:
        return dict(indicators or {})
    return {
        "symbol": metadata_row.symbol,
        "price": float(metadata_row.price) if metadata_row.price is not None else 0.0,
        "volume_24h": float(metadata_row.volume_24h) if metadata_row.volume_24h is not None else 0.0,
        "market_cap": float(metadata_row.market_cap) if metadata_row.market_cap is not None else 0.0,
        # Keep both aliases because profile/filter code paths still read either
        # `change_24h` or `price_change_24h` depending on the caller.
        "change_24h": float(metadata_row.price_change_24h) if metadata_row.price_change_24h is not None else 0.0,
        "price_change_24h": float(metadata_row.price_change_24h) if metadata_row.price_change_24h is not None else 0.0,
        **(indicators or {}),
    }


@router.get("/")
async def get_watchlist(
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """Get all tracked symbols with latest score, indicators, and price data."""
    try:
        # Get latest indicators
        indicators_query = text("""
            SELECT DISTINCT ON (symbol)
                symbol, indicators_json, time
            FROM indicators
            ORDER BY symbol, time DESC
        """)
        indicators_result = await db.execute(indicators_query)
        indicators_rows = indicators_result.fetchall()

        # Get market metadata
        metadata_query = text("""
            SELECT symbol, name, market_cap, volume_24h, price, price_change_24h, ranking
            FROM market_metadata
            ORDER BY ranking ASC NULLS LAST
        """)
        metadata_result = await db.execute(metadata_query)
        metadata_rows = metadata_result.fetchall()

        indicators_map = {}
        for row in indicators_rows:
            indicators_map[row.symbol] = row.indicators_json or {}

        score_engine = await _load_score_engine(db, user_id)
        watchlist = []
        for row in metadata_rows:
            symbol = row.symbol
            inds = indicators_map.get(symbol, {})
            score_result = score_engine.compute_alpha_score(_build_eval_data(row, inds))
            components = score_result.get("components", {})
            score_val = float(score_result.get("total_score", 0) or 0)
            score_data = {
                "score": score_val,
                "liquidity_score": float(components.get("liquidity_score", 0) or 0),
                "market_structure_score": float(components.get("market_structure_score", 0) or 0),
                "momentum_score": float(components.get("momentum_score", 0) or 0),
                "signal_score": float(components.get("signal_score", 0) or 0),
                "classification": score_result.get("classification"),
                "matched_rules": score_result.get("matched_rules", []),
            }

            # Derive trend from indicators
            trend = "Range"
            if score_data.get("score", 0) >= 70:
                trend = "Bullish"
            elif score_data.get("score", 0) <= 30:
                trend = "Bearish"

            watchlist.append({
                "symbol": symbol,
                "name": row.name,
                "price": float(row.price) if row.price else 0,
                "change_24h": float(row.price_change_24h) if row.price_change_24h else 0,
                "market_cap": float(row.market_cap) if row.market_cap else 0,
                "volume_24h": float(row.volume_24h) if row.volume_24h else 0,
                "ranking": row.ranking,
                "trend": trend,
                "score": score_val,
                "score_level": _score_level(score_val),
                "score_components": score_data,
                "indicators": inds,
            })

        return {"watchlist": watchlist, "total": len(watchlist)}

    except Exception as e:
        logger.warning(f"Watchlist query failed (tables may not exist yet): {e}")
        return {"watchlist": [], "total": 0}


@router.get("/{symbol}")
async def get_symbol_detail(
    symbol: str,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """Get detailed indicators for a single symbol."""
    try:
        indicators_query = text("""
            SELECT indicators_json, time FROM indicators
            WHERE symbol = :symbol
            ORDER BY time DESC LIMIT 1
        """)
        result = await db.execute(indicators_query, {"symbol": symbol})
        row = result.fetchone()

        metadata_query = text("""
            SELECT symbol, name, market_cap, volume_24h, price, price_change_24h, ranking
            FROM market_metadata
            WHERE symbol = :symbol
            LIMIT 1
        """)
        metadata_result = await db.execute(metadata_query, {"symbol": symbol})
        metadata_row = metadata_result.fetchone()

        score_engine = await _load_score_engine(db, user_id)
        indicators = row.indicators_json if row else {}
        eval_data = _build_eval_data(metadata_row, indicators) if metadata_row else indicators
        score = score_engine.compute_alpha_score(eval_data)
        components = score.get("components", {})

        return {
            "symbol": symbol,
            "indicators": indicators,
            "score": {
                "total": float(score.get("total_score", 0) or 0),
                "liquidity": float(components.get("liquidity_score", 0) or 0),
                "market_structure": float(components.get("market_structure_score", 0) or 0),
                "momentum": float(components.get("momentum_score", 0) or 0),
                "signal": float(components.get("signal_score", 0) or 0),
                "components": {
                    "classification": score.get("classification"),
                    "matched_rules": score.get("matched_rules", []),
                },
            },
            "updated_at": row.time.isoformat() if row else None,
        }
    except Exception as e:
        logger.warning(f"Symbol detail query failed: {e}")
        return {"symbol": symbol, "indicators": {}, "score": {}, "updated_at": None}


def _score_level(score: float) -> str:
    if score >= 80:
        return "excellent"
    elif score >= 60:
        return "good"
    elif score >= 40:
        return "neutral"
    elif score >= 25:
        return "low"
    return "critical"
