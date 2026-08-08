from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai_orchestration.langgraph.config import get_langgraph_settings
from ..database import get_db
from ..models.ai_graph import AIGraphDefinition, AIGraphRun
from ..services.ai_graph_service import AIGraphRunService, GraphAccessError
from .config import get_current_user_id


router = APIRouter(prefix="/api/ai/graphs", tags=["AI Graphs"])


class CreateGraphRunRequest(BaseModel):
    graph_key: Literal[
        "systemic-analysis-v1", "root-cause-audit-v1",
        "regenerative-shadow-v1", "copilot-systemic-v1",
    ]
    ai_request_id: UUID
    idempotency_key: str = Field(min_length=16, max_length=160)


class ResumeGraphRunRequest(BaseModel):
    interrupt_id: UUID
    decision: Literal["approve", "reject", "edit"]
    decision_id: UUID
    idempotency_key: str = Field(min_length=16, max_length=160)
    edits: dict[str, Any] = Field(default_factory=dict)


def _run_payload(run) -> dict[str, Any]:
    return {
        "id": str(run.id),
        "ai_request_id": str(run.ai_request_id),
        "graph_definition_id": str(run.graph_definition_id),
        "status": run.status,
        "current_node": run.current_node,
        "authority": run.authority,
        "state_schema_version": run.state_schema_version,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "terminal_reason": run.terminal_reason,
        "last_error_code": run.last_error_code,
        "last_error_safe_message": run.last_error_safe_message,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
    }


def _error(exc: GraphAccessError) -> HTTPException:
    code = str(exc)
    status = 404 if code.endswith("NOT_FOUND") else 409
    if code in {"GRAPH_INTERRUPT_EDIT_FORBIDDEN"}:
        status = 403
    return HTTPException(status_code=status, detail={"code": code})


@router.post("/runs", status_code=202)
async def create_graph_run(
    payload: CreateGraphRunRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    settings = get_langgraph_settings()
    if not settings.runtime_enabled or not settings.entrypoints_enabled:
        raise HTTPException(status_code=409, detail={"code": "LANGGRAPH_ENTRYPOINTS_DISABLED"})
    if payload.graph_key == "regenerative-shadow-v1" and not settings.regenerative_shadow_enabled:
        raise HTTPException(status_code=409, detail={"code": "LANGGRAPH_REGENERATIVE_SHADOW_DISABLED"})
    try:
        run = await AIGraphRunService.create(
            db, tenant_id=user_id, user_id=user_id, graph_key=payload.graph_key,
            ai_request_id=payload.ai_request_id, idempotency_key=payload.idempotency_key,
        )
        await db.commit()
        await db.refresh(run)
    except GraphAccessError as exc:
        raise _error(exc) from exc
    from ..tasks.ai_orchestration import start_graph_run
    start_graph_run.apply_async(args=[str(run.id)], queue="ai_orchestration")
    return _run_payload(run)


@router.get("/runs")
async def list_graph_runs(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    rows = list((await db.execute(
        select(AIGraphRun).where(AIGraphRun.tenant_id == user_id)
        .order_by(AIGraphRun.created_at.desc()).offset(offset).limit(limit)
    )).scalars().all())
    return {"items": [_run_payload(row) for row in rows], "limit": limit, "offset": offset}


@router.get("/runs/{run_id}")
async def get_graph_run(
    run_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    try:
        return _run_payload(await AIGraphRunService.get(db, tenant_id=user_id, run_id=run_id))
    except GraphAccessError as exc:
        raise _error(exc) from exc


@router.get("/runs/{run_id}/timeline")
async def get_graph_timeline(
    run_id: UUID,
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    try:
        events = await AIGraphRunService.timeline(
            db, tenant_id=user_id, run_id=run_id, limit=limit, offset=offset,
        )
    except GraphAccessError as exc:
        raise _error(exc) from exc
    return {"items": [{
        "id": event.id, "event_type": event.event_type, "node_name": event.node_name,
        "status": event.status, "payload": event.payload, "created_at": event.created_at,
    } for event in events], "limit": limit, "offset": offset}


@router.get("/runs/{run_id}/interrupts")
async def get_graph_interrupts(
    run_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    try:
        records = await AIGraphRunService.interrupts(db, tenant_id=user_id, run_id=run_id)
    except GraphAccessError as exc:
        raise _error(exc) from exc
    return {"items": [{
        "id": str(item.id), "interrupt_type": item.interrupt_type, "status": item.status,
        "payload": item.payload, "allowed_edit_fields": item.allowed_edit_fields,
        "decision": item.decision, "created_at": item.created_at, "resolved_at": item.resolved_at,
    } for item in records]}


@router.post("/runs/{run_id}/resume", status_code=202)
async def resume_graph_run(
    run_id: UUID,
    payload: ResumeGraphRunRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    try:
        run = await AIGraphRunService.resume(
            db, tenant_id=user_id, actor_user_id=user_id, run_id=run_id,
            interrupt_id=payload.interrupt_id, decision=payload.decision,
            decision_id=payload.decision_id, idempotency_key=payload.idempotency_key,
            edits=payload.edits,
        )
        await db.commit()
        await db.refresh(run)
    except GraphAccessError as exc:
        raise _error(exc) from exc
    from ..tasks.ai_orchestration import resume_graph_run as resume_task
    resume_task.apply_async(args=[str(run.id), str(payload.interrupt_id)], queue="ai_orchestration")
    return _run_payload(run)


@router.post("/runs/{run_id}/cancel")
async def cancel_graph_run(
    run_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    try:
        run = await AIGraphRunService.cancel(db, tenant_id=user_id, run_id=run_id, actor_user_id=user_id)
        await db.commit()
        await db.refresh(run)
    except GraphAccessError as exc:
        raise _error(exc) from exc
    from ..tasks.ai_orchestration import cancel_graph_run as cancel_task
    cancel_task.apply_async(args=[str(run.id)], queue="ai_orchestration")
    return _run_payload(run)


@router.get("/definitions")
async def list_graph_definitions(
    db: AsyncSession = Depends(get_db),
    _user_id: UUID = Depends(get_current_user_id),
):
    rows = list((await db.execute(
        select(AIGraphDefinition).where(AIGraphDefinition.status == "APPROVED").order_by(AIGraphDefinition.graph_key)
    )).scalars().all())
    return {"items": [{
        "graph_key": row.graph_key, "semantic_version": row.semantic_version,
        "state_schema_version": row.state_schema_version, "status": row.status,
        "content_hash": row.content_hash, "node_manifest": row.node_manifest,
        "edge_manifest": row.edge_manifest, "tool_policy_version": row.tool_policy_version,
    } for row in rows]}


@router.get("/capabilities")
async def graph_capabilities(_user_id: UUID = Depends(get_current_user_id)):
    settings = get_langgraph_settings()
    return {
        "runtime": settings.runtime,
        "runtime_enabled": settings.runtime_enabled,
        "entrypoints_enabled": settings.entrypoints_enabled,
        "regenerative_shadow_enabled": settings.regenerative_shadow_enabled,
        "real_provider_canary_enabled": settings.real_provider_canary_enabled,
        "strict_msgpack": settings.strict_msgpack,
        "authorities": ["ANALYSIS_ONLY", "PROPOSAL_ONLY", "CANDIDATE_ONLY", "SHADOW_ONLY"],
        "live_write": False,
    }
