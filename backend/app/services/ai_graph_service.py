from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai_orchestration.langgraph.registry import resolve_graph
from ..models.ai_graph import (
    AI_GRAPH_DISPATCH_RESUME,
    AI_GRAPH_DISPATCH_START,
    AIGraphDefinition,
    AIGraphEvent,
    AIGraphInterrupt,
    AIGraphRun,
)
from ..models.systemic_ai import AIRequestRecord


PROTECTED_EDIT_FIELDS = {
    "tenant_id", "dataset_snapshot_id", "configuration_bundle_id", "base_version",
    "evidence_refs", "authority", "side_effect", "tool_side_effect_class",
}


class GraphAccessError(RuntimeError):
    pass


class AIGraphRunService:
    @staticmethod
    async def _definition(db: AsyncSession, graph_key: str) -> AIGraphDefinition:
        code_definition = resolve_graph(graph_key)
        record = (
            await db.execute(
                select(AIGraphDefinition).where(
                    AIGraphDefinition.graph_key == graph_key,
                    AIGraphDefinition.semantic_version == code_definition.semantic_version,
                    AIGraphDefinition.status == "APPROVED",
                )
            )
        ).scalar_one_or_none()
        if record is None:
            raise GraphAccessError("GRAPH_DEFINITION_NOT_DEPLOYED")
        if record.content_hash != code_definition.content_hash:
            raise GraphAccessError("GRAPH_DEFINITION_HASH_MISMATCH")
        return record

    @staticmethod
    async def create(
        db: AsyncSession,
        *,
        tenant_id: UUID,
        user_id: UUID,
        graph_key: str,
        ai_request_id: UUID,
        idempotency_key: str,
    ) -> AIGraphRun:
        existing = (
            await db.execute(
                select(AIGraphRun).where(
                    AIGraphRun.tenant_id == tenant_id,
                    AIGraphRun.idempotency_key == idempotency_key,
                )
            )
        ).scalar_one_or_none()
        if existing:
            return existing
        request = await db.get(AIRequestRecord, ai_request_id)
        if request is None or request.tenant_id != tenant_id:
            raise GraphAccessError("AI_REQUEST_NOT_FOUND")
        definition = await AIGraphRunService._definition(db, graph_key)
        run = AIGraphRun(
            tenant_id=tenant_id,
            requested_by_user_id=user_id,
            ai_request_id=ai_request_id,
            graph_definition_id=definition.id,
            thread_id=uuid4(),
            idempotency_key=idempotency_key,
            status="QUEUED",
            dispatch_kind=AI_GRAPH_DISPATCH_START,
            dispatch_interrupt_id=None,
            dispatch_decision_id=None,
            state_schema_version=definition.state_schema_version,
            authority=request.authority,
        )
        db.add(run)
        await db.flush()
        db.add(AIGraphEvent(
            tenant_id=tenant_id,
            graph_run_id=run.id,
            event_key=f"{run.id}:requested",
            event_type="REQUESTED",
            status="QUEUED",
            payload={"graph_key": graph_key, "graph_version": definition.semantic_version},
        ))
        await db.flush()
        return run

    @staticmethod
    async def get(db: AsyncSession, *, tenant_id: UUID, run_id: UUID) -> AIGraphRun:
        run = await db.get(AIGraphRun, run_id)
        if run is None or run.tenant_id != tenant_id:
            raise GraphAccessError("GRAPH_RUN_NOT_FOUND")
        return run

    @staticmethod
    async def timeline(
        db: AsyncSession, *, tenant_id: UUID, run_id: UUID, limit: int, offset: int,
    ) -> list[AIGraphEvent]:
        await AIGraphRunService.get(db, tenant_id=tenant_id, run_id=run_id)
        return list((await db.execute(
            select(AIGraphEvent).where(
                AIGraphEvent.tenant_id == tenant_id,
                AIGraphEvent.graph_run_id == run_id,
            ).order_by(AIGraphEvent.created_at, AIGraphEvent.id).offset(offset).limit(limit)
        )).scalars().all())

    @staticmethod
    async def interrupts(db: AsyncSession, *, tenant_id: UUID, run_id: UUID) -> list[AIGraphInterrupt]:
        await AIGraphRunService.get(db, tenant_id=tenant_id, run_id=run_id)
        return list((await db.execute(
            select(AIGraphInterrupt).where(
                AIGraphInterrupt.tenant_id == tenant_id,
                AIGraphInterrupt.graph_run_id == run_id,
            ).order_by(AIGraphInterrupt.created_at)
        )).scalars().all())

    @staticmethod
    async def resume(
        db: AsyncSession,
        *,
        tenant_id: UUID,
        actor_user_id: UUID,
        run_id: UUID,
        interrupt_id: UUID,
        decision: str,
        decision_id: UUID,
        idempotency_key: str,
        edits: dict[str, Any] | None,
    ) -> tuple[AIGraphRun, bool, UUID]:
        run = (
            await db.execute(select(AIGraphRun).where(
                AIGraphRun.id == run_id,
                AIGraphRun.tenant_id == tenant_id,
            ).with_for_update())
        ).scalar_one_or_none()
        if run is None:
            raise GraphAccessError("GRAPH_RUN_NOT_FOUND")
        if run.status in {"COMPLETED", "FAILED", "CANCELLED"}:
            raise GraphAccessError("GRAPH_RUN_TERMINAL")
        interrupt_record = (
            await db.execute(select(AIGraphInterrupt).where(
                AIGraphInterrupt.id == interrupt_id,
            ).with_for_update())
        ).scalar_one_or_none()
        if (
            interrupt_record is None
            or interrupt_record.tenant_id != tenant_id
            or interrupt_record.graph_run_id != run_id
        ):
            raise GraphAccessError("GRAPH_INTERRUPT_NOT_FOUND")
        expected_run_status = (
            "WAITING_SHADOW"
            if interrupt_record.interrupt_type == "SHADOW_EVIDENCE"
            else "INTERRUPTED"
        )
        if interrupt_record.idempotency_key == idempotency_key:
            if interrupt_record.decision_id is None:
                raise GraphAccessError("GRAPH_INTERRUPT_DECISION_INVALID")
            if run.status == "QUEUED":
                if (
                    run.dispatch_kind != AI_GRAPH_DISPATCH_RESUME
                    or run.dispatch_interrupt_id != interrupt_id
                    or run.dispatch_decision_id != interrupt_record.decision_id
                ):
                    raise GraphAccessError("GRAPH_RESUME_IDEMPOTENCY_CONFLICT")
            elif run.status != expected_run_status:
                raise GraphAccessError("GRAPH_RUN_INTERRUPT_STATE_MISMATCH")
            return run, True, interrupt_record.decision_id
        if run.status != expected_run_status:
            raise GraphAccessError("GRAPH_RUN_INTERRUPT_STATE_MISMATCH")
        if interrupt_record.status != "PENDING":
            raise GraphAccessError("GRAPH_INTERRUPT_ALREADY_RESOLVED")
        if decision not in {"approve", "reject", "edit"}:
            raise GraphAccessError("GRAPH_INTERRUPT_DECISION_INVALID")
        edits = edits or {}
        if decision == "edit":
            allowed = set(interrupt_record.allowed_edit_fields or [])
            if set(edits) & PROTECTED_EDIT_FIELDS or not set(edits).issubset(allowed):
                raise GraphAccessError("GRAPH_INTERRUPT_EDIT_FORBIDDEN")
        now = datetime.now(timezone.utc)
        interrupt_record.status = "REJECTED" if decision == "reject" else "RESOLVED"
        interrupt_record.decision = decision
        interrupt_record.decision_payload = {"edits": edits}
        interrupt_record.decision_id = decision_id
        interrupt_record.actor_user_id = actor_user_id
        interrupt_record.idempotency_key = idempotency_key
        interrupt_record.resolved_at = now
        run.status = "QUEUED"
        run.dispatch_kind = AI_GRAPH_DISPATCH_RESUME
        run.dispatch_interrupt_id = interrupt_id
        run.dispatch_decision_id = decision_id
        run.updated_at = now
        db.add(AIGraphEvent(
            tenant_id=tenant_id,
            graph_run_id=run_id,
            event_key=f"{run_id}:interrupt:{interrupt_id}:{decision_id}",
            event_type="RESUMED",
            status="QUEUED",
            payload={"interrupt_id": str(interrupt_id), "decision": decision},
        ))
        await db.flush()
        return run, False, decision_id

    @staticmethod
    async def cancel(db: AsyncSession, *, tenant_id: UUID, run_id: UUID, actor_user_id: UUID) -> AIGraphRun:
        run = await AIGraphRunService.get(db, tenant_id=tenant_id, run_id=run_id)
        if run.status in {"COMPLETED", "FAILED", "CANCELLED"}:
            return run
        now = datetime.now(timezone.utc)
        run.status = "CANCELLED"
        run.cancelled_at = now
        run.completed_at = now
        run.terminal_reason = "CANCELLED_BY_AUTHORIZED_ACTOR"
        run.updated_at = now
        db.add(AIGraphEvent(
            tenant_id=tenant_id,
            graph_run_id=run_id,
            event_key=f"{run_id}:cancelled",
            event_type="CANCELLED",
            status="CANCELLED",
            payload={"actor_user_id": str(actor_user_id)},
        ))
        await db.flush()
        return run
