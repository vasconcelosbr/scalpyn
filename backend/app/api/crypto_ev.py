from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.config import get_current_user_id
from ..database import get_db
from ..services.config_service import config_service

router = APIRouter(prefix="/api/crypto-ev", tags=["Crypto EV"])


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


async def _operational_view(db: AsyncSession, user_id: UUID) -> str:
    cfg = await config_service.get_config(db, "crypto_ev", user_id)
    view = ((cfg.get("views") or {}).get("operational_view") if cfg else None) or "spectrum"
    if view not in {"executable", "spectrum"}:
        return "spectrum"
    return view


@router.get("/scores")
async def list_crypto_ev_scores(
    view: Optional[str] = Query(default=None, pattern="^(executable|spectrum)$"),
    state: Optional[str] = Query(default=None),
    min_n: Optional[int] = Query(default=None, ge=0),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    selected_view = view or await _operational_view(db, user_id)
    rows = (await db.execute(text("""
        SELECT id, computed_at, symbol, view, score, state, ev_shrunk,
               n_trades, n_excluded_unreplayable, w, atr_bucket,
               config_version, l3_config_version
          FROM crypto_ev_current
         WHERE view = :view
           AND (:state IS NULL OR state = :state)
           AND (:min_n IS NULL OR n_trades >= :min_n)
         ORDER BY score DESC, n_trades DESC, symbol
    """), {"view": selected_view, "state": state, "min_n": min_n})).mappings().all()
    return {
        "view": selected_view,
        "items": [_jsonable(dict(row)) for row in rows],
    }


@router.get("/{symbol}")
async def get_crypto_ev_symbol(
    symbol: str,
    view: Optional[str] = Query(default=None, pattern="^(executable|spectrum)$"),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    selected_view = view or await _operational_view(db, user_id)
    row = (await db.execute(text("""
        SELECT *
          FROM crypto_ev_current
         WHERE symbol = :symbol
           AND view = :view
    """), {"symbol": symbol.upper(), "view": selected_view})).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="crypto_ev_snapshot_not_found")
    return _jsonable(dict(row))


@router.get("/{symbol}/history")
async def get_crypto_ev_history(
    symbol: str,
    hours: int = Query(default=168, ge=1, le=2160),
    view: Optional[str] = Query(default=None, pattern="^(executable|spectrum)$"),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    selected_view = view or await _operational_view(db, user_id)
    rows = (await db.execute(text("""
        SELECT id, computed_at, symbol, view, score, state, ev_shrunk,
               n_trades, n_excluded_unreplayable, w, atr_bucket
          FROM crypto_ev_snapshots
         WHERE symbol = :symbol
           AND view = :view
           AND computed_at >= now() - (CAST(:hours AS text) || ' hours')::interval
         ORDER BY computed_at ASC, id ASC
    """), {"symbol": symbol.upper(), "view": selected_view, "hours": hours})).mappings().all()
    return {"symbol": symbol.upper(), "view": selected_view, "items": [_jsonable(dict(row)) for row in rows]}


@router.get("/audit/{snapshot_id}")
async def get_crypto_ev_audit(
    snapshot_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    del user_id
    row = (await db.execute(text("""
        SELECT *
          FROM crypto_ev_snapshots
         WHERE id = :snapshot_id
         ORDER BY computed_at DESC
         LIMIT 1
    """), {"snapshot_id": snapshot_id})).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="crypto_ev_snapshot_not_found")
    return _jsonable(dict(row))
