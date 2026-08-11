"""Durable LangGraph tasks on the isolated ``ai_orchestration`` queue."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import os
from uuid import UUID

from langgraph.types import Command
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from .celery_app import celery_app
from .task_dispatch import enqueue
from ..ai_orchestration.errors import GraphNodeExecutionError, ProviderBlockedError
from ..ai_orchestration.langgraph.checkpoint import postgres_checkpointer
from ..ai_orchestration.langgraph.config import get_langgraph_settings
from ..ai_orchestration.langgraph.graphs import build_graph
from ..ai_orchestration.langgraph.handler import CanonicalGraphNodeHandler
from ..ai_orchestration.langgraph.analysis_chat_handler import AnalysisChatGraphNodeHandler
from ..ai_orchestration.langgraph.metrics import (
    graph_runs_failed, graph_runs_interrupted, graph_runs_resumed,
    graph_runs_running, graph_runs_total, stale_graph_leases,
)
from ..ai_orchestration.langgraph.registry import resolve_graph
from ..database import run_db_task
from ..models.ai_graph import (
    AIGraphDefinition, AIGraphEvent, AIGraphInterrupt, AIGraphRun,
)
from ..models.systemic_ai import AIJobRecord, AIRequestRecord
from ..models.systemic_ai import AIBudgetReservationRecord
from ..models.analysis_chat import AIAnalysisMessage


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _acquire_run(db, run_id: UUID):
    run = (
        await db.execute(select(AIGraphRun).where(AIGraphRun.id == run_id).with_for_update())
    ).scalar_one_or_none()
    if run is None:
        raise RuntimeError("GRAPH_RUN_NOT_FOUND")
    if run.status in {"COMPLETED", "FAILED", "CANCELLED"}:
        return None
    now = _now()
    if run.status == "RUNNING" and run.lease_expires_at and run.lease_expires_at > now:
        return None
    definition = await db.get(AIGraphDefinition, run.graph_definition_id)
    request = await db.get(AIRequestRecord, run.ai_request_id)
    if definition is None or request is None or request.tenant_id != run.tenant_id:
        raise RuntimeError("GRAPH_CANONICAL_LINEAGE_INVALID")
    code_definition = resolve_graph(definition.graph_key)
    if definition.content_hash != code_definition.content_hash:
        raise RuntimeError("GRAPH_DEFINITION_HASH_MISMATCH")
    settings = get_langgraph_settings()
    settings.require_runtime()
    run.status = "RUNNING"
    run.started_at = run.started_at or now
    run.heartbeat_at = now
    run.lease_owner = f"{os.getenv('RAILWAY_SERVICE_NAME', 'celery')}:{os.getpid()}"
    run.lease_expires_at = now + timedelta(seconds=settings.lease_seconds)
    run.last_error_code = None
    run.last_error_safe_message = None
    run.failed_node = None
    run.error_kind = None
    # A fresh lease has not attempted provider transport yet.  The terminal
    # handlers replace this value with the audited outcome from the run.
    run.provider_transport_attempted = None
    run.updated_at = now
    if run.ai_job_id:
        job = await db.get(AIJobRecord, run.ai_job_id)
        if job is None or job.tenant_id != run.tenant_id or job.ai_request_id != run.ai_request_id:
            raise RuntimeError("GRAPH_JOB_STATE_MISMATCH")
        job.status = "RUNNING"
        job.started_at = job.started_at or now
        job.heartbeat_at = now
        job.lease_owner = run.lease_owner
        job.lease_expires_at = run.lease_expires_at
        job.attempt = int(job.attempt or 0) + 1
    return {
        "run_id": run.id,
        "thread_id": run.thread_id,
        "checkpoint_namespace": run.checkpoint_namespace,
        "tenant_id": run.tenant_id,
        "user_id": run.requested_by_user_id,
        "ai_request_id": run.ai_request_id,
        "graph_key": definition.graph_key,
        "graph_version": definition.semantic_version,
        "state_schema_version": run.state_schema_version,
        "authority": run.authority,
        "job_id": run.ai_job_id,
    }


async def _mark_terminal(db, run_id: UUID, final_state: dict):
    run = (
        await db.execute(select(AIGraphRun).where(AIGraphRun.id == run_id).with_for_update())
    ).scalar_one()
    if run.status == "CANCELLED":
        return
    now = _now()
    run.status = "COMPLETED"
    run.current_node = final_state.get("current_node")
    run.last_completed_node = final_state.get("current_node") or run.last_completed_node
    run.failed_node = None
    run.error_kind = None
    run.provider_transport_attempted = None
    run.terminal_reason = final_state.get("terminal_reason") or "GRAPH_COMPLETED"
    run.completed_at = now
    run.heartbeat_at = now
    run.lease_owner = None
    run.lease_expires_at = None
    run.updated_at = now
    if run.ai_job_id:
        job = await db.get(AIJobRecord, run.ai_job_id)
        if job is None or job.tenant_id != run.tenant_id:
            raise RuntimeError("GRAPH_JOB_STATE_MISMATCH")
        job.status = "COMPLETED"
        job.completed_at = now
        job.terminal_reason = run.terminal_reason
        job.lease_owner = None
        job.lease_expires_at = None
    request = await db.get(AIRequestRecord, run.ai_request_id)
    if request is not None and request.request_kind in {
        "FOLLOW_UP_CHAT", "CHILD_ANALYSIS", "PROPOSAL_DRAFT", "CONVERSATION_SUMMARY",
    }:
        message = (await db.execute(select(AIAnalysisMessage).where(
            AIAnalysisMessage.tenant_id == run.tenant_id,
            AIAnalysisMessage.ai_request_id == request.id,
            AIAnalysisMessage.role == "ASSISTANT",
        ).with_for_update())).scalar_one_or_none()
        if message is not None and message.status == "INTERRUPTED":
            message.status = "CANCELLED"
            message.content = "A ação human-gated foi rejeitada. Nenhuma mudança foi aplicada."
            message.content_hash = __import__("hashlib").sha256(message.content.encode("utf-8")).hexdigest()
            message.provider_transport_attempted = False
            message.cancelled_at = now
    await db.execute(insert(AIGraphEvent).values(
        tenant_id=run.tenant_id, graph_run_id=run.id,
        event_key=f"{run.id}:completed", event_type="COMPLETED",
        node_name=run.current_node, status="COMPLETED",
        payload={"terminal_reason": run.terminal_reason},
    ).on_conflict_do_nothing(index_elements=[AIGraphEvent.graph_run_id, AIGraphEvent.event_key]))


async def _mark_interrupted(db, run_id: UUID, interrupt_value):
    run = (
        await db.execute(select(AIGraphRun).where(AIGraphRun.id == run_id).with_for_update())
    ).scalar_one()
    value = dict(interrupt_value.value)
    interrupt_type = value.get("interrupt_type", "HUMAN_DECISION")
    allowed_fields = {
        "CANDIDATE_APPROVAL": ["candidate_version_ids", "hypothesis_notes"],
        "SHADOW_EVIDENCE": [],
        "FINAL_DECISION": ["decision_notes", "rollback_reason"],
        "CHILD_ANALYSIS_CONFIRMATION": [],
        "PROPOSAL_CONFIRMATION": [],
        "PROPOSAL_APPROVAL": ["proposal_notes"],
    }.get(interrupt_type, [])
    interrupt_key = f"{interrupt_type}:{interrupt_value.id}"
    await db.execute(insert(AIGraphInterrupt).values(
        tenant_id=run.tenant_id,
        graph_run_id=run.id,
        interrupt_key=interrupt_key,
        interrupt_type=interrupt_type,
        status="PENDING",
        payload=value,
        allowed_edit_fields=allowed_fields,
    ).on_conflict_do_nothing(
        index_elements=[AIGraphInterrupt.graph_run_id, AIGraphInterrupt.interrupt_key]
    ))
    run.status = "WAITING_SHADOW" if interrupt_type == "SHADOW_EVIDENCE" else "INTERRUPTED"
    run.current_node = {
        "CANDIDATE_APPROVAL": "interrupt_candidate_approval",
        "SHADOW_EVIDENCE": "interrupt_wait_for_shadow_evidence",
        "FINAL_DECISION": "interrupt_final_decision",
        "CHILD_ANALYSIS_CONFIRMATION": "interrupt_child_analysis_confirmation",
        "PROPOSAL_CONFIRMATION": "interrupt_proposal_confirmation",
        "PROPOSAL_APPROVAL": "interrupt_proposal_approval",
    }.get(interrupt_type)
    if interrupt_type == "SHADOW_EVIDENCE" and run.current_node is None:
        run.current_node = "interrupt_wait_evidence"
    run.lease_owner = None
    run.lease_expires_at = None
    run.updated_at = _now()
    request = await db.get(AIRequestRecord, run.ai_request_id)
    if request is not None and request.request_kind in {
        "FOLLOW_UP_CHAT", "CHILD_ANALYSIS", "PROPOSAL_DRAFT", "CONVERSATION_SUMMARY",
    }:
        message = (await db.execute(select(AIAnalysisMessage).where(
            AIAnalysisMessage.tenant_id == run.tenant_id,
            AIAnalysisMessage.ai_request_id == request.id,
            AIAnalysisMessage.role == "ASSISTANT",
        ).with_for_update())).scalar_one_or_none()
        if message is not None:
            message.status = "INTERRUPTED"
            message.lock_version = int(message.lock_version or 0) + 1
    await db.execute(insert(AIGraphEvent).values(
        tenant_id=run.tenant_id, graph_run_id=run.id,
        event_key=f"{run.id}:interrupt:{interrupt_key}", event_type="INTERRUPTED",
        node_name=run.current_node, status=run.status,
        payload={"interrupt_type": interrupt_type},
    ).on_conflict_do_nothing(index_elements=[AIGraphEvent.graph_run_id, AIGraphEvent.event_key]))


def _failure_details(exc: Exception) -> dict:
    failed_node = exc.node_name if isinstance(exc, GraphNodeExecutionError) else None
    cause = exc.cause if isinstance(exc, GraphNodeExecutionError) else exc
    reason_code = str(getattr(cause, "reason_code", "") or str(cause).split(":", 1)[0])
    reason_code = reason_code.strip().upper().replace(" ", "_")
    if not reason_code or len(reason_code) > 80:
        reason_code = type(cause).__name__.upper()[:80]
    if isinstance(cause, ProviderBlockedError):
        error_kind = "PROVIDER_BLOCKED"
        terminal_reason = "PROVIDER_BLOCKED"
    else:
        error_kind = str(getattr(cause, "error_kind", "GRAPH_EXECUTION_FAILED"))[:80]
        terminal_reason = "FAIL_CLOSED"
    return {
        "failed_node": failed_node,
        "error_kind": error_kind,
        "reason_code": reason_code,
        "safe_message": str(
            getattr(cause, "safe_message", "The systemic AI graph could not complete")
        )[:500],
        "provider_transport_attempted": bool(
            getattr(cause, "provider_transport_attempted", False)
        ),
        "terminal_reason": terminal_reason,
    }


async def _mark_failed(
    db, run_id: UUID, *, failed_node: str | None, error_kind: str,
    reason_code: str, safe_message: str, provider_transport_attempted: bool,
    terminal_reason: str,
):
    run = (
        await db.execute(select(AIGraphRun).where(AIGraphRun.id == run_id).with_for_update())
    ).scalar_one_or_none()
    if run is None or run.status == "CANCELLED":
        return
    now = _now()
    run.status = "FAILED"
    run.failed_node = failed_node
    run.error_kind = error_kind[:80]
    run.last_error_code = reason_code[:80]
    run.last_error_safe_message = safe_message[:500]
    run.provider_transport_attempted = provider_transport_attempted
    run.terminal_reason = terminal_reason[:160]
    run.completed_at = now
    run.lease_owner = None
    run.lease_expires_at = None
    run.updated_at = now
    if run.ai_job_id:
        job = await db.get(AIJobRecord, run.ai_job_id)
        if job:
            job.status = "FAILED_TERMINAL"
            job.last_error_code = run.last_error_code
            job.last_error_safe_message = run.last_error_safe_message
            job.terminal_reason = run.terminal_reason
            job.completed_at = now
            job.lease_owner = None
            job.lease_expires_at = None
    request = await db.get(AIRequestRecord, run.ai_request_id)
    correlation_id = request.correlation_id if request and request.tenant_id == run.tenant_id else None
    if request is not None and request.request_kind in {
        "FOLLOW_UP_CHAT", "CHILD_ANALYSIS", "PROPOSAL_DRAFT", "CONVERSATION_SUMMARY",
    }:
        message = (await db.execute(select(AIAnalysisMessage).where(
            AIAnalysisMessage.tenant_id == run.tenant_id,
            AIAnalysisMessage.ai_request_id == request.id,
            AIAnalysisMessage.role == "ASSISTANT",
        ).with_for_update())).scalar_one_or_none()
        if message is not None:
            message.status = "BLOCKED" if error_kind == "PROVIDER_BLOCKED" else "FAILED"
            message.message_type = "ERROR_NOTICE"
            message.content = safe_message[:500]
            message.content_hash = __import__("hashlib").sha256(message.content.encode("utf-8")).hexdigest()
            message.provider_transport_attempted = provider_transport_attempted
            message.completed_at = now
            message.lock_version = int(message.lock_version or 0) + 1
        reservation = (await db.execute(select(AIBudgetReservationRecord).where(
            AIBudgetReservationRecord.ai_request_id == request.id
        ).with_for_update())).scalar_one_or_none()
        if reservation is not None and reservation.status in {"RESERVED", "TRANSPORT_STARTED"}:
            reservation.status = "RELEASED"
            reservation.released_tokens = int(reservation.reserved_tokens or 0)
            reservation.provider_transport_attempted = provider_transport_attempted
            reservation.terminal_reason = reason_code[:160]
            reservation.released_at = now
            reservation.updated_at = now
    await db.execute(insert(AIGraphEvent).values(
        tenant_id=run.tenant_id, graph_run_id=run.id,
        event_key=f"{run.id}:failed", event_type="FAILED",
        node_name=failed_node, status="FAILED",
        payload={
            "error_kind": run.error_kind,
            "reason_code": run.last_error_code,
            "safe_message": run.last_error_safe_message,
            "terminal_reason": run.terminal_reason,
            "provider_transport_attempted": provider_transport_attempted,
            "correlation_id": correlation_id,
        },
    ).on_conflict_do_nothing(index_elements=[AIGraphEvent.graph_run_id, AIGraphEvent.event_key]))


async def execute_graph_run(run_id: UUID, *, resume_payload: dict | None = None) -> dict:
    context = await run_db_task(lambda db: _acquire_run(db, run_id), celery=True)
    if context is None:
        return {"status": "NOOP", "run_id": str(run_id)}
    graph = None
    graph_runs_running.inc()
    try:
        handler = (
            AnalysisChatGraphNodeHandler(run_id, celery=True)
            if context["graph_key"] == "analysis-chat-v1"
            else CanonicalGraphNodeHandler(run_id, celery=True)
        )
        async with postgres_checkpointer() as saver:
            graph = build_graph(context["graph_key"], handler=handler, checkpointer=saver)
            config = {"configurable": {
                "thread_id": str(context["thread_id"]),
                "checkpoint_ns": context["checkpoint_namespace"],
            }}
            if resume_payload is None:
                definition = resolve_graph(context["graph_key"])
                graph_input = {
                    "state_schema_version": definition.state_schema_version,
                    "ai_request_id": str(context["ai_request_id"]),
                    "tenant_id": str(context["tenant_id"]),
                    "user_id": str(context["user_id"]) if context["user_id"] else None,
                    "graph_run_id": str(context["run_id"]),
                    "graph_key": context["graph_key"],
                    "graph_version": context["graph_version"],
                    "job_id": str(context["job_id"]) if context["job_id"] else None,
                    "status": "RUNNING",
                    "authority": context["authority"],
                    "completed_nodes": [], "event_keys": [], "tool_call_ids": [],
                    "evidence_refs": [], "candidate_version_ids": [],
                    "decision_memory_ids": [], "memory_hits": [],
                    "recommendations": [], "warnings": [], "limitations": [],
                }
            else:
                graph_input = Command(resume=resume_payload)
            final_state = await graph.ainvoke(graph_input, config=config)
        interrupts = final_state.get("__interrupt__") or []
        if interrupts:
            await run_db_task(lambda db: _mark_interrupted(db, run_id, interrupts[0]), celery=True)
            graph_runs_interrupted.labels(interrupt_type=interrupts[0].value.get("interrupt_type", "UNKNOWN")).inc()
            graph_runs_total.labels(graph_key=context["graph_key"], status="INTERRUPTED").inc()
            return {"status": "INTERRUPTED", "run_id": str(run_id)}
        await run_db_task(lambda db: _mark_terminal(db, run_id, final_state), celery=True)
        graph_runs_total.labels(graph_key=context["graph_key"], status="COMPLETED").inc()
        return {"status": "COMPLETED", "run_id": str(run_id)}
    except Exception as exc:
        failure = _failure_details(exc)
        await run_db_task(lambda db: _mark_failed(db, run_id, **failure), celery=True)
        graph_runs_failed.labels(error_code=failure["reason_code"]).inc()
        graph_runs_total.labels(graph_key=context["graph_key"], status="FAILED").inc()
        raise
    finally:
        graph_runs_running.dec()


@celery_app.task(name="app.tasks.ai_orchestration.start_graph_run")
def start_graph_run(run_id: str) -> dict:
    return asyncio.run(execute_graph_run(UUID(run_id)))


@celery_app.task(name="app.tasks.ai_orchestration.dispatch_queued_graph_runs")
def dispatch_queued_graph_runs() -> dict:
    """Transactional outbox dispatcher for API and legacy-created graph runs."""
    async def _queued(db):
        rows = list((await db.execute(select(AIGraphRun.id).where(
            AIGraphRun.status == "QUEUED",
        ).order_by(AIGraphRun.created_at).limit(50))).scalars().all())
        return [str(value) for value in rows]

    run_ids = asyncio.run(run_db_task(_queued, celery=True))
    for run_id in run_ids:
        enqueue(
            "app.tasks.ai_orchestration.start_graph_run",
            dedup_key=f"ai-graph-queued:{run_id}",
            ttl_seconds=600,
            queue="ai_orchestration",
            args=(run_id,),
        )
    return {"status": "COMPLETED", "dispatched": len(run_ids)}


@celery_app.task(name="app.tasks.ai_orchestration.resume_graph_run")
def resume_graph_run(run_id: str, interrupt_id: str) -> dict:
    async def _load(db):
        record = await db.get(AIGraphInterrupt, UUID(interrupt_id))
        if record is None or record.graph_run_id != UUID(run_id) or record.status not in {"RESOLVED", "REJECTED"}:
            raise RuntimeError("GRAPH_INTERRUPT_NOT_RESOLVED")
        return {"decision": record.decision, **(record.decision_payload or {})}
    payload = asyncio.run(run_db_task(_load, celery=True))
    graph_runs_resumed.labels(decision=payload.get("decision") or "unknown").inc()
    return asyncio.run(execute_graph_run(UUID(run_id), resume_payload=payload))


@celery_app.task(name="app.tasks.ai_orchestration.recover_stale_graph_runs")
def recover_stale_graph_runs() -> dict:
    async def _recover(db):
        now = _now()
        rows = list((await db.execute(select(AIGraphRun).where(
            AIGraphRun.status == "RUNNING",
            AIGraphRun.lease_expires_at < now,
        ).with_for_update(skip_locked=True).limit(50))).scalars().all())
        for run in rows:
            run.status = "QUEUED"
            run.lease_owner = None
            run.lease_expires_at = None
        return [str(run.id) for run in rows]
    run_ids = asyncio.run(run_db_task(_recover, celery=True))
    stale_graph_leases.inc(len(run_ids))
    for run_id in run_ids:
        enqueue(
            "app.tasks.ai_orchestration.start_graph_run",
            dedup_key=f"ai-graph-recover:{run_id}", ttl_seconds=600,
            queue="ai_orchestration", args=(run_id,),
        )
    return {"status": "COMPLETED", "requeued": len(run_ids)}


@celery_app.task(name="app.tasks.ai_orchestration.cancel_graph_run")
def cancel_graph_run(run_id: str) -> dict:
    async def _cancel(db):
        run = (await db.execute(select(AIGraphRun).where(
            AIGraphRun.id == UUID(run_id)).with_for_update())).scalar_one_or_none()
        if run is None:
            raise RuntimeError("GRAPH_RUN_NOT_FOUND")
        if run.status not in {"COMPLETED", "FAILED", "CANCELLED"}:
            run.status = "CANCELLED"
            run.cancelled_at = _now()
            run.completed_at = run.cancelled_at
            run.terminal_reason = "CANCELLED_BY_AUTHORIZED_ACTOR"
            run.lease_owner = None
            run.lease_expires_at = None
        return run.status
    status = asyncio.run(run_db_task(_cancel, celery=True))
    return {"status": status, "run_id": run_id}


@celery_app.task(name="app.tasks.ai_orchestration.dispatch_shadow_resume_events")
def dispatch_shadow_resume_events() -> dict:
    async def _ready(db):
        records = list((await db.execute(select(AIGraphInterrupt).where(
            AIGraphInterrupt.interrupt_type == "SHADOW_EVIDENCE",
            AIGraphInterrupt.status == "RESOLVED",
        ).limit(50))).scalars().all())
        return [(str(record.graph_run_id), str(record.id)) for record in records]
    ready = asyncio.run(run_db_task(_ready, celery=True))
    for run_id, interrupt_id in ready:
        enqueue(
            "app.tasks.ai_orchestration.resume_graph_run",
            dedup_key=f"ai-graph-shadow-resume:{interrupt_id}", ttl_seconds=600,
            queue="ai_orchestration", args=(run_id, interrupt_id),
        )
    return {"status": "COMPLETED", "dispatched": len(ready)}
