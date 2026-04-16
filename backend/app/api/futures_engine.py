"""Futures Engine API — start, pause, resume, stop, and status endpoints.

Routes:
  POST /api/futures-engine/start         → build scanner from DB config and start
  POST /api/futures-engine/pause         → pause (keeps state, no new entries)
  POST /api/futures-engine/resume        → resume after pause
  POST /api/futures-engine/stop          → gracefully stop and clean up
  GET  /api/futures-engine/status        → engine + balance + positions summary
  GET  /api/futures-engine/config/default → default FuturesEngineConfig JSON
"""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from ..database import get_db
from ..api.config import get_current_user_id
from ..engines.futures_scanner import (
    FuturesScanner,
    build_futures_scanner,
    get_engine,
)
from ..exchange_adapters.gate_adapter import GateAdapter
from ..models.exchange_connection import ExchangeConnection
from ..models.config_profile import ConfigProfile
from ..models.trade import Trade
from ..schemas.futures_engine_config import FuturesEngineConfig
from ..utils.encryption import decrypt

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/futures-engine", tags=["Futures Engine"])


@router.post("/start")
async def start_engine(
    user_id: UUID = Depends(get_current_user_id),
):
    """
    Build the Futures Engine from DB config and credentials, then start the scanner loop.
    Idempotent: if already running, returns current status.
    """
    uid = str(user_id)
    existing = get_engine(uid)
    if existing and existing._running and not existing._paused:
        return {"status": "already_running", "engine": existing.status()}

    try:
        scanner = await build_futures_scanner(uid)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Failed to build futures scanner for user %s: %s", uid, e)
        raise HTTPException(status_code=500, detail=f"Failed to initialize futures engine: {e}")

    scanner.start()
    return {"status": "started", "engine": scanner.status()}


@router.post("/pause")
async def pause_engine(user_id: UUID = Depends(get_current_user_id)):
    """Pause the running futures engine (no new entries, management cycle continues)."""
    scanner = get_engine(str(user_id))
    if not scanner:
        raise HTTPException(status_code=404, detail="No running futures engine for this user")
    scanner.pause()
    return {"status": "paused", "engine": scanner.status()}


@router.post("/resume")
async def resume_engine(user_id: UUID = Depends(get_current_user_id)):
    """Resume a paused futures engine."""
    scanner = get_engine(str(user_id))
    if not scanner:
        raise HTTPException(status_code=404, detail="No futures engine found for this user")
    scanner.resume()
    return {"status": "resumed", "engine": scanner.status()}


@router.post("/stop")
async def stop_engine(user_id: UUID = Depends(get_current_user_id)):
    """Gracefully stop the futures engine and release resources."""
    scanner = get_engine(str(user_id))
    if not scanner:
        return {"status": "not_running"}
    await scanner.stop()
    return {"status": "stopped"}


