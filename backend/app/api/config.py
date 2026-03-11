from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Dict, Any, Optional
from uuid import UUID

from ..database import get_db
from ..services.config_service import config_service

# Assuming we have a get_current_user dependency, mocked for now
async def get_current_user_id() -> UUID:
    # This is a mocked standard user ID for scaffolding
    import uuid
    return uuid.UUID("00000000-0000-0000-0000-000000000001")

router = APIRouter(prefix="/api/config", tags=["Configuration"])

@router.get("/{config_type}")
async def get_config(
    config_type: str,
    pool_id: Optional[UUID] = None,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id)
):
    config = await config_service.get_config(db, config_type, user_id, pool_id)
    if not config:
        raise HTTPException(status_code=404, detail="Configuration not found or user inactive")
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
