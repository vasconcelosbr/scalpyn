from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID, uuid4

from .errors import AIErrorCode, fail


@dataclass(frozen=True)
class BudgetPolicy:
    tenant_id: UUID
    provider: str
    model: str | None
    module: str | None
    daily_token_limit: int | None
    monthly_token_limit: int | None
    request_token_limit: int
    production_null_limit_policy: str = "DENY"


@dataclass(frozen=True)
class BudgetReservation:
    id: UUID
    estimated_tokens: int
    estimated_cost: Decimal
    remaining_tokens: int


class BudgetService:
    def admit(self, policy: BudgetPolicy, *, estimated_input: int, estimated_output: int,
              used_today: int, used_month: int, estimated_cost: Decimal = Decimal("0"),
              environment: str = "production") -> BudgetReservation:
        estimated = estimated_input + estimated_output
        if estimated > policy.request_token_limit:
            raise fail(AIErrorCode.BUDGET_DENIED, "Request token estimate exceeds its limit", http_status=429)
        if environment != "development" and policy.monthly_token_limit is None and policy.production_null_limit_policy == "DENY":
            raise fail(AIErrorCode.BUDGET_DENIED, "An explicit monthly AI budget is required", http_status=429)
        if policy.daily_token_limit is not None and used_today + estimated > policy.daily_token_limit:
            raise fail(AIErrorCode.BUDGET_DENIED, "Daily AI budget would be exceeded", http_status=429)
        if policy.monthly_token_limit is not None and used_month + estimated > policy.monthly_token_limit:
            raise fail(AIErrorCode.BUDGET_DENIED, "Monthly AI budget would be exceeded", http_status=429)
        ceiling = policy.monthly_token_limit if policy.monthly_token_limit is not None else policy.request_token_limit
        return BudgetReservation(uuid4(), estimated, estimated_cost, ceiling - used_month - estimated)

    @staticmethod
    def reconcile(reservation: BudgetReservation, *, actual_input: int, actual_output: int,
                  actual_cost: Decimal) -> dict:
        actual = actual_input + actual_output
        return {"reservation_id": reservation.id, "reserved_tokens": reservation.estimated_tokens,
                "actual_tokens": actual, "released_tokens": max(reservation.estimated_tokens - actual, 0),
                "overage_tokens": max(actual - reservation.estimated_tokens, 0), "actual_cost": actual_cost}
