from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db, run_db_task
from ..models.ai_graph import AIGraphEvent, AIGraphInterrupt, AIGraphRun
from ..models.analysis_chat import AIAnalysisConversation, AIAnalysisMessage
from ..models.copilot import CopilotActionPlan
from ..models.systemic_ai import AIBudgetReservationRecord, AIRequestRecord
from ..schemas.analysis_chat import AnalysisChatDataMode
from ..services.ai_graph_service import AIGraphRunService, GraphAccessError
from ..services.analysis_chat_service import AnalysisChatError, AnalysisChatService
from ..services.governed_change_service import (
    get_plan as get_governed_change_plan,
    plan_to_dict as governed_plan_to_dict,
    rollback as rollback_governed_change,
)
from .config import get_current_user_id


router = APIRouter(tags=["Analysis Chat"])


class CreateConversationRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class SendMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=12000)
    data_mode: AnalysisChatDataMode = AnalysisChatDataMode.FROZEN_ANALYSIS_ONLY
    idempotency_key: str = Field(min_length=16, max_length=160)
    response_language: str = Field(default="pt-BR", min_length=2, max_length=16)


class DecideMessageRequest(BaseModel):
    interrupt_id: UUID
    decision: Literal["approve", "reject", "edit"]
    decision_id: UUID
    idempotency_key: str = Field(min_length=16, max_length=160)
    edits: dict[str, Any] = Field(default_factory=dict)


class RollbackProposalRequest(BaseModel):
    confirmation_text: str = Field(min_length=1, max_length=80)


def _error(exc: AnalysisChatError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail={"code": exc.code})


def _conversation_payload(row: AIAnalysisConversation) -> dict[str, Any]:
    return {
        "conversation_id": str(row.id),
        "thread_id": row.thread_id,
        "parent_analysis_run_id": str(row.parent_analysis_run_id),
        "parent_result_id": str(row.parent_result_id),
        "title": row.title,
        "status": row.status,
        "running_summary": row.running_summary,
        "summary_version": row.summary_version,
        "summary_hash": row.summary_hash,
        "summarized_through_sequence": row.summarized_through_sequence,
        "message_count": row.message_count,
        "total_tokens_input": row.total_tokens_input,
        "total_tokens_output": row.total_tokens_output,
        "total_cost_usd": str(row.total_cost_usd or 0),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "last_message_at": row.last_message_at,
    }


def _message_payload(
    row: AIAnalysisMessage,
    reservation=None,
    interrupt=None,
    proposal: CopilotActionPlan | None = None,
) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "conversation_id": str(row.conversation_id),
        "sequence_number": row.sequence_number,
        "role": row.role,
        "message_type": row.message_type,
        "status": row.status,
        "content": row.content,
        "parent_message_id": str(row.parent_message_id) if row.parent_message_id else None,
        "request_kind": row.request_kind,
        "data_mode": row.data_mode,
        "answer_type": row.answer_type,
        "ai_request_id": str(row.ai_request_id) if row.ai_request_id else None,
        "ai_result_id": str(row.ai_result_id) if row.ai_result_id else None,
        "graph_run_id": str(row.graph_run_id) if row.graph_run_id else None,
        "child_analysis_run_id": str(row.child_analysis_run_id) if row.child_analysis_run_id else None,
        "proposal_id": str(row.proposal_id) if row.proposal_id else None,
        "proposal": governed_plan_to_dict(proposal) if proposal else None,
        "configured_provider": row.configured_provider,
        "configured_model": row.configured_model,
        "effective_provider": row.effective_provider,
        "effective_model": row.effective_model,
        "evidence_refs": row.evidence_refs_json or [],
        "tool_call_ids": row.tool_call_ids_json or [],
        "modules_consulted": row.modules_consulted_json or [],
        "warnings": row.warnings_json or [],
        "limitations": row.limitations_json or [],
        "suggested_questions": row.suggested_questions_json or [],
        "new_data_queried": bool(row.new_data_queried),
        "provider_transport_attempted": row.provider_transport_attempted,
        "tokens_input": row.tokens_input,
        "tokens_output": row.tokens_output,
        "cost_usd": str(row.cost_usd) if row.cost_usd is not None else None,
        "budget_reservation": ({
            "status": reservation.status,
            "reserved_tokens": reservation.reserved_tokens,
            "actual_tokens": reservation.actual_tokens,
            "released_tokens": reservation.released_tokens,
            "reserved_cost_usd": str(reservation.reserved_cost_usd),
            "actual_cost_usd": str(reservation.actual_cost_usd) if reservation.actual_cost_usd is not None else None,
            "provider_transport_attempted": reservation.provider_transport_attempted,
            "terminal_reason": reservation.terminal_reason,
        } if reservation else None),
        "pending_interrupt": ({
            "id": str(interrupt.id),
            "interrupt_type": interrupt.interrupt_type,
            "status": interrupt.status,
            "allowed_edit_fields": interrupt.allowed_edit_fields or [],
        } if interrupt else None),
        "created_at": row.created_at,
        "completed_at": row.completed_at,
        "cancelled_at": row.cancelled_at,
    }


