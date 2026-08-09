from __future__ import annotations

import os
import secrets
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai_orchestration.langgraph.config import get_langgraph_settings
from ..database import get_db
from ..models.ai_graph import AIGraphDefinition, AIGraphRun
from ..models.systemic_ai import (
    AIConfigurationBundleRecord, AIDatasetSnapshotRecord, AIModelResolutionRecord,
    AIPromptVersion, AIRequestRecord, AIResultRecord, AIToolEvidenceRecord,
    AIUsageRecord,
)
from ..services.ai_graph_service import AIGraphRunService, GraphAccessError
from .config import get_current_user_id


router = APIRouter(prefix="/api/ai/graphs", tags=["AI Graphs"])


class CreateGraphRunRequest(BaseModel):
    graph_key: Literal[
        "systemic-analysis-v1", "root-cause-audit-v1",
        "regenerative-shadow-v1", "copilot-systemic-v1",
        "systemic-analysis-v2", "root-cause-audit-v2",
        "regenerative-shadow-v2", "copilot-systemic-v2",
    ]
    ai_request_id: UUID
    idempotency_key: str = Field(min_length=16, max_length=160)


class ResumeGraphRunRequest(BaseModel):
    interrupt_id: UUID
    decision: Literal["approve", "reject", "edit"]
    decision_id: UUID
    idempotency_key: str = Field(min_length=16, max_length=160)
    edits: dict[str, Any] = Field(default_factory=dict)


class StagingCrashResumeRequest(BaseModel):
    action: Literal["seed-start", "snapshot", "resume"]
    run_id: UUID | None = None


def _run_payload(run, request=None, definition=None) -> dict[str, Any]:
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
        "origin_module": request.origin_module if request else None,
        "origin_view": request.origin_view if request else None,
        "graph_key": definition.graph_key if definition else None,
        "graph_version": definition.semantic_version if definition else None,
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
    if payload.graph_key in {"regenerative-shadow-v1", "regenerative-shadow-v2"} and not settings.regenerative_shadow_enabled:
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
        select(AIGraphRun, AIRequestRecord, AIGraphDefinition)
        .join(AIRequestRecord, AIRequestRecord.id == AIGraphRun.ai_request_id)
        .join(AIGraphDefinition, AIGraphDefinition.id == AIGraphRun.graph_definition_id)
        .where(AIGraphRun.tenant_id == user_id)
        .order_by(AIGraphRun.created_at.desc()).offset(offset).limit(limit)
    )).all())
    return {
        "items": [_run_payload(run, request, definition) for run, request, definition in rows],
        "limit": limit, "offset": offset,
    }


