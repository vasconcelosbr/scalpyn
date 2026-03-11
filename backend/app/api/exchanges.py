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
            existing_conn.api_key_encrypted = encrypt(api_key.strip())
            existing_conn.api_secret_encrypted = encrypt(api_secret.strip())
            existing_conn.connection_status = "connected"
            existing_conn.is_active = True
        else:
            new_conn = ExchangeConnection(
                user_id=user_id,
                exchange_name=exchange_name,
                api_key_encrypted=encrypt(api_key.strip()),
                api_secret_encrypted=encrypt(api_secret.strip()),
                connection_status="connected",
                is_active=True
            )
            db.add(new_conn)
            
        await db.commit()
        return {"status": "success", "message": f"{exchange_name} connected successfully."}
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to save connection: {str(e)}")

@router.get("/{exchange_id}/test")
async def test_exchange_connection(exchange_id: str, db: AsyncSession = Depends(get_db), user_id: UUID = Depends(get_current_user_id)):
    query = select(ExchangeConnection).where(
        ExchangeConnection.id == exchange_id,
        ExchangeConnection.user_id == user_id
    )
    result = await db.execute(query)
    conn = result.scalars().first()
    
    if not conn:
        raise HTTPException(status_code=404, detail="Exchange connection not found")
        
    try:
        from ..utils.encryption import decrypt
        
        # Only Gate.io logic implemented for now as requested
        if conn.exchange_name.lower() == "gate.io":
            import time
            import hashlib
            import hmac
            import httpx
            
            api_key = decrypt(conn.api_key_encrypted).strip()
            api_secret = decrypt(conn.api_secret_encrypted).strip()
            
            host = "api.gateio.ws"
            prefix = "/api/v4"
            url = "/spot/accounts"
            query_param = ""
            
            # Formating the V4 signature string
            # Method \n URL \n Query String \n Hexlified Payload Hash \n Timestamp
            t = str(int(time.time()))
            m = hashlib.sha512()
            m.update(b"") # Empty body for GET requests
            hashed_payload = m.hexdigest()
            
            sign_string = f"GET\n{prefix}{url}\n{query_param}\n{hashed_payload}\n{t}"
            sign = hmac.new(api_secret.encode('utf-8'), sign_string.encode('utf-8'), hashlib.sha512).hexdigest()
            
            headers = {
                'Accept': 'application/json',
                'Content-Type': 'application/json',
                'KEY': api_key,
                'Timestamp': t,
                'SIGN': sign
            }
            
            async with httpx.AsyncClient() as client:
                r = await client.get(f"https://{host}{prefix}{url}", headers=headers)
                
                if r.status_code != 200:
                    raise HTTPException(status_code=r.status_code, detail=f"Gate.io API Error: {r.text}")
                    
                accounts = r.json()
            
            balances = []
            for acc in accounts:
                if float(acc.get("available", 0)) > 0 or float(acc.get("locked", 0)) > 0:
                    balances.append({
                        "currency": acc.get("currency"),
                        "available": acc.get("available"),
                        "locked": acc.get("locked")
                    })
            
            return {"status": "success", "exchange": conn.exchange_name, "balances": balances}
            
        return {"status": "success", "message": f"Test not implemented for {conn.exchange_name} yet, but connection exists."}
        
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to connect to Exchange API: {str(e)}")

@router.delete("/{exchange_id}")
async def delete_exchange(exchange_id: str, db: AsyncSession = Depends(get_db), user_id: UUID = Depends(get_current_user_id)):
    query = select(ExchangeConnection).where(
        ExchangeConnection.id == exchange_id,
        ExchangeConnection.user_id == user_id
    )
    result = await db.execute(query)
    conn = result.scalars().first()
    
    if not conn:
        raise HTTPException(status_code=404, detail="Exchange connection not found")
        
    try:
        await db.delete(conn)
        await db.commit()
        return {"status": "success", "message": "Connection deleted successfully"}
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to delete connection: {str(e)}")

