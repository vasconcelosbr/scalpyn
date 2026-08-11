"""Durable, idempotent budget reservation lifecycle for provider transport."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.systemic_ai import AIBudgetReservationRecord


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _existing(db: AsyncSession, *, tenant_id: UUID, ai_request_id: UUID) -> AIBudgetReservationRecord:
    row = (await db.execute(select(AIBudgetReservationRecord).where(
        AIBudgetReservationRecord.ai_request_id == ai_request_id,
    ))).scalar_one_or_none()
    if row is None:
        raise RuntimeError("BUDGET_RESERVATION_NOT_FOUND")
    if row.tenant_id != tenant_id:
        raise RuntimeError("BUDGET_RESERVATION_TENANT_MISMATCH")
    return row


class BudgetReservationAudit:
    @staticmethod
    async def reserve(db: AsyncSession, **values: Any) -> dict[str, Any]:
        reservation_id = uuid4()
        now = _now()
        statement = insert(AIBudgetReservationRecord).values(
            id=reservation_id,
            status="RESERVED",
            provider_transport_attempted=False,
            released_tokens=0,
            overage_tokens=0,
            currency="USD",
            created_at=now,
            updated_at=now,
            **values,
        ).on_conflict_do_nothing(
            index_elements=[AIBudgetReservationRecord.ai_request_id],
        ).returning(AIBudgetReservationRecord.id)
        created_id = (await db.execute(statement)).scalar_one_or_none()
        if created_id is not None:
            return {"id": created_id, "status": "RESERVED", "created": True}
        row = await _existing(
            db,
            tenant_id=values["tenant_id"],
            ai_request_id=values["ai_request_id"],
        )
        return {"id": row.id, "status": row.status, "created": False}

    @staticmethod
    async def mark_transport_started(
        db: AsyncSession, *, tenant_id: UUID, ai_request_id: UUID,
    ) -> None:
        now = _now()
        result = await db.execute(update(AIBudgetReservationRecord).where(
            AIBudgetReservationRecord.tenant_id == tenant_id,
            AIBudgetReservationRecord.ai_request_id == ai_request_id,
            AIBudgetReservationRecord.status == "RESERVED",
            AIBudgetReservationRecord.provider_transport_attempted.is_(False),
        ).values(
            status="TRANSPORT_STARTED",
            provider_transport_attempted=True,
            transport_started_at=now,
            updated_at=now,
        ))
        if result.rowcount != 1:
            raise RuntimeError("BUDGET_RESERVATION_TRANSPORT_START_DENIED")

    @staticmethod
    async def reconcile(
        db: AsyncSession, *, tenant_id: UUID, ai_request_id: UUID,
        reserved_tokens: int, actual_tokens: int, actual_cost_usd: Decimal,
        terminal_reason: str,
    ) -> dict[str, int]:
        released_tokens = max(reserved_tokens - actual_tokens, 0)
        overage_tokens = max(actual_tokens - reserved_tokens, 0)
        now = _now()
        result = await db.execute(update(AIBudgetReservationRecord).where(
            AIBudgetReservationRecord.tenant_id == tenant_id,
            AIBudgetReservationRecord.ai_request_id == ai_request_id,
            AIBudgetReservationRecord.status == "TRANSPORT_STARTED",
        ).values(
            status="RECONCILED",
            actual_tokens=actual_tokens,
            actual_cost_usd=actual_cost_usd,
            released_tokens=released_tokens,
            overage_tokens=overage_tokens,
            terminal_reason=terminal_reason,
            reconciled_at=now,
            updated_at=now,
        ))
        if result.rowcount != 1:
            row = await _existing(db, tenant_id=tenant_id, ai_request_id=ai_request_id)
            if (
                row.status != "RECONCILED"
                or int(row.actual_tokens or 0) != actual_tokens
                or Decimal(row.actual_cost_usd or 0) != actual_cost_usd
            ):
                raise RuntimeError("BUDGET_RESERVATION_RECONCILIATION_CONFLICT")
        return {"released_tokens": released_tokens, "overage_tokens": overage_tokens}

    @staticmethod
    async def release_before_transport(
        db: AsyncSession, *, tenant_id: UUID, ai_request_id: UUID, reason_code: str,
    ) -> None:
        now = _now()
        result = await db.execute(update(AIBudgetReservationRecord).where(
            AIBudgetReservationRecord.tenant_id == tenant_id,
            AIBudgetReservationRecord.ai_request_id == ai_request_id,
            AIBudgetReservationRecord.status == "RESERVED",
            AIBudgetReservationRecord.provider_transport_attempted.is_(False),
        ).values(
            status="RELEASED",
            released_tokens=AIBudgetReservationRecord.reserved_tokens,
            terminal_reason=reason_code,
            released_at=now,
            updated_at=now,
        ))
        if result.rowcount != 1:
            row = await _existing(db, tenant_id=tenant_id, ai_request_id=ai_request_id)
            if row.status != "RELEASED":
                raise RuntimeError("BUDGET_RESERVATION_RELEASE_CONFLICT")

    @staticmethod
    async def mark_transport_error(
        db: AsyncSession, *, tenant_id: UUID, ai_request_id: UUID, reason_code: str,
    ) -> None:
        now = _now()
        result = await db.execute(update(AIBudgetReservationRecord).where(
            AIBudgetReservationRecord.tenant_id == tenant_id,
            AIBudgetReservationRecord.ai_request_id == ai_request_id,
            AIBudgetReservationRecord.status == "TRANSPORT_STARTED",
        ).values(
            status="TRANSPORT_ERROR",
            terminal_reason=reason_code,
            updated_at=now,
        ))
        if result.rowcount != 1:
            row = await _existing(db, tenant_id=tenant_id, ai_request_id=ai_request_id)
            if row.status != "TRANSPORT_ERROR":
                raise RuntimeError("BUDGET_RESERVATION_TRANSPORT_ERROR_CONFLICT")

    @staticmethod
    async def record_zero_cost_fake(db: AsyncSession, **values: Any) -> dict[str, Any]:
        reservation_id = uuid4()
        now = _now()
        statement = insert(AIBudgetReservationRecord).values(
            id=reservation_id,
            status="RECONCILED",
            estimated_input_tokens=0,
            max_output_tokens=0,
            request_token_limit=0,
            daily_token_limit=0,
            monthly_token_limit=0,
            reserved_tokens=0,
            reserved_cost_usd=Decimal("0"),
            actual_tokens=0,
            actual_cost_usd=Decimal("0"),
            released_tokens=0,
            overage_tokens=0,
            currency="USD",
            provider_transport_attempted=False,
            terminal_reason="FAKE_PROVIDER_ZERO_COST",
            created_at=now,
            updated_at=now,
            reconciled_at=now,
            **values,
        ).on_conflict_do_nothing(
            index_elements=[AIBudgetReservationRecord.ai_request_id],
        ).returning(AIBudgetReservationRecord.id)
        created_id = (await db.execute(statement)).scalar_one_or_none()
        if created_id is not None:
            return {"id": created_id, "status": "RECONCILED", "created": True}
        row = await _existing(
            db,
            tenant_id=values["tenant_id"],
            ai_request_id=values["ai_request_id"],
        )
        if row.status != "RECONCILED" or row.provider != "fake" or row.provider_transport_attempted:
            raise RuntimeError("FAKE_BUDGET_RESERVATION_CONFLICT")
        return {"id": row.id, "status": row.status, "created": False}
