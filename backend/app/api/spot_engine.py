"""Spot Engine API — start, pause, resume, stop, and status endpoints.

Routes:
  POST /api/spot-engine/start   → build scanner from DB config and start
  POST /api/spot-engine/pause   → pause (keeps state, no new buys)
  POST /api/spot-engine/resume  → resume after pause
  POST /api/spot-engine/stop    → gracefully stop and clean up
  GET  /api/spot-engine/status  → engine + capital + positions summary
"""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..api.config import get_current_user_id
from ..engines.spot_scanner import (
    SpotScanner,
    build_scanner_from_db,
    get_engine,
)
from ..exchange_adapters.gate_adapter import GateAdapter
from ..models.exchange_connection import ExchangeConnection
from ..models.config_profile import ConfigProfile
from ..schemas.spot_engine_config import SpotEngineConfig
from ..engines.spot_capital_manager import SpotCapitalManager
from ..engines.spot_position_manager import SpotPositionManager
from ..utils.encryption import decrypt
from ..utils.exchange_names import exchange_name_matches
from sqlalchemy import select

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/spot-engine", tags=["Spot Engine"])


@router.post("/start")
async def start_engine(
    user_id: UUID = Depends(get_current_user_id),
):
    """
    Build the Spot Engine from DB config and credentials, then start the scanner loop.
    Idempotent: if already running, returns current status.
    """
    uid = str(user_id)
    existing = get_engine(uid)
    if existing and existing._running and not existing._paused:
        return {"status": "already_running", "engine": existing.status()}

    try:
        scanner = await build_scanner_from_db(uid)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Failed to build scanner for user %s: %s", uid, e)
        raise HTTPException(status_code=500, detail=f"Failed to initialize engine: {e}")

    scanner.start()
    return {"status": "started", "engine": scanner.status()}


@router.post("/pause")
async def pause_engine(user_id: UUID = Depends(get_current_user_id)):
    """Pause the running engine (no new buys/sells until resumed)."""
    scanner = get_engine(str(user_id))
    if not scanner:
        raise HTTPException(status_code=404, detail="No running spot engine for this user")
    scanner.pause()
    return {"status": "paused", "engine": scanner.status()}


@router.post("/resume")
async def resume_engine(user_id: UUID = Depends(get_current_user_id)):
    """Resume a paused engine."""
    scanner = get_engine(str(user_id))
    if not scanner:
        raise HTTPException(status_code=404, detail="No spot engine found for this user")
    scanner.resume()
    return {"status": "resumed", "engine": scanner.status()}


@router.post("/stop")
async def stop_engine(user_id: UUID = Depends(get_current_user_id)):
    """Gracefully stop the engine and release resources."""
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
    Full status: engine state + capital overview + position summary.
    Works even when engine is stopped (returns position/capital info from DB).
    """
    uid     = str(user_id)
    scanner = get_engine(uid)

    engine_info = scanner.status() if scanner else {
        "running": False, "paused": False, "cycle": 0,
        "started_at": None, "last_error": None, "user_id": uid,
    }

    # Load config for capital/position display
    try:
        cfg_row = await db.execute(
            select(ConfigProfile).where(
                ConfigProfile.user_id == user_id,
                ConfigProfile.config_type == "spot_engine",
                ConfigProfile.is_active == True,
            )
        )
        cfg_row = cfg_row.scalars().first()
        spot_cfg = SpotEngineConfig.from_config_json(cfg_row.config_json) if cfg_row else SpotEngineConfig()
    except Exception:
        spot_cfg = SpotEngineConfig()

    capital_summary   = {}
    positions_summary = {}

    # Try to get live balance if exchange is connected
    try:
        exc_row = await db.execute(
            select(ExchangeConnection).where(
                ExchangeConnection.user_id == user_id,
                exchange_name_matches(ExchangeConnection.exchange_name, "gate.io"),
                ExchangeConnection.is_active == True,
            )
        )
        exc_row = exc_row.scalars().first()
        if exc_row:
            raw_key    = bytes(exc_row.api_key_encrypted)    if isinstance(exc_row.api_key_encrypted, memoryview) else exc_row.api_key_encrypted
            raw_secret = bytes(exc_row.api_secret_encrypted) if isinstance(exc_row.api_secret_encrypted, memoryview) else exc_row.api_secret_encrypted
            adapter    = GateAdapter(decrypt(raw_key).strip(), decrypt(raw_secret).strip())

            balance_data = await adapter.get_spot_balance()
            usdt_balance = next(
                (float(a["available"]) for a in balance_data if a.get("currency") == "USDT"), 0.0
            )

            capital_mgr   = SpotCapitalManager(spot_cfg.buying)
            capital_state = await capital_mgr.get_state(usdt_balance, db, uid)
            capital_summary = capital_state.to_dict()

            # Get tickers for current prices (needed for P&L)
            tickers = await adapter.get_tickers(market="spot")
            prices  = {t["currency_pair"]: float(t.get("last", 0)) for t in tickers if t.get("last")}

            position_mgr      = SpotPositionManager(spot_cfg)
            positions_summary = await position_mgr.get_position_summary(db, uid, prices)

    except Exception as e:
        logger.debug("Status: could not fetch live data: %s", e)
        capital_summary   = {"error": str(e)}
        positions_summary = {"error": str(e)}

    return {
        "engine":    engine_info,
        "capital":   capital_summary,
        "positions": positions_summary,
        "config":    spot_cfg.model_dump() if cfg_row else None,
    }


@router.get("/config/default")
async def get_default_config(_: UUID = Depends(get_current_user_id)):
    """Return the default SpotEngineConfig as JSON (for GUI initialization)."""
    return SpotEngineConfig().model_dump()