@router.get("/status")
async def engine_status(
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """
    Full status: engine state + futures balance + open positions summary.
    Works even when engine is stopped (returns position info from DB).
    """
    uid     = str(user_id)
    scanner = get_engine(uid)

    engine_info = scanner.status() if scanner else {
        "running": False, "paused": False, "cycle": 0,
        "started_at": None, "last_error": None, "user_id": uid,
    }

    # Load config for display
    try:
        cfg_row = await db.execute(
            select(ConfigProfile).where(
                ConfigProfile.user_id == user_id,
                ConfigProfile.config_type == "futures_engine",
                ConfigProfile.is_active == True,
            )
        )
        cfg_row = cfg_row.scalars().first()
        fut_cfg = FuturesEngineConfig.model_validate(cfg_row.config_json) if cfg_row else FuturesEngineConfig()
    except Exception:
        fut_cfg  = FuturesEngineConfig()
        cfg_row  = None

    balance_summary   = {}
    positions_summary = {}

    try:
        exc_row = await db.execute(
            select(ExchangeConnection).where(
                ExchangeConnection.user_id == user_id,
                ExchangeConnection.exchange_name == "gate.io",
                ExchangeConnection.is_active == True,
            )
        )
        exc_row = exc_row.scalars().first()

        if exc_row:
            raw_key    = bytes(exc_row.api_key_encrypted)    if isinstance(exc_row.api_key_encrypted, memoryview) else exc_row.api_key_encrypted
            raw_secret = bytes(exc_row.api_secret_encrypted) if isinstance(exc_row.api_secret_encrypted, memoryview) else exc_row.api_secret_encrypted
            adapter    = GateAdapter(decrypt(raw_key).strip(), decrypt(raw_secret).strip())

            fut_balance = await adapter.get_futures_balance()
            balance_summary = {
                "total":     float(fut_balance.get("total",     0)),
                "available": float(fut_balance.get("available", 0)),
                "unrealized_pnl": float(fut_balance.get("unrealised_pnl", 0)),
                "order_margin":   float(fut_balance.get("order_margin", 0)),
            }

            # Open futures positions from DB
            positions_result = await db.execute(
                select(Trade).where(
                    Trade.user_id == str(user_id),
                    Trade.market_type == "futures",
                    Trade.status.in_(["ACTIVE", "open"]),
                )
            )
            open_positions = positions_result.scalars().all()

            # Fetch current prices for unrealized P&L
            try:
                tickers = await adapter.get_tickers(market="futures")
                prices  = {t["contract"]: float(t.get("last", 0)) for t in tickers if t.get("last")}
            except Exception:
                prices = {}

            pos_list = []
            for pos in open_positions:
                symbol    = pos.symbol
                entry     = float(pos.entry_price)
                qty       = float(pos.quantity)
                direction = pos.direction or "long"
                cur_price = prices.get(symbol, entry)
                leverage  = float(pos.leverage or 1)

                if direction == "long":
                    unrealized = (cur_price - entry) * qty * 0.0001
                else:
                    unrealized = (entry - cur_price) * qty * 0.0001

                pos_list.append({
                    "id":            str(pos.id),
                    "symbol":        symbol,
                    "direction":     direction,
                    "entry_price":   entry,
                    "current_price": cur_price,
                    "quantity":      qty,
                    "leverage":      leverage,
                    "liq_price":     float(pos.liq_price) if pos.liq_price else None,
                    "tp1":           float(pos.take_profit_price) if pos.take_profit_price else None,
                    "tp2":           float(pos.tp2_price) if pos.tp2_price else None,
                    "tp3":           float(pos.tp3_price) if pos.tp3_price else None,
                    "tp1_hit":       bool(pos.tp1_hit),
                    "tp2_hit":       bool(pos.tp2_hit),
                    "unrealized_pnl": round(unrealized, 4),
                    "risk_dollars":  float(pos.risk_dollars) if pos.risk_dollars else None,
                    "funding_cost":  float(pos.funding_cost_usdt) if pos.funding_cost_usdt else 0.0,
                    "opened_at":     pos.entry_at.isoformat() if pos.entry_at else None,
                    "hwm_price":     float(pos.hwm_price) if pos.hwm_price else None,
                    "trailing":      bool(pos.tp2_hit),
                })

            positions_summary = {
                "open_count": len(pos_list),
                "positions":  pos_list,
                "total_unrealized_pnl": round(sum(p["unrealized_pnl"] for p in pos_list), 4),
            }

    except Exception as e:
        logger.debug("Futures status: could not fetch live data: %s", e)
        balance_summary   = {"error": str(e)}
        positions_summary = {"error": str(e)}

    return {
        "engine":    engine_info,
        "balance":   balance_summary,
        "positions": positions_summary,
        "config":    fut_cfg.model_dump() if cfg_row else None,
    }


@router.get("/config/default")
async def get_default_config(_: UUID = Depends(get_current_user_id)):
    """Return the default FuturesEngineConfig as JSON (for GUI initialization)."""
    return FuturesEngineConfig().model_dump()
