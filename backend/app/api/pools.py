from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Dict, Any
from uuid import UUID

from ..database import get_db
from ..models.pool import Pool, PoolCoin
from .config import get_current_user_id

router = APIRouter(prefix="/api/pools", tags=["Pools"])


def _pool_to_dict(pool: Pool) -> Dict[str, Any]:
    return {
        "id": str(pool.id),
        "name": pool.name,
        "description": pool.description,
        "is_active": pool.is_active,
        "mode": pool.mode,
        "overrides": pool.overrides if pool.overrides else {},
        "created_at": pool.created_at.isoformat() if pool.created_at else None,
        "updated_at": pool.updated_at.isoformat() if pool.updated_at else None,
    }


def _coin_to_dict(coin: PoolCoin) -> Dict[str, Any]:
    return {
        "id": str(coin.id),
        "pool_id": str(coin.pool_id),
        "symbol": coin.symbol,
        "market_type": coin.market_type,
        "is_active": coin.is_active,
        "added_at": coin.added_at.isoformat() if coin.added_at else None,
    }


@router.get("/")
async def get_pools(db: AsyncSession = Depends(get_db), user_id: UUID = Depends(get_current_user_id)):
    query = select(Pool).where(Pool.user_id == user_id)
    result = await db.execute(query)
    pools = result.scalars().all()
    return {"pools": [_pool_to_dict(p) for p in pools]}


@router.post("/")
async def create_pool(payload: Dict[str, Any], db: AsyncSession = Depends(get_db), user_id: UUID = Depends(get_current_user_id)):
    pool = Pool(
        user_id=user_id,
        name=payload.get("name"),
        description=payload.get("description", ""),
        is_active=payload.get("is_active", True),
        mode=payload.get("mode", "paper"),
    )
    db.add(pool)
    await db.commit()
    await db.refresh(pool)
    return _pool_to_dict(pool)


@router.patch("/{pool_id}")
async def update_pool(pool_id: UUID, payload: Dict[str, Any], db: AsyncSession = Depends(get_db), user_id: UUID = Depends(get_current_user_id)):
    query = select(Pool).where(Pool.id == pool_id, Pool.user_id == user_id)
    result = await db.execute(query)
    pool = result.scalars().first()
    if not pool:
        raise HTTPException(status_code=404, detail="Pool not found")

    if "name" in payload:
        pool.name = payload["name"]
    if "description" in payload:
        pool.description = payload["description"]
    if "is_active" in payload:
        pool.is_active = payload["is_active"]
    if "mode" in payload:
        pool.mode = payload["mode"]
    if "overrides" in payload:
        pool.overrides = payload["overrides"]

    await db.commit()
    await db.refresh(pool)
    return _pool_to_dict(pool)


@router.get("/{pool_id}/coins")
async def get_pool_coins(pool_id: UUID, db: AsyncSession = Depends(get_db), user_id: UUID = Depends(get_current_user_id)):
    pool_query = select(Pool).where(Pool.id == pool_id, Pool.user_id == user_id)
    pool_result = await db.execute(pool_query)
    if not pool_result.scalars().first():
        raise HTTPException(status_code=404, detail="Pool not found")

    query = select(PoolCoin).where(PoolCoin.pool_id == pool_id)
    result = await db.execute(query)
    coins = result.scalars().all()
    return {"coins": [_coin_to_dict(c) for c in coins]}


@router.post("/{pool_id}/coins")
async def add_pool_coin(pool_id: UUID, payload: Dict[str, Any], db: AsyncSession = Depends(get_db), user_id: UUID = Depends(get_current_user_id)):
    pool_query = select(Pool).where(Pool.id == pool_id, Pool.user_id == user_id)
    pool_result = await db.execute(pool_query)
    if not pool_result.scalars().first():
        raise HTTPException(status_code=404, detail="Pool not found")

    symbol = payload.get("symbol", "").upper().strip()
    market_type = payload.get("market_type", "spot").lower()

    if not symbol:
        raise HTTPException(status_code=400, detail="Symbol is required")

    existing_query = select(PoolCoin).where(PoolCoin.pool_id == pool_id, PoolCoin.symbol == symbol)
    existing_result = await db.execute(existing_query)
    if existing_result.scalars().first():
        raise HTTPException(status_code=409, detail="Symbol already in pool")

    coin = PoolCoin(pool_id=pool_id, symbol=symbol, market_type=market_type, is_active=True)
    db.add(coin)
    await db.commit()
    await db.refresh(coin)
    return _coin_to_dict(coin)


@router.delete("/{pool_id}/coins/{symbol}")
async def remove_pool_coin(pool_id: UUID, symbol: str, db: AsyncSession = Depends(get_db), user_id: UUID = Depends(get_current_user_id)):
    pool_query = select(Pool).where(Pool.id == pool_id, Pool.user_id == user_id)
    pool_result = await db.execute(pool_query)
    if not pool_result.scalars().first():
        raise HTTPException(status_code=404, detail="Pool not found")

    coin_query = select(PoolCoin).where(PoolCoin.pool_id == pool_id, PoolCoin.symbol == symbol.upper())
    coin_result = await db.execute(coin_query)
    coin = coin_result.scalars().first()
    if not coin:
        raise HTTPException(status_code=404, detail="Symbol not found in pool")

    await db.delete(coin)
    await db.commit()
    return {"status": "success", "message": f"{symbol} removed from pool"}


@router.get("/{pool_id}/overrides")
async def get_pool_overrides(pool_id: UUID, db: AsyncSession = Depends(get_db), user_id: UUID = Depends(get_current_user_id)):
    pool_query = select(Pool).where(Pool.id == pool_id, Pool.user_id == user_id)
    pool_result = await db.execute(pool_query)
    pool = pool_result.scalars().first()
    if not pool:
        raise HTTPException(status_code=404, detail="Pool not found")

    overrides = pool.overrides if pool.overrides else {}
    return {"pool_id": str(pool_id), "overrides": overrides}


@router.put("/{pool_id}/overrides")
async def update_pool_overrides(pool_id: UUID, payload: Dict[str, Any], db: AsyncSession = Depends(get_db), user_id: UUID = Depends(get_current_user_id)):
    pool_query = select(Pool).where(Pool.id == pool_id, Pool.user_id == user_id)
    pool_result = await db.execute(pool_query)
    pool = pool_result.scalars().first()
    if not pool:
        raise HTTPException(status_code=404, detail="Pool not found")

    pool.overrides = payload
    await db.commit()
    await db.refresh(pool)
    return {"pool_id": str(pool_id), "overrides": pool.overrides}