@router.get("/runs/{run_id}")
async def get_graph_run(
    run_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    try:
        run = await AIGraphRunService.get(db, tenant_id=user_id, run_id=run_id)
        request = await db.get(AIRequestRecord, run.ai_request_id)
        definition = await db.get(AIGraphDefinition, run.graph_definition_id)
        return _run_payload(run, request, definition)
    except GraphAccessError as exc:
        raise _error(exc) from exc


@router.get("/runs/{run_id}/context")
async def get_graph_context(
    run_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    try:
        run = await AIGraphRunService.get(db, tenant_id=user_id, run_id=run_id)
    except GraphAccessError as exc:
        raise _error(exc) from exc
    request = await db.get(AIRequestRecord, run.ai_request_id)
    if request is None or request.tenant_id != user_id:
        raise HTTPException(status_code=404, detail={"code": "AI_REQUEST_NOT_FOUND"})
    definition = await db.get(AIGraphDefinition, run.graph_definition_id)
    resolution = await db.get(AIModelResolutionRecord, request.model_resolution_id)
    prompt = await db.get(AIPromptVersion, request.prompt_version_id)
    dataset = await db.get(AIDatasetSnapshotRecord, request.dataset_snapshot_id)
    bundle = await db.get(AIConfigurationBundleRecord, request.configuration_bundle_id)
    result = (await db.execute(select(AIResultRecord).where(
        AIResultRecord.tenant_id == user_id, AIResultRecord.ai_request_id == request.id,
    ))).scalar_one_or_none()
    usage = (await db.execute(select(AIUsageRecord).where(
        AIUsageRecord.tenant_id == user_id, AIUsageRecord.ai_request_id == request.id,
    ).order_by(AIUsageRecord.created_at.desc()))).scalars().first()
    tool_evidence = list((await db.execute(select(AIToolEvidenceRecord).where(
        AIToolEvidenceRecord.tenant_id == user_id,
        AIToolEvidenceRecord.ai_request_id == request.id,
    ).order_by(AIToolEvidenceRecord.created_at, AIToolEvidenceRecord.id))).scalars())
    return {
        "run": _run_payload(run, request, definition),
        "model": {
            "configured_provider": resolution.configured_provider if resolution else None,
            "configured_model": resolution.configured_model if resolution else None,
            "effective_provider": resolution.effective_provider if resolution else None,
            "effective_model": resolution.effective_model if resolution else None,
            "resolution_reason": resolution.resolution_reason if resolution else None,
        },
        "prompt": {
            "key": prompt.prompt_key if prompt else None,
            "version": prompt.semantic_version if prompt else None,
            "hash": prompt.content_hash if prompt else None,
        },
        "dataset": {
            "id": str(dataset.id) if dataset else None,
            "hash": dataset.dataset_hash if dataset else None,
            "contract_version": dataset.contract_version if dataset else None,
            "quality_status": dataset.quality_status if dataset else None,
            "row_count": dataset.row_count if dataset else None,
            "module_context_refs": dataset.module_context_refs if dataset else None,
            "context_manifest": dataset.context_manifest if dataset else None,
        },
        "bundle": {
            "id": str(bundle.id) if bundle else None,
            "hash": bundle.bundle_hash if bundle else None,
            "lineage_status": bundle.lineage_status if bundle else None,
            "lineage_refs": bundle.lineage_refs if bundle else None,
        },
        "result": {
            "status": result.status if result else None,
            "warnings": (result.result_json or {}).get("warnings", []) if result else [],
            "limitations": (result.result_json or {}).get("limitations", []) if result else [],
            "memory_hits": (result.result_json or {}).get("memory_hits", []) if result else [],
        },
        "usage": {
            "tokens_input": usage.tokens_input if usage else None,
            "tokens_output": usage.tokens_output if usage else None,
            "actual_cost": str(usage.actual_cost) if usage else None,
            "currency": usage.currency if usage else None,
            "pricing_snapshot_version": usage.pricing_snapshot_version if usage else None,
        },
        "tool_evidence": [{
            "id": str(item.id),
            "tool_call_audit_id": str(item.tool_call_audit_id),
            "module_key": item.module_key,
            "tool_name": item.tool_name,
            "output_hash": item.output_hash,
            "freshness": item.freshness_json,
            "quality": item.quality,
            "created_at": item.created_at,
        } for item in tool_evidence],
    }


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
        "module_flags": dict(settings.module_flags),
    }


@router.post("/staging-canary")
async def run_staging_canary(
    authorization: str | None = Header(default=None),
):
    """Run the zero-cost v2 canary only in the isolated staging environment."""
    expected = os.getenv("DIAGNOSTICS_BEARER_TOKEN", "").strip()
    if not expected:
        raise HTTPException(status_code=404, detail="Not Found")
    prefix = "Bearer "
    supplied = authorization[len(prefix):] if authorization and authorization.startswith(prefix) else ""
    if not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Bearer"},
        )
    from ..ai_orchestration.langgraph.staging_canary import run_canaries

    try:
        return await run_canaries()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail={"code": str(exc)}) from exc


@router.post("/staging-crash-resume")
async def run_staging_crash_resume(
    payload: StagingCrashResumeRequest,
    authorization: str | None = Header(default=None),
):
    """Drive a fake-provider crash/resume proof through the dedicated worker."""
    expected = os.getenv("DIAGNOSTICS_BEARER_TOKEN", "").strip()
    prefix = "Bearer "
    supplied = authorization[len(prefix):] if authorization and authorization.startswith(prefix) else ""
    if not expected or not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=401 if expected else 404,
            detail="Unauthorized" if expected else "Not Found",
            headers={"WWW-Authenticate": "Bearer"} if expected else None,
        )
    from ..ai_orchestration.langgraph.staging_canary import _assert_staging
    from scripts.final_langgraph_runtime_driver import (
        _resume_pending,
        _seed_start,
        _snapshot,
    )

    try:
        _assert_staging()
        if payload.action == "seed-start":
            return await _seed_start()
        if payload.run_id is None:
            raise RuntimeError("GRAPH_RUN_ID_REQUIRED")
        if payload.action == "snapshot":
            return await _snapshot(payload.run_id)
        return await _resume_pending(payload.run_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail={"code": str(exc)}) from exc
