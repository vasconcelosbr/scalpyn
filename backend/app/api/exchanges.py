from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Dict, Any
from uuid import UUID

from ..database import get_db
from ..models.exchange_connection import ExchangeConnection
from .config import get_current_user_id

router = APIRouter(prefix="/api/exchanges", tags=["Exchanges"])

@router.get("/")
async def get_exchanges(db: AsyncSession = Depends(get_db), user_id: UUID = Depends(get_current_user_id)):
    query = select(ExchangeConnection).where(ExchangeConnection.user_id == user_id)
    result = await db.execute(query)
    exchanges = result.scalars().all()
    # In a real app we'd omit the encrypted keys from the response
    return {"exchanges": [{"id": e.id, "exchange_name": e.exchange_name, "is_active": e.is_active, "status": e.connection_status} for e in exchanges]}

@router.post("/")
async def add_exchange(payload: Dict[str, Any], db: AsyncSession = Depends(get_db), user_id: UUID = Depends(get_current_user_id)):
    # We would encrypt API keys properly here
    conn = ExchangeConnection(
        user_id=user_id,
        exchange_name=payload.get("exchange_name"),
        api_key_encrypted=payload.get("api_key", "").encode(),
        api_secret_encrypted=payload.get("api_secret", "").encode()
    )
    db.add(conn)
    await db.commit()
    return {"status": "success", "message": "Exchange added successfully."}
