from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from .errors import AIErrorCode, fail
from .hashing import canonical_hash


class AIJobState(StrEnum):
    QUEUED = "QUEUED"
    LEASED = "LEASED"
    RUNNING = "RUNNING"
    WAITING_HUMAN = "WAITING_HUMAN"
    COMPLETED = "COMPLETED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_TERMINAL = "FAILED_TERMINAL"
    CANCELLED = "CANCELLED"
    EXPIRED_RECOVERABLE = "EXPIRED_RECOVERABLE"


class LeaseJob(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: UUID = Field(default_factory=uuid4)
    tenant_id: UUID
    purpose: str
    dedupe_key: str
    status: AIJobState = AIJobState.QUEUED
    queued_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    heartbeat_at: datetime | None = None
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    attempt: int = 0
    max_attempts: int = 3
    retry_after: datetime | None = None
    completed_at: datetime | None = None
    terminal_reason: str | None = None
    last_error_code: str | None = None
    last_error_safe_message: str | None = None

    @classmethod
    def queued(cls, *, tenant_id: UUID, purpose: str, identity: dict) -> "LeaseJob":
        return cls(tenant_id=tenant_id, purpose=purpose,
                   dedupe_key=canonical_hash({"tenant_id": str(tenant_id), "purpose": purpose, **identity}))

    def acquire(self, owner: str, *, now: datetime | None = None, lease_seconds: int = 60) -> "LeaseJob":
        now = now or datetime.now(timezone.utc)
        valid_lease = self.status in {AIJobState.LEASED, AIJobState.RUNNING} and self.lease_expires_at and self.lease_expires_at > now
        if valid_lease:
            raise fail(AIErrorCode.LEASE_EXPIRED, "A live lease already owns this job", http_status=409)
        recoverable = self.status in {AIJobState.LEASED, AIJobState.RUNNING, AIJobState.EXPIRED_RECOVERABLE}
        if self.attempt >= self.max_attempts:
            return self.model_copy(update={"status": AIJobState.FAILED_TERMINAL, "terminal_reason": "MAX_ATTEMPTS_EXCEEDED", "completed_at": now})
        return self.model_copy(update={
            "status": AIJobState.LEASED, "lease_owner": owner, "lease_expires_at": now + timedelta(seconds=lease_seconds),
            "heartbeat_at": now, "started_at": self.started_at or now, "attempt": self.attempt + 1,
            "last_error_code": AIErrorCode.STALE_JOB_RECOVERED if recoverable else None,
        })

    def heartbeat(self, owner: str, *, now: datetime | None = None, lease_seconds: int = 60) -> "LeaseJob":
        now = now or datetime.now(timezone.utc)
        if owner != self.lease_owner or not self.lease_expires_at or self.lease_expires_at <= now:
            raise fail(AIErrorCode.LEASE_EXPIRED, "The job lease is missing or expired", retryable=True, http_status=409)
        return self.model_copy(update={"status": AIJobState.RUNNING, "heartbeat_at": now, "lease_expires_at": now + timedelta(seconds=lease_seconds)})

    def terminalize(self, *, status: AIJobState, reason: str | None = None,
                    now: datetime | None = None) -> "LeaseJob":
        if status not in {AIJobState.COMPLETED, AIJobState.FAILED_TERMINAL, AIJobState.CANCELLED}:
            raise ValueError("terminalize requires a terminal state")
        return self.model_copy(update={"status": status, "terminal_reason": reason,
                                       "completed_at": now or datetime.now(timezone.utc), "lease_expires_at": None})