async def _message_rows(db: AsyncSession, tenant_id: UUID, conversation_id: UUID):
    rows = await AnalysisChatService.list_messages(
        db, tenant_id=tenant_id, conversation_id=conversation_id
    )
    payloads = []
    for row in rows:
        reservation = None
        if row.ai_request_id:
            reservation = (await db.execute(select(AIBudgetReservationRecord).where(
                AIBudgetReservationRecord.tenant_id == tenant_id,
                AIBudgetReservationRecord.ai_request_id == row.ai_request_id,
            ))).scalar_one_or_none()
        interrupt = None
        if row.graph_run_id:
            interrupt = (await db.execute(select(AIGraphInterrupt).where(
                AIGraphInterrupt.tenant_id == tenant_id,
                AIGraphInterrupt.graph_run_id == row.graph_run_id,
                AIGraphInterrupt.status == "PENDING",
            ).order_by(AIGraphInterrupt.created_at.desc()).limit(1))).scalar_one_or_none()
        proposal = None
        if row.proposal_id:
            proposal = (
                await db.execute(select(CopilotActionPlan).where(
                    CopilotActionPlan.id == row.proposal_id,
                    CopilotActionPlan.user_id == tenant_id,
                ))
            ).scalar_one_or_none()
        payloads.append(_message_payload(row, reservation, interrupt, proposal))
    return payloads


