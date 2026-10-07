"""Pump Monitor API — live observation of the monitored pool. Not an entry signal."""
from typing import Any, Dict, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..services import pump_monitor_service as svc
from .config import get_current_user_id

router = APIRouter(prefix="/api/pump-monitor", tags=["Pump Monitor"])

NOT_A_SIGNAL = "OBSERVAÇÃO - não é sinal de entrada"


@router.get("/opportunities/config")
async def read_opportunity_config(db: AsyncSession = Depends(get_db), user_id: UUID = Depends(get_current_user_id)):
    from ..services import pump_opportunity_service as opportunities
    return await opportunities.get_config(db,user_id)


@router.put("/opportunities/config")
async def write_opportunity_config(payload: Dict[str,Any], db: AsyncSession = Depends(get_db), user_id: UUID = Depends(get_current_user_id)):
    from ..services import pump_opportunity_service as opportunities
    try:
        return await opportunities.put_config(db,user_id,payload)
    except (ValueError,TypeError,KeyError) as exc:
        raise HTTPException(status_code=422,detail=str(exc))


@router.get("/opportunities")
async def list_opportunities(response: Response, db: AsyncSession = Depends(get_db), user_id: UUID = Depends(get_current_user_id)):
    from ..services import pump_opportunity_service as opportunities
    response.headers["Cache-Control"]="private, no-store"
    return await opportunities.latest(db,user_id)


@router.get("/opportunities/history")
async def opportunity_history(cursor: Optional[str] = Query(None,max_length=36),limit: int = Query(50,ge=1,le=100),
                              db: AsyncSession = Depends(get_db),user_id: UUID = Depends(get_current_user_id)):
    from ..services import pump_opportunity_service as opportunities
    return await opportunities.history(db,user_id,cursor,limit)


@router.get("/opportunities/intelligence")
async def opportunity_intelligence(response: Response,horizon: int = Query(5,ge=1,le=120),refresh: bool = Query(False),db: AsyncSession = Depends(get_db),user_id: UUID = Depends(get_current_user_id)):
    from ..services import pump_opportunity_service as opportunities
    response.headers["Cache-Control"]="private, no-store"
    try:
        return await opportunities.intelligence(db,user_id,horizon=horizon,refresh=refresh)
    except ValueError as exc:
        raise HTTPException(status_code=422,detail=str(exc))


@router.post("/opportunities/explore")
async def explore_opportunity_pattern(payload: Dict[str,Any],response: Response,db: AsyncSession = Depends(get_db),user_id: UUID = Depends(get_current_user_id)):
    from ..services import pump_opportunity_service as opportunities
    conditions=payload.get("conditions",[])
    if not isinstance(conditions,list) or not 1<=len(conditions)<=20:
        raise HTTPException(status_code=422,detail="Expected 1-20 AND conditions")
    try:
        response.headers["Cache-Control"]="private, no-store"
        horizon=payload.get("horizon_minutes",5)
        if not isinstance(horizon,int) or isinstance(horizon,bool):raise ValueError("Integer horizon required")
        refresh=payload.get('refresh',False)
        if not isinstance(refresh,bool):raise ValueError('Boolean refresh required')
        return await opportunities.intelligence(db,user_id,conditions,horizon,refresh)
    except (ValueError,TypeError,KeyError) as exc:
        raise HTTPException(status_code=422,detail=str(exc))


@router.post("/ml/train", status_code=202)
async def trigger_ml_training(user_id: UUID = Depends(get_current_user_id)):
    """Manual Pump ML training for the caller only. Bypasses the one-run-per-day
    rule but keeps the singleton lock and a 60-minute minimum interval."""
    from ..tasks.pump_monitor import train_ml_daily
    result = train_ml_daily.apply_async(kwargs={"owner": str(user_id), "force": True})
    return {"status": "queued", "task_id": result.id, "owner": str(user_id),
            "note": "Resultado em GET /api/pump-monitor/opportunities/intelligence (training_runs)."}


