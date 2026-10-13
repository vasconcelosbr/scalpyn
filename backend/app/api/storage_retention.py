"""Scoped operational retention control; archives are never returned to the UI."""
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from .config import get_current_user_id
from ..database import get_db
from ..schemas.storage_retention import StorageRetentionConfig
from ..services.config_service import config_service

router = APIRouter(prefix="/api/storage-retention", tags=["Storage retention"])


async def require_operator(db, user_id):
    allowed = (await db.execute(text("SELECT EXISTS(SELECT 1 FROM storage_retention_owners WHERE user_id=:uid)"), {"uid": user_id})).scalar()
    if not allowed:
        raise HTTPException(status_code=403, detail="Storage retention operator authorization required")


@router.get("/status")
async def status(db: AsyncSession = Depends(get_db), user_id: UUID = Depends(get_current_user_id)):
    await require_operator(db, user_id)
    cfg = StorageRetentionConfig.model_validate(await config_service.get_config(db, "storage_retention", user_id))
    runs = (await db.execute(text("""SELECT run_id,started_at,finished_at,status,summary,config_json
        FROM storage_retention_runs WHERE user_id=:uid ORDER BY started_at DESC LIMIT 20"""), {"uid": user_id})).mappings().all()
    archive = (await db.execute(text("""SELECT b.table_name,sum(b.deleted_count) deleted_rows,
        sum(octet_length(b.archive_gzip)) archive_bytes FROM storage_retention_batches b
        JOIN storage_retention_runs r USING(run_id) WHERE r.user_id=:uid GROUP BY b.table_name"""), {"uid": user_id})).mappings().all()
    return {"config": cfg.model_dump(), "schema": cfg.model_json_schema(), "runs": [dict(r) for r in runs], "archives": [dict(r) for r in archive]}


@router.post("/preview")
async def preview(db: AsyncSession = Depends(get_db), user_id: UUID = Depends(get_current_user_id)):
    await require_operator(db, user_id)
    from ..tasks.storage_retention import run
    job = run.apply_async(kwargs={"owner": str(user_id), "force": True, "preview": True}, expires=120)
    return {"task_id": job.id, "status": "queued"}