@router.post("/api/intelligence-runs/{run_id}/conversations", status_code=201)
async def create_conversation(
    run_id: UUID,
    payload: CreateConversationRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    try:
        conversation = await AnalysisChatService.create_conversation(
            db, tenant_id=user_id, user_id=user_id, run_id=run_id, title=payload.title
        )
        await db.commit()
        await db.refresh(conversation)
        return _conversation_payload(conversation)
    except AnalysisChatError as exc:
        await db.rollback()
        raise _error(exc) from exc


@router.get("/api/intelligence-runs/{run_id}/conversations")
async def list_conversations(
    run_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    try:
        config, rows = await AnalysisChatService.list_conversations(
            db, tenant_id=user_id, run_id=run_id
        )
        return {
            "items": [_conversation_payload(row) for row in rows],
            "feature_flags": config.model_dump(mode="json"),
        }
    except AnalysisChatError as exc:
        raise _error(exc) from exc


@router.get("/api/intelligence-conversations/{conversation_id}")
async def get_conversation(
    conversation_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    try:
        row = await AnalysisChatService.get_conversation(
            db, tenant_id=user_id, conversation_id=conversation_id
        )
        return _conversation_payload(row)
    except AnalysisChatError as exc:
        raise _error(exc) from exc


@router.get("/api/intelligence-conversations/{conversation_id}/messages")
async def list_messages(
    conversation_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    try:
        return {"items": await _message_rows(db, user_id, conversation_id)}
    except AnalysisChatError as exc:
        raise _error(exc) from exc


@router.post("/api/intelligence-conversations/{conversation_id}/messages", status_code=202)
async def send_message(
    conversation_id: UUID,
    payload: SendMessageRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    try:
        user_message, assistant, graph_run, reused = await AnalysisChatService.send_message(
            db,
            tenant_id=user_id,
            user_id=user_id,
            conversation_id=conversation_id,
            message=payload.message,
            data_mode=payload.data_mode,
            idempotency_key=payload.idempotency_key,
            response_language=payload.response_language,
        )
        await db.commit()
        if not reused:
            from ..tasks.ai_orchestration import start_graph_run
            start_graph_run.apply_async(args=[str(graph_run.id)], queue="ai_orchestration")
        return {
            "reused": reused,
            "user_message": _message_payload(user_message),
            "assistant_message": _message_payload(assistant),
            "graph_run_id": str(graph_run.id),
        }
    except AnalysisChatError as exc:
        await db.rollback()
        raise _error(exc) from exc


@router.post("/api/intelligence-conversations/{conversation_id}/messages/{message_id}/decision", status_code=202)
async def decide_message(
    conversation_id: UUID,
    message_id: UUID,
    payload: DecideMessageRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    try:
        conversation = await AnalysisChatService.get_conversation(
            db, tenant_id=user_id, conversation_id=conversation_id
        )
        message = await db.get(AIAnalysisMessage, message_id)
        if message is None or message.tenant_id != user_id or message.conversation_id != conversation.id or not message.graph_run_id:
            raise AnalysisChatError("ANALYSIS_CHAT_MESSAGE_NOT_FOUND", status_code=404)
        await AnalysisChatService.refresh_proposal_confirmation_contract(
            db,
            tenant_id=user_id,
            user_id=user_id,
            message=message,
            interrupt_id=payload.interrupt_id,
            decision=payload.decision,
        )
        run = await AIGraphRunService.resume(
            db,
            tenant_id=user_id,
            actor_user_id=user_id,
            run_id=message.graph_run_id,
            interrupt_id=payload.interrupt_id,
            decision=payload.decision,
            decision_id=payload.decision_id,
            idempotency_key=payload.idempotency_key,
            edits=payload.edits,
        )
        await db.commit()
        from ..tasks.ai_orchestration import resume_graph_run
        resume_graph_run.apply_async(
            args=[str(run.id), str(payload.interrupt_id)], queue="ai_orchestration"
        )
        return {"status": "QUEUED", "graph_run_id": str(run.id)}
    except AnalysisChatError as exc:
        await db.rollback()
        raise _error(exc) from exc
    except GraphAccessError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail={"code": str(exc)}) from exc


@router.post(
    "/api/intelligence-conversations/{conversation_id}/proposals/{proposal_id}/rollback"
)
async def rollback_proposal(
    conversation_id: UUID,
    proposal_id: UUID,
    payload: RollbackProposalRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    try:
        await AnalysisChatService.get_conversation(
            db, tenant_id=user_id, conversation_id=conversation_id
        )
        proposal = await get_governed_change_plan(db, user_id, proposal_id)
        if str((proposal.evidence or {}).get("conversation_id")) != str(conversation_id):
            raise LookupError("Governed change proposal not found in this conversation")
        plan = await rollback_governed_change(
            db,
            user_id,
            proposal_id,
            confirmation_text=payload.confirmation_text,
        )
        return {"status": "ROLLED_BACK", "proposal": plan}
    except LookupError as exc:
        await db.rollback()
        raise HTTPException(status_code=404, detail={"code": str(exc)}) from exc
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail={"code": str(exc)}) from exc


@router.post("/api/intelligence-conversations/{conversation_id}/cancel")
async def cancel_conversation_message(
    conversation_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    try:
        message = await AnalysisChatService.cancel(
            db, tenant_id=user_id, user_id=user_id, conversation_id=conversation_id
        )
        await db.commit()
        return {"status": message.status if message else "NO_ACTIVE_MESSAGE"}
    except AnalysisChatError as exc:
        await db.rollback()
        raise _error(exc) from exc


async def _stream_snapshot(tenant_id: UUID, conversation_id: UUID, after_id: int):
    async def _query(db):
        rows = list((await db.execute(
            select(AIGraphEvent, AIRequestRecord)
            .join(AIGraphRun, AIGraphRun.id == AIGraphEvent.graph_run_id)
            .join(AIRequestRecord, AIRequestRecord.id == AIGraphRun.ai_request_id)
            .where(
                AIGraphEvent.tenant_id == tenant_id,
                AIRequestRecord.conversation_id == conversation_id,
                AIGraphEvent.id > after_id,
            )
            .order_by(AIGraphEvent.id)
            .limit(100)
        )).all())
        active = (await db.execute(select(AIAnalysisMessage.id).where(
            AIAnalysisMessage.tenant_id == tenant_id,
            AIAnalysisMessage.conversation_id == conversation_id,
            AIAnalysisMessage.role == "ASSISTANT",
            AIAnalysisMessage.status.in_(("PENDING", "QUEUED", "STREAMING", "INTERRUPTED")),
        ).limit(1))).scalar_one_or_none()
        return rows, active is not None
    return await run_db_task(_query)


@router.get("/api/intelligence-conversations/{conversation_id}/stream")
async def stream_conversation(
    conversation_id: UUID,
    request: Request,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    try:
        config = await AnalysisChatService.runtime_config(db, user_id)
        if not config.enabled or not config.streaming_enabled:
            raise AnalysisChatError("ANALYSIS_CHAT_STREAMING_DISABLED", status_code=403)
        await AnalysisChatService.get_conversation(
            db, tenant_id=user_id, conversation_id=conversation_id
        )
    except AnalysisChatError as exc:
        raise _error(exc) from exc
    try:
        cursor = max(0, int(last_event_id or 0))
    except ValueError:
        cursor = 0

    async def events():
        nonlocal cursor
        connected = {
            "event_id": cursor,
            "conversation_id": str(conversation_id),
            "data": {"reconnected": cursor > 0},
        }
        yield f"event: connected\ndata: {json.dumps(connected)}\n\n"
        idle_terminal_polls = 0
        heartbeat_at = datetime.now(timezone.utc)
        while True:
            if await request.is_disconnected():
                return
            rows, active = await _stream_snapshot(user_id, conversation_id, cursor)
            for event, ai_request in rows:
                cursor = int(event.id)
                event_name = {
                    "TOKEN": "token",
                    "NODE_COMPLETED": "node_completed",
                    "REQUESTED": "message_accepted",
                    "INTERRUPTED": "blocked",
                    "FAILED": "blocked" if (event.payload or {}).get("error_kind") == "PROVIDER_BLOCKED" else "error",
                    "COMPLETED": "completed",
                }.get(event.event_type, event.event_type.lower())
                envelope = {
                    "event_id": cursor,
                    "conversation_id": str(conversation_id),
                    "message_id": (event.payload or {}).get("message_id"),
                    "graph_run_id": str(event.graph_run_id),
                    "node": event.node_name,
                    "data": event.payload or {},
                }
                yield f"id: {cursor}\nevent: {event_name}\ndata: {json.dumps(envelope, default=str)}\n\n"
            if not active and not rows:
                idle_terminal_polls += 1
                if idle_terminal_polls >= 2:
                    return
            else:
                idle_terminal_polls = 0
            now = datetime.now(timezone.utc)
            if (now - heartbeat_at).total_seconds() >= 15:
                yield f"event: heartbeat\ndata: {json.dumps({'event_id': cursor, 'conversation_id': str(conversation_id)})}\n\n"
                heartbeat_at = now
            await asyncio.sleep(0.75)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