@router.get("/capital-flow/history")
async def capital_flow_history(response: Response,
                               days: int = Query(7, ge=1, le=30),
                               top: int = Query(5, ge=1, le=24),
                               tz_offset_minutes: int = Query(-180, ge=-720, le=840),
                               db: AsyncSession = Depends(get_db),
                               user_id: UUID = Depends(get_current_user_id)):
    """Hourly USDT capital tide of the monitored universe (Gate taker flow)."""
    response.headers["Cache-Control"] = "private, no-store"
    try:
        return await svc.capital_flow_history(db, user_id, days=days, top=top,
                                              tz_offset_minutes=tz_offset_minutes)
    except Exception as exc:  # table not migrated yet → explicit, never a 500 loop
        raise HTTPException(status_code=503, detail=f"capital_flow_history_unavailable:{type(exc).__name__}")


@router.get("/opportunities/{observation_id}")
async def opportunity_detail(observation_id: UUID,db: AsyncSession = Depends(get_db),user_id: UUID = Depends(get_current_user_id)):
    from ..services import pump_opportunity_service as opportunities
    result=await opportunities.observation(db,user_id,observation_id)
    if result is None:
        raise HTTPException(status_code=404,detail="Observation not found")
    return result


@router.get("/assets")
async def list_assets(
    pool_id: Optional[UUID] = Query(None),
    limit: Optional[int] = Query(None, ge=0, le=500, description="TOP-N; 0 = todos; vazio = config"),
    sort: str = Query("pump_monitor_score", max_length=64),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    only_rising: bool = Query(True),
    columns: str = Query("", max_length=2000, description="grupos ou indicadores separados por vírgula"),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
    response: Response = None,
):
    config = await svc.get_config(db, user_id)
    monitored = config.get("universe_pool_id")
    if pool_id is not None and str(pool_id) != str(monitored):
        raise HTTPException(status_code=409, detail="POOL_NOT_MONITORED: configure universe_pool_id to monitor this pool")
    if response is not None:
        response.headers["Cache-Control"] = "private, no-store"
    envelope = await svc.latest_envelope(db, user_id)
    base = {"notice": NOT_A_SIGNAL, "pool_id": str(monitored) if monitored else None,
            "config_version": config["_meta"]["version"], "config_hash": config["_meta"]["config_hash"],
            "cycle_seconds": config["cycle_seconds"], "score_status": config["score"]["status"]}
    if not envelope:
        return {**base, "generated_at": None, "total_assets": 0, "rows": [],
                "reason": "no_cycle_yet" if monitored else "universe_pool_not_configured"}
    top_n = config["display"]["top_n"] if limit is None else limit
    selected = svc.select_rows(
        envelope, limit=int(top_n), sort=sort, order=order, only_rising=only_rising,
        columns=[c.strip() for c in columns.split(",") if c.strip()] or None, config=config)
    return {**base, **selected, "limit": int(top_n), "sort": sort, "order": order,
            "only_rising": only_rising, "display": config["display"],
            "indicator_specs": config["indicators"]}


@router.get("/config")
async def read_config(db: AsyncSession = Depends(get_db), user_id: UUID = Depends(get_current_user_id)):
    return await svc.get_config(db, user_id)


@router.put("/config")
async def write_config(payload: Dict[str, Any], db: AsyncSession = Depends(get_db),
                       user_id: UUID = Depends(get_current_user_id)):
    pool_id = payload.get("universe_pool_id")
    if pool_id:
        from sqlalchemy import select
        from ..models.pool import Pool
        pool = (await db.scalars(select(Pool).where(Pool.id == UUID(str(pool_id)), Pool.user_id == user_id))).first()
        if pool is None:
            raise HTTPException(status_code=404, detail="Pool not found")
    try:
        return await svc.put_config(db, user_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
