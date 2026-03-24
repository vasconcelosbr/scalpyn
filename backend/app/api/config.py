from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Dict, Any, Optional
from uuid import UUID

import jwt as pyjwt

from ..config import settings
from ..database import get_db
from ..services.config_service import config_service

security = HTTPBearer()

async def get_current_user_id(credentials: HTTPAuthorizationCredentials = Depends(security)) -> UUID:
    token = credentials.credentials
    try:
        payload = pyjwt.decode(token, settings.JWT_SECRET, algorithms=["HS256"])
        if payload.get("type") != "access":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")
        return UUID(payload["sub"])
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

router = APIRouter(prefix="/api/config", tags=["Configuration"])

@router.get("/{config_type}")
async def get_config(
    config_type: str,
    pool_id: Optional[UUID] = None,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id)
):
    config = await config_service.get_config(db, config_type, user_id, pool_id)
    return {"config_type": config_type, "pool_id": pool_id, "data": config}

@router.put("/{config_type}")
async def update_config(
    config_type: str,
    payload: Dict[str, Any],
    pool_id: Optional[UUID] = None,
    change_description: str = "Updated via API",
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id)
):
    updated = await config_service.update_config(
        db=db,
        config_type=config_type,
        user_id=user_id,
        new_json=payload,
        changed_by=user_id,
        pool_id=pool_id,
        change_description=change_description
    )
    return {"status": "success", "config_type": config_type, "data": updated}
