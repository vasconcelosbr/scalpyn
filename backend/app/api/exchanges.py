from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Dict, Any
from uuid import UUID

from ..database import get_db
from ..models.exchange_connection import ExchangeConnection
from .config import get_current_user_id
from ..utils.encryption import encrypt

router = APIRouter(prefix="/api/exchanges", tags=["Exchanges"])

@router.get("/")
async def get_exchanges(db: AsyncSession = Depends(get_db), user_id: UUID = Depends(get_current_user_id)):
    query = select(ExchangeConnection).where(ExchangeConnection.user_id == user_id)
    result = await db.execute(query)
    exchanges = result.scalars().all()
    
    connections = []
    for e in exchanges:
        connections.append({
            "id": str(e.id), 
            "exchange_name": e.exchange_name, 
            "is_active": e.is_active, 
            "status": e.connection_status,
            "lastSync": e.last_connected_at.isoformat() if e.last_connected_at else "Not connected"
        })
        
    return {"status": "success", "exchanges": connections}

@router.post("/connect")
async def add_exchange(payload: Dict[str, Any], db: AsyncSession = Depends(get_db), user_id: UUID = Depends(get_current_user_id)):
    exchange_name = payload.get("exchange_name")
    api_key = payload.get("api_key", "")
    api_secret = payload.get("api_secret", "")
    environment = payload.get("environment", "live")
    
    if not exchange_name or not api_key or not api_secret:
        raise HTTPException(status_code=400, detail="Missing required parameters")

    try:
        # Check if connection already exists
        query = select(ExchangeConnection).where(
            ExchangeConnection.user_id == user_id, 
            ExchangeConnection.exchange_name == exchange_name
        )
        result = await db.execute(query)
        existing_conn = result.scalars().first()
        
        if existing_conn:
            existing_conn.api_key_encrypted = encrypt(api_key)
            existing_conn.api_secret_encrypted = encrypt(api_secret)
            existing_conn.connection_status = "connected"
            existing_conn.is_active = True
        else:
            new_conn = ExchangeConnection(
                user_id=user_id,
                exchange_name=exchange_name,
                api_key_encrypted=encrypt(api_key),
                api_secret_encrypted=encrypt(api_secret),
                connection_status="connected",
                is_active=True
            )
            db.add(new_conn)
            
        await db.commit()
        return {"status": "success", "message": f"{exchange_name} connected successfully."}
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to save connection: {str(e)}")
