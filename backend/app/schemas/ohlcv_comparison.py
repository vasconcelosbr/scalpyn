"""Operator-supplied comparison budgets; no implicit production thresholds."""
from pydantic import BaseModel, ConfigDict, Field


class OHLCVComparisonConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: bool
    window_seconds: int = Field(gt=0)
    max_rounds_per_run: int = Field(gt=0)
    max_shadow_rows: int = Field(gt=0)
    statement_timeout_ms: int = Field(gt=0)
    lock_timeout_ms: int = Field(gt=0)
    settlement_delay_seconds: int = Field(ge=0)


async def require_comparison_owner(db, user_id):
    from sqlalchemy import text
    allowed = (await db.execute(text(
        "SELECT EXISTS(SELECT 1 FROM ohlcv_comparison_owner WHERE user_id=:uid)"
    ), {"uid": user_id})).scalar()
    if not allowed:
        raise PermissionError("OHLCV comparison operator authorization required")
