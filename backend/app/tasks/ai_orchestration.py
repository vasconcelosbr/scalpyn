"""Durable LangGraph tasks on the isolated ``ai_orchestration`` queue."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import os
from uuid import UUID

from langgraph.types import Command
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from .celery_app import celery_app
from .task_dispatch import enqueue
from ..ai_orchestration.errors import AIOrchestrationError, GraphNodeExecutionError, ProviderBlockedError
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
    AI_GRAPH_DISPATCH_RESUME,
    AI_GRAPH_DISPATCH_START,
    AIGraphDefinition,
    AIGraphEvent,
    AIGraphInterrupt,
    AIGraphRun,
)
from ..models.systemic_ai import AIJobRecord, AIRequestRecord, AIUsageRecord
from ..models.systemic_ai import AIBudgetReservationRecord
from ..models.analysis_chat import AIAnalysisConversation, AIAnalysisMessage
from ..models.ai_provider_key import AIProviderKey


def _now() -> datetime:
    return datetime.now(timezone.utc)


_TERMINAL_RUN_STATUSES = frozenset({"COMPLETED", "FAILED", "CANCELLED"})


def _dispatch_matches(
    run: AIGraphRun,
    *,
    dispatch_kind: str,
    interrupt_id: UUID | None = None,
    decision_id: UUID | None = None,
) -> bool:
    """Return whether this delivery still names the durable queued command.

    Celery delivery is at-least-once.  The database tuple is therefore the
    fencing token: a redelivery for an older human gate must not resume the
    graph after a newer gate has replaced it.
    """
    if run.status != "QUEUED" or run.dispatch_kind != dispatch_kind:
        return False
    if dispatch_kind == AI_GRAPH_DISPATCH_START:
        return (
            interrupt_id is None
            and decision_id is None
            and run.dispatch_interrupt_id is None
            and run.dispatch_decision_id is None
        )
    if dispatch_kind != AI_GRAPH_DISPATCH_RESUME:
        return False
    if run.dispatch_interrupt_id is None or run.dispatch_decision_id is None:
        return False
    if interrupt_id != run.dispatch_interrupt_id:
        return False
    return decision_id == run.dispatch_decision_id


def _queued_dispatch_spec(run: AIGraphRun) -> dict[str, object] | None:
    """Build the exact Celery command represented by a queued run."""
    run_id = str(run.id)
    if run.dispatch_kind == AI_GRAPH_DISPATCH_START:
        if not _dispatch_matches(run, dispatch_kind=AI_GRAPH_DISPATCH_START):
            return None
        return {
            "task_name": "app.tasks.ai_orchestration.start_graph_run",
            "args": (run_id,),
            "dedup_key": f"ai-graph-dispatch:start:{run_id}",
        }
    if run.dispatch_kind == AI_GRAPH_DISPATCH_RESUME:
        interrupt_id = run.dispatch_interrupt_id
        decision_id = run.dispatch_decision_id
        if not _dispatch_matches(
            run,
            dispatch_kind=AI_GRAPH_DISPATCH_RESUME,
            interrupt_id=interrupt_id,
            decision_id=decision_id,
        ):
            return None
        return {
            "task_name": "app.tasks.ai_orchestration.resume_graph_run",
            "args": (run_id, str(interrupt_id), str(decision_id)),
            "dedup_key": (
                f"ai-graph-dispatch:resume:{run_id}:{interrupt_id}:{decision_id}"
            ),
        }
    return None


async def _start_dispatch_has_interrupt_history(db, run: AIGraphRun) -> bool:
    """Fence legacy START rows that have already reached a human gate.

    Migration 167 necessarily defaults historical rows to START.  An older API
    can subsequently resolve one of those interrupts without persisting the
    new RESUME tuple.  Any interrupt history proves the run is no longer a
    first invocation, including a still-pending gate.
    """
    if run.dispatch_kind != AI_GRAPH_DISPATCH_START:
        return False
    interrupt_status = (await db.execute(select(AIGraphInterrupt.status).where(
        AIGraphInterrupt.graph_run_id == run.id,
        AIGraphInterrupt.tenant_id == run.tenant_id,
        AIGraphInterrupt.status.in_(("PENDING", "RESOLVED", "REJECTED")),
    ).limit(1))).scalar_one_or_none()
    return interrupt_status is not None


async def _mark_legacy_start_reconciliation_required(
    db,
    run: AIGraphRun,
) -> None:
    await _mark_failed(
        db,
        run.id,
        failed_node=run.current_node,
        error_kind="GRAPH_RECONCILIATION_REQUIRED",
        reason_code="LEGACY_START_INTERRUPT_HISTORY_RECONCILIATION_REQUIRED",
        safe_message=(
            "The graph has human-interrupt history but no durable resume command; "
            "it was not restarted and requires audited reconciliation."
        ),
        provider_transport_attempted=bool(run.provider_transport_attempted),
        terminal_reason="FAIL_CLOSED_RECONCILIATION_REQUIRED",
    )


async def _guarded_queued_dispatch_spec(
    db,
    run: AIGraphRun,
) -> tuple[dict[str, object] | None, bool]:
    if await _start_dispatch_has_interrupt_history(db, run):
        await _mark_legacy_start_reconciliation_required(db, run)
        return None, True
    return _queued_dispatch_spec(run), False


def _enqueue_dispatch(spec: dict[str, object]) -> str | None:
    return enqueue(
        str(spec["task_name"]),
        dedup_key=str(spec["dedup_key"]),
        ttl_seconds=960,
        queue="ai_orchestration",
        args=tuple(spec["args"]),
    )


async def _acquire_run(
    db,
    run_id: UUID,
    *,
    dispatch_kind: str,
    interrupt_id: UUID | None = None,
    decision_id: UUID | None = None,
):
    run = (
        await db.execute(select(AIGraphRun).where(AIGraphRun.id == run_id).with_for_update())
    ).scalar_one_or_none()
    if run is None:
        raise RuntimeError("GRAPH_RUN_NOT_FOUND")
    if not _dispatch_matches(
        run,
        dispatch_kind=dispatch_kind,
        interrupt_id=interrupt_id,
        decision_id=decision_id,
    ):
        return None
    if (
        dispatch_kind == AI_GRAPH_DISPATCH_START
        and await _start_dispatch_has_interrupt_history(db, run)
    ):
        await _mark_legacy_start_reconciliation_required(db, run)
        return None

    # Lock order is always run -> interrupt, matching AIGraphRunService.resume.
    # The decision is loaded under the same transaction that changes QUEUED to
    # RUNNING, so no other delivery can substitute a newer human decision.
    resume_payload = None
    persisted_interrupt_id = None
    persisted_decision_id = None
    if dispatch_kind == AI_GRAPH_DISPATCH_RESUME:
        persisted_interrupt_id = run.dispatch_interrupt_id
        persisted_decision_id = run.dispatch_decision_id
        record = (
            await db.execute(
                select(AIGraphInterrupt).where(
                    AIGraphInterrupt.id == persisted_interrupt_id,
                    AIGraphInterrupt.graph_run_id == run.id,
                    AIGraphInterrupt.tenant_id == run.tenant_id,
                ).with_for_update()
            )
        ).scalar_one_or_none()
        if (
            record is None
            or record.status not in {"RESOLVED", "REJECTED"}
            or record.decision_id != persisted_decision_id
            or record.decision is None
        ):
            raise RuntimeError("GRAPH_RESUME_DISPATCH_INVALID")
        resume_payload = {
            **dict(record.decision_payload or {}),
            "decision": record.decision,
            "decision_id": str(record.decision_id),
            "actor_user_id": str(record.actor_user_id) if record.actor_user_id else None,
        }

    now = _now()
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
    job = None
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
        "dispatch_kind": dispatch_kind,
        "dispatch_interrupt_id": persisted_interrupt_id,
        "dispatch_decision_id": persisted_decision_id,
        "resume_payload": resume_payload,
    }


async def _mark_terminal(db, run_id: UUID, final_state: dict):
    run = (
        await db.execute(select(AIGraphRun).where(AIGraphRun.id == run_id).with_for_update())
    ).scalar_one()
    if run.status in _TERMINAL_RUN_STATUSES:
        return
    now = _now()
    request = await db.get(AIRequestRecord, run.ai_request_id)
    request_is_scoped = request is not None and request.tenant_id == run.tenant_id
    is_chat_request = request_is_scoped and request.request_kind in {
        "FOLLOW_UP_CHAT", "CHILD_ANALYSIS", "PROPOSAL_DRAFT", "CONVERSATION_SUMMARY",
    }
    usage = None
    reservation = None
    if is_chat_request:
        usage = (await db.execute(select(AIUsageRecord).where(
            AIUsageRecord.tenant_id == run.tenant_id,
            AIUsageRecord.ai_request_id == request.id,
        ))).scalar_one_or_none()
        reservation = (await db.execute(select(AIBudgetReservationRecord).where(
            AIBudgetReservationRecord.tenant_id == run.tenant_id,
            AIBudgetReservationRecord.ai_request_id == request.id,
        ).with_for_update())).scalar_one_or_none()
    result_json = final_state.get("result_json")
    state_transport_attempted = bool(
        run.provider_transport_attempted
        or final_state.get("provider_transport_attempted") is True
        or (
            isinstance(result_json, dict)
            and result_json.get("provider_transport_attempted") is True
        )
    )
    provider_transport_attempted = _audited_provider_transport_attempted(
        state_transport_attempted,
        reservation=reservation,
        usage=usage,
    )
    run.status = "COMPLETED"
    run.current_node = final_state.get("current_node")
    run.last_completed_node = final_state.get("current_node") or run.last_completed_node
    run.failed_node = None
    run.error_kind = None
    run.provider_transport_attempted = provider_transport_attempted
    run.terminal_reason = final_state.get("terminal_reason") or "GRAPH_COMPLETED"
    run.completed_at = now
    run.heartbeat_at = now
    run.lease_owner = None
    run.lease_expires_at = None
    run.updated_at = now
    job = None
    if run.ai_job_id:
        job = await db.get(AIJobRecord, run.ai_job_id)
        if job is None or job.tenant_id != run.tenant_id:
            raise RuntimeError("GRAPH_JOB_STATE_MISMATCH")
        job.status = "COMPLETED"
        job.completed_at = now
        job.terminal_reason = run.terminal_reason
        job.lease_owner = None
        job.lease_expires_at = None
    if is_chat_request:
        message = (await db.execute(select(AIAnalysisMessage).where(
            AIAnalysisMessage.tenant_id == run.tenant_id,
            AIAnalysisMessage.ai_request_id == request.id,
            AIAnalysisMessage.role == "ASSISTANT",
        ).with_for_update())).scalar_one_or_none()
        if message is not None and message.status == "INTERRUPTED":
            run.terminal_reason = "HUMAN_GATE_REJECTED"
            if job is not None:
                job.terminal_reason = run.terminal_reason
            message.status = "CANCELLED"
            message.content = "A ação human-gated foi rejeitada. Nenhuma mudança foi aplicada."
            message.content_hash = __import__("hashlib").sha256(message.content.encode("utf-8")).hexdigest()
            message.provider_transport_attempted = provider_transport_attempted
            message.cancelled_at = now
            if (
                reservation is not None
                and reservation.status == "RESERVED"
                and not provider_transport_attempted
            ):
                reservation.status = "RELEASED"
                reservation.actual_tokens = 0
                reservation.actual_cost_usd = Decimal("0")
                reservation.released_tokens = int(reservation.reserved_tokens or 0)
                reservation.provider_transport_attempted = False
                reservation.terminal_reason = "HUMAN_GATE_REJECTED"
                reservation.released_at = now
                reservation.updated_at = now
    await db.execute(insert(AIGraphEvent).values(
        tenant_id=run.tenant_id, graph_run_id=run.id,
        event_key=f"{run.id}:completed", event_type="COMPLETED",
        node_name=run.current_node, status="COMPLETED",
        payload={
            "terminal_reason": run.terminal_reason,
            "provider_transport_attempted": provider_transport_attempted,
        },
    ).on_conflict_do_nothing(index_elements=[AIGraphEvent.graph_run_id, AIGraphEvent.event_key]))


async def _mark_interrupted(db, run_id: UUID, interrupt_value):
    run = (
        await db.execute(select(AIGraphRun).where(AIGraphRun.id == run_id).with_for_update())
    ).scalar_one()
    if run.status in _TERMINAL_RUN_STATUSES:
        return
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


def _failure_diagnostics(cause: Exception) -> dict | None:
    """AUD-IR-CTR-001 (4.3/L14): surface safe metadata that was already
    computed before the exception was raised, instead of letting it be
    discarded at persistence time. Never attempts to recover a raw prompt
    or provider response -- those are deliberately never attached to any
    exception in this module (see errors.py)."""
    diagnostics = getattr(cause, "diagnostics", None)
    if diagnostics:
        return diagnostics
    if isinstance(cause, AIOrchestrationError):
        detail = cause.detail
        out = {
            "http_status": detail.http_status,
            "provider_error_code": detail.provider_error_code,
            "internal_detail_redacted": detail.internal_detail_redacted,
        }
        return {k: v for k, v in out.items() if v is not None} or None
    return None


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
        "diagnostics": _failure_diagnostics(cause),
    }


def _audited_provider_transport_attempted(
    exception_reported: bool,
    *,
    reservation: AIBudgetReservationRecord | None,
    usage: AIUsageRecord | None,
) -> bool:
    """Prefer persisted provider audit facts over a later node exception."""
    if exception_reported:
        return True
    if reservation is not None and bool(reservation.provider_transport_attempted):
        return True
    if usage is None:
        return False
    return bool(
        int(usage.tokens_input or 0)
        or int(usage.tokens_output or 0)
        or Decimal(usage.actual_cost or 0) > 0
    )


async def _mark_failed(
    db, run_id: UUID, *, failed_node: str | None, error_kind: str,
    reason_code: str, safe_message: str, provider_transport_attempted: bool,
    terminal_reason: str, diagnostics: dict | None = None,
):
    run = (
        await db.execute(select(AIGraphRun).where(AIGraphRun.id == run_id).with_for_update())
    ).scalar_one_or_none()
    if run is None or run.status in _TERMINAL_RUN_STATUSES:
        return
    now = _now()
    request = await db.get(AIRequestRecord, run.ai_request_id)
    request_is_scoped = request is not None and request.tenant_id == run.tenant_id
    is_chat_request = request_is_scoped and request.request_kind in {
        "FOLLOW_UP_CHAT", "CHILD_ANALYSIS", "PROPOSAL_DRAFT", "CONVERSATION_SUMMARY",
    }
    usage = None
    reservation = None
    if is_chat_request:
        usage = (await db.execute(select(AIUsageRecord).where(
            AIUsageRecord.tenant_id == run.tenant_id,
            AIUsageRecord.ai_request_id == request.id,
        ))).scalar_one_or_none()
        reservation = (await db.execute(select(AIBudgetReservationRecord).where(
            AIBudgetReservationRecord.ai_request_id == request.id
        ).with_for_update())).scalar_one_or_none()
    provider_transport_attempted = _audited_provider_transport_attempted(
        provider_transport_attempted,
        reservation=reservation,
        usage=usage,
    )
    run.status = "FAILED"
    run.failed_node = failed_node
    run.error_kind = error_kind[:80]
    run.last_error_code = reason_code[:80]
    run.last_error_safe_message = safe_message[:500]
    run.provider_transport_attempted = provider_transport_attempted
    run.terminal_reason = terminal_reason[:160]
    run.failure_diagnostics = diagnostics
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
    correlation_id = request.correlation_id if request_is_scoped else None
    if is_chat_request:
        message = (await db.execute(select(AIAnalysisMessage).where(
            AIAnalysisMessage.tenant_id == run.tenant_id,
            AIAnalysisMessage.ai_request_id == request.id,
            AIAnalysisMessage.role == "ASSISTANT",
        ).with_for_update())).scalar_one_or_none()
        if message is not None:
            if usage is not None:
                first_usage_attribution = (
                    message.tokens_input is None
                    and message.tokens_output is None
                    and message.cost_usd is None
                )
                message.tokens_input = int(usage.tokens_input)
                message.tokens_output = int(usage.tokens_output)
                message.cost_usd = Decimal(usage.actual_cost)
                if first_usage_attribution:
                    conversation = await db.get(
                        AIAnalysisConversation,
                        message.conversation_id,
                    )
                    if conversation is not None:
                        conversation.total_tokens_input = (
                            int(conversation.total_tokens_input or 0)
                            + int(usage.tokens_input)
                        )
                        conversation.total_tokens_output = (
                            int(conversation.total_tokens_output or 0)
                            + int(usage.tokens_output)
                        )
                        conversation.total_cost_usd = (
                            Decimal(str(conversation.total_cost_usd or 0))
                            + Decimal(usage.actual_cost)
                        )
                        conversation.updated_at = now
                        conversation.lock_version = int(conversation.lock_version or 0) + 1
            message.status = "BLOCKED" if error_kind == "PROVIDER_BLOCKED" else "FAILED"
            message.message_type = "ERROR_NOTICE"
            message.content = safe_message[:500]
            message.content_hash = __import__("hashlib").sha256(message.content.encode("utf-8")).hexdigest()
            message.provider_transport_attempted = provider_transport_attempted
            message.completed_at = now
            message.lock_version = int(message.lock_version or 0) + 1
        if (
            reservation is not None
            and reservation.status == "RESERVED"
            and not reservation.provider_transport_attempted
        ):
            reservation.status = "RELEASED"
            reservation.released_tokens = int(reservation.reserved_tokens or 0)
            reservation.provider_transport_attempted = False
            reservation.terminal_reason = reason_code[:160]
            reservation.released_at = now
            reservation.updated_at = now
        elif reservation is not None and reservation.status == "TRANSPORT_STARTED":
            # Transport-started reservations represent a possibly billable
            # request. They must remain auditable and must never be presented
            # as a pre-transport release.
            reservation.status = "TRANSPORT_ERROR"
            reservation.provider_transport_attempted = True
            reservation.terminal_reason = reason_code[:160]
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


async def execute_graph_run(
    run_id: UUID,
    *,
    dispatch_kind: str = AI_GRAPH_DISPATCH_START,
    interrupt_id: UUID | None = None,
    decision_id: UUID | None = None,
) -> dict:
    context = await run_db_task(
        lambda db: _acquire_run(
            db,
            run_id,
            dispatch_kind=dispatch_kind,
            interrupt_id=interrupt_id,
            decision_id=decision_id,
        ),
        celery=True,
    )
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
            if context["resume_payload"] is None:
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
                graph_runs_resumed.labels(
                    decision=context["resume_payload"].get("decision") or "unknown"
                ).inc()
                graph_input = Command(resume=context["resume_payload"])
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
    return asyncio.run(execute_graph_run(
        UUID(run_id), dispatch_kind=AI_GRAPH_DISPATCH_START,
    ))


@celery_app.task(name="app.tasks.ai_orchestration.dispatch_queued_graph_runs")
def dispatch_queued_graph_runs() -> dict:
    """Transactional outbox dispatcher for API and legacy-created graph runs."""
    async def _queued(db):
        rows = list((await db.execute(select(AIGraphRun).where(
            AIGraphRun.status == "QUEUED",
        ).order_by(AIGraphRun.created_at).with_for_update(
            skip_locked=True
        ).limit(50))).scalars().all())
        specs: list[dict[str, object]] = []
        reconciliation_required = 0
        for run in rows:
            spec, fenced = await _guarded_queued_dispatch_spec(db, run)
            reconciliation_required += int(fenced)
            if spec is not None:
                specs.append(spec)
        return specs, reconciliation_required

    specs, reconciliation_required = asyncio.run(run_db_task(_queued, celery=True))
    dispatched = sum(_enqueue_dispatch(spec) is not None for spec in specs)
    return {
        "status": "COMPLETED",
        "eligible": len(specs),
        "dispatched": dispatched,
        "reconciliation_required": reconciliation_required,
    }


@celery_app.task(name="app.tasks.ai_orchestration.resume_graph_run")
def resume_graph_run(
    run_id: str, interrupt_id: str, decision_id: str | None = None,
) -> dict:
    # During a rolling upgrade an older API may still publish the historical
    # two-argument task.  It is intentionally a fenced NOOP: the durable
    # dispatcher will publish the exact three-part command after the new API
    # and worker are active.  Never infer a decision id from the interrupt.
    if decision_id is None:
        return {"status": "NOOP", "run_id": run_id, "reason": "LEGACY_RESUME_TASK_FENCED"}
    return asyncio.run(execute_graph_run(
        UUID(run_id),
        dispatch_kind=AI_GRAPH_DISPATCH_RESUME,
        interrupt_id=UUID(interrupt_id),
        decision_id=UUID(decision_id),
    ))


@celery_app.task(name="app.tasks.ai_orchestration.recover_stale_graph_runs")
def recover_stale_graph_runs() -> dict:
    async def _recover(db):
        now = _now()
        rows = list((await db.execute(select(AIGraphRun).where(
            AIGraphRun.status == "RUNNING",
            AIGraphRun.lease_expires_at < now,
        ).with_for_update(skip_locked=True).limit(50))).scalars().all())
        recoverable: list[AIGraphRun] = []
        reconciliation_required = 0
        for run in rows:
            # A LangGraph checkpoint may already have advanced to the next
            # human gate before the application row is marked INTERRUPTED.
            # Replaying the previous RESUME command would then apply an old
            # human decision to that newer gate.  We cannot prove the
            # checkpoint position here, so fail closed and require an audited
            # reconciliation instead of ever replaying a human decision.
            if run.dispatch_kind == AI_GRAPH_DISPATCH_RESUME:
                await _mark_failed(
                    db,
                    run.id,
                    failed_node=run.current_node,
                    error_kind="GRAPH_RECONCILIATION_REQUIRED",
                    reason_code="STALE_RESUME_RECONCILIATION_REQUIRED",
                    safe_message=(
                        "The graph resume stopped after a human decision and requires "
                        "audited reconciliation; the decision was not replayed."
                    ),
                    provider_transport_attempted=bool(
                        run.provider_transport_attempted
                    ),
                    terminal_reason="FAIL_CLOSED_RECONCILIATION_REQUIRED",
                )
                reconciliation_required += 1
                continue
            if await _start_dispatch_has_interrupt_history(db, run):
                await _mark_legacy_start_reconciliation_required(db, run)
                reconciliation_required += 1
                continue
            run.status = "QUEUED"
            run.lease_owner = None
            run.lease_expires_at = None
            run.updated_at = now
            recoverable.append(run)
        specs = [
            spec for run in recoverable
            if (spec := _queued_dispatch_spec(run)) is not None
        ]
        return specs, reconciliation_required
    specs, reconciliation_required = asyncio.run(run_db_task(_recover, celery=True))
    stale_graph_leases.inc(len(specs))
    dispatched = sum(_enqueue_dispatch(spec) is not None for spec in specs)
    return {
        "status": "COMPLETED",
        "requeued": len(specs),
        "dispatched": dispatched,
        "reconciliation_required": reconciliation_required,
    }


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
        runs = list((await db.execute(select(AIGraphRun).join(
            AIGraphInterrupt,
            AIGraphInterrupt.id == AIGraphRun.dispatch_interrupt_id,
        ).where(
            AIGraphRun.status == "QUEUED",
            AIGraphRun.dispatch_kind == AI_GRAPH_DISPATCH_RESUME,
            AIGraphInterrupt.interrupt_type == "SHADOW_EVIDENCE",
            AIGraphInterrupt.status == "RESOLVED",
            AIGraphInterrupt.decision_id == AIGraphRun.dispatch_decision_id,
        ).limit(50))).scalars().all())
        return [spec for run in runs if (spec := _queued_dispatch_spec(run)) is not None]
    specs = asyncio.run(run_db_task(_ready, celery=True))
    dispatched = sum(_enqueue_dispatch(spec) is not None for spec in specs)
    return {"status": "COMPLETED", "eligible": len(specs), "dispatched": dispatched}


@celery_app.task(name="app.tasks.ai_orchestration.reset_monthly_ai_token_usage")
def reset_monthly_ai_token_usage() -> dict:
    """Zero every active provider key's tokens_used_month counter.

    tokens_used_month accumulates forever with nothing to reset it on a
    calendar boundary, so a key configured with a genuinely monthly budget
    eventually exhausts permanently instead of refreshing each month. Runs
    once at 00:05 UTC on the 1st (see celery_app.py beat_schedule).
    """
    async def _reset(db):
        keys = list((await db.execute(select(AIProviderKey).where(
            AIProviderKey.is_active.is_(True),
            AIProviderKey.tokens_used_month > 0,
        ))).scalars().all())
        for key in keys:
            key.tokens_used_month = 0
        return len(keys)
    reset_count = asyncio.run(run_db_task(_reset, celery=True))
    return {"status": "COMPLETED", "keys_reset": reset_count}
