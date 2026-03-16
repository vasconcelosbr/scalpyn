"""Watchlist API — symbols with live scores, indicators, and prices."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from typing import Optional
from uuid import UUID
import logging

from ..database import get_db
from .config import get_current_user_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/watchlist", tags=["Watchlist"])


@router.get("/")
async def get_watchlist(
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """Get all tracked symbols with latest score, indicators, and price data."""
    try:
        # Get latest alpha scores
        scores_query = text("""
            SELECT DISTINCT ON (symbol)
                symbol, score, liquidity_score, market_structure_score,
                momentum_score, signal_score, components_json, time
            FROM alpha_scores
            ORDER BY symbol, time DESC
        """)
        scores_result = await db.execute(scores_query)
        scores_rows = scores_result.fetchall()

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

        # Build response
        scores_map = {}
        for row in scores_rows:
            scores_map[row.symbol] = {
                "score": float(row.score) if row.score else 0,
                "liquidity_score": float(row.liquidity_score) if row.liquidity_score else 0,
                "market_structure_score": float(row.market_structure_score) if row.market_structure_score else 0,
                "momentum_score": float(row.momentum_score) if row.momentum_score else 0,
                "signal_score": float(row.signal_score) if row.signal_score else 0,
                "components": row.components_json,
            }

        indicators_map = {}
        for row in indicators_rows:
            indicators_map[row.symbol] = row.indicators_json or {}

        watchlist = []
        for row in metadata_rows:
            symbol = row.symbol
            score_data = scores_map.get(symbol, {})
            inds = indicators_map.get(symbol, {})

            # Derive trend from indicators
            trend = "Range"
            if score_data.get("score", 0) >= 70:
                trend = "Bullish"
            elif score_data.get("score", 0) <= 30:
                trend = "Bearish"

            score_val = score_data.get("score", 0)

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

        scores_query = text("""
            SELECT score, liquidity_score, market_structure_score,
                   momentum_score, signal_score, components_json, time
            FROM alpha_scores
            WHERE symbol = :symbol
            ORDER BY time DESC LIMIT 1
        """)
        score_result = await db.execute(scores_query, {"symbol": symbol})
        score_row = score_result.fetchone()

        return {
            "symbol": symbol,
            "indicators": row.indicators_json if row else {},
            "score": {
                "total": float(score_row.score) if score_row else 0,
                "liquidity": float(score_row.liquidity_score) if score_row and score_row.liquidity_score else 0,
                "market_structure": float(score_row.market_structure_score) if score_row and score_row.market_structure_score else 0,
                "momentum": float(score_row.momentum_score) if score_row and score_row.momentum_score else 0,
                "signal": float(score_row.signal_score) if score_row and score_row.signal_score else 0,
                "components": score_row.components_json if score_row else {},
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
