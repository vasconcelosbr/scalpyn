from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Dict, Any
from uuid import UUID

from ..database import get_db
from ..models.pool import Pool, PoolCoin
from .config import get_current_user_id

router = APIRouter(prefix="/api/pools", tags=["Pools"])

@router.get("/")
async def get_pools(db: AsyncSession = Depends(get_db), user_id: UUID = Depends(get_current_user_id)):
    query = select(Pool).where(Pool.user_id == user_id)
    result = await db.execute(query)
    pools = result.scalars().all()
    return {"pools": pools}

@router.post("/")
async def create_pool(payload: Dict[str, Any], db: AsyncSession = Depends(get_db), user_id: UUID = Depends(get_current_user_id)):
    pool = Pool(
        user_id=user_id,
        name=payload.get("name"),
        description=payload.get("description", ""),
        is_active=payload.get("is_active", True),
        mode=payload.get("mode", "paper")
    )
    db.add(pool)
    await db.commit()
    await db.refresh(pool)
    return pool

@router.get("/{pool_id}/coins")
async def get_pool_coins(pool_id: UUID, db: AsyncSession = Depends(get_db), user_id: UUID = Depends(get_current_user_id)):
    # Add ownership verification here
    query = select(PoolCoin).where(PoolCoin.pool_id == pool_id)
    result = await db.execute(query)
    coins = result.scalars().all()
    return {"coins": coins}
