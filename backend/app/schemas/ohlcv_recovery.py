"""Explicit operator budgets and an immutable, single-identity recovery scope."""
import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import text
from ..services.pool_service import normalize_pool_symbol


class OHLCVRecoveryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: bool
    max_manifest_candles: int = Field(gt=0)
    page_candles: int = Field(gt=0, le=1000)  # Gate protocol limit
    max_pages_per_run: int = Field(gt=0)
    max_attempts_per_gap: int = Field(gt=0)
    request_spacing_seconds: int = Field(gt=0)
    retry_delay_seconds: int = Field(gt=0)
    http_timeout_seconds: int = Field(gt=0)
    statement_timeout_ms: int = Field(gt=0)
    lock_timeout_ms: int = Field(gt=0)
    dispatch_ttl_seconds: int = Field(gt=0)


class RecoveryManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    exchange: Literal["gate.io"]
    market_type: Literal["spot"]
    symbol: str
    timeframe: Literal["1m", "5m", "30m"]
    start: datetime
    end: datetime
    capture_contract_version: str = Field(min_length=1)
    finalization_delay_seconds: int = Field(ge=0, strict=True)
    reason: str = Field(min_length=1)

    @field_validator("symbol")
    @classmethod
    def symbol_key(cls, value):
        value = normalize_pool_symbol(value)
        if not re.fullmatch(r"[A-Z0-9]+_[A-Z0-9]+", value):
            raise ValueError("Invalid Gate spot symbol")
        return value

    @field_validator("start", "end")
    @classmethod
    def utc(cls, value):
        if value.tzinfo is None:
            raise ValueError("Explicit timezone required")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def aligned_range(self):
        if self.end <= self.start:
            raise ValueError("Expected nonempty [start,end) interval")
        if any(t.timestamp() % self.seconds for t in (self.start, self.end)):
            raise ValueError("Bounds must align with the exchange candle interval")
        return self

    @property
    def seconds(self):
        return {"1m": 60, "5m": 300, "30m": 1800}[self.timeframe]

    def digest(self):
        return hashlib.sha256(json.dumps(self.model_dump(mode="json"), sort_keys=True,
                                        separators=(",", ":")).encode()).hexdigest()


async def require_recovery_owner(db, user_id):
    if not (await db.execute(text(
        "SELECT EXISTS(SELECT 1 FROM ohlcv_recovery_owner WHERE user_id=:uid)"
    ), {"uid": user_id})).scalar():
        raise PermissionError("OHLCV recovery operator authorization required")
