"""Canonical, tenant-scoped metadata for LangGraph runs.

Checkpoint payloads live in the dedicated ``langgraph_runtime`` schema. These
models are the application authorization and audit boundary.
"""

from datetime import datetime, timezone
import uuid

from sqlalchemy import Column, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.types import BigInteger, TIMESTAMP

from ..database import Base


def _now():
    return datetime.now(timezone.utc)


class AIGraphDefinition(Base):
    __tablename__ = "ai_graph_definitions"
    __table_args__ = (
        UniqueConstraint("graph_key", "semantic_version", name="uq_ai_graph_definition_key_version"),
    )
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    graph_key = Column(String(120), nullable=False)
    semantic_version = Column(String(40), nullable=False)
    state_schema_version = Column(String(80), nullable=False)
    status = Column(String(24), nullable=False)
    content_hash = Column(String(64), nullable=False, unique=True)
    code_revision = Column(String(80), nullable=False)
    node_manifest = Column(JSONB, nullable=False)
    edge_manifest = Column(JSONB, nullable=False)
    tool_policy_version = Column(String(80), nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=_now)
    approved_at = Column(TIMESTAMP(timezone=True))
    deprecated_at = Column(TIMESTAMP(timezone=True))


class AIGraphRun(Base):
    __tablename__ = "ai_graph_runs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_ai_graph_run_tenant_idempotency"),
        Index("ix_ai_graph_run_tenant_created", "tenant_id", "created_at"),
        Index("ix_ai_graph_run_status_lease", "status", "lease_expires_at"),
        Index("ix_ai_graph_run_ai_request", "ai_request_id"),
    )
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    requested_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    ai_request_id = Column(UUID(as_uuid=True), ForeignKey("ai_requests.id", ondelete="CASCADE"), nullable=False)
    ai_job_id = Column(UUID(as_uuid=True), ForeignKey("ai_jobs.id", ondelete="SET NULL"))
    graph_definition_id = Column(UUID(as_uuid=True), ForeignKey("ai_graph_definitions.id", ondelete="RESTRICT"), nullable=False)
    thread_id = Column(UUID(as_uuid=True), nullable=False, unique=True, default=uuid.uuid4)
    checkpoint_namespace = Column(String(120), nullable=False, default="scalpyn")
    idempotency_key = Column(String(160), nullable=False)
    status = Column(String(40), nullable=False, default="QUEUED")
    current_node = Column(String(160))
    state_schema_version = Column(String(80), nullable=False)
    authority = Column(String(40), nullable=False)
    lease_owner = Column(String(160))
    lease_expires_at = Column(TIMESTAMP(timezone=True))
    heartbeat_at = Column(TIMESTAMP(timezone=True))
    started_at = Column(TIMESTAMP(timezone=True))
    completed_at = Column(TIMESTAMP(timezone=True))
    cancelled_at = Column(TIMESTAMP(timezone=True))
    terminal_reason = Column(String(160))
    last_error_code = Column(String(80))
    last_error_safe_message = Column(Text)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=_now)
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, default=_now, onupdate=_now)


class AIGraphInterrupt(Base):
    __tablename__ = "ai_graph_interrupts"
    __table_args__ = (
        UniqueConstraint("graph_run_id", "interrupt_key", name="uq_ai_graph_interrupt_run_key"),
        UniqueConstraint("graph_run_id", "idempotency_key", name="uq_ai_graph_interrupt_run_idempotency"),
        Index("ix_ai_graph_interrupt_tenant_status", "tenant_id", "status", "created_at"),
    )
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    graph_run_id = Column(UUID(as_uuid=True), ForeignKey("ai_graph_runs.id", ondelete="CASCADE"), nullable=False)
    interrupt_key = Column(String(160), nullable=False)
    interrupt_type = Column(String(80), nullable=False)
    status = Column(String(24), nullable=False, default="PENDING")
    payload = Column(JSONB, nullable=False, default=dict)
    allowed_edit_fields = Column(JSONB, nullable=False, default=list)
    decision = Column(String(24))
    decision_payload = Column(JSONB)
    decision_id = Column(UUID(as_uuid=True))
    actor_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    idempotency_key = Column(String(160))
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=_now)
    resolved_at = Column(TIMESTAMP(timezone=True))


class AIGraphEvent(Base):
    __tablename__ = "ai_graph_events"
    __table_args__ = (
        UniqueConstraint("graph_run_id", "event_key", name="uq_ai_graph_event_run_key"),
        Index("ix_ai_graph_event_run_time", "graph_run_id", "created_at", "id"),
        Index("ix_ai_graph_event_tenant_time", "tenant_id", "created_at"),
    )
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    graph_run_id = Column(UUID(as_uuid=True), ForeignKey("ai_graph_runs.id", ondelete="CASCADE"), nullable=False)
    event_key = Column(String(200), nullable=False)
    event_type = Column(String(80), nullable=False)
    node_name = Column(String(160))
    status = Column(String(40))
    payload = Column(JSONB, nullable=False, default=dict)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=_now)


class AIGraphRuntimeMetadata(Base):
    __tablename__ = "ai_graph_runtime_metadata"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    metadata_key = Column(String(160), nullable=False, unique=True)
    metadata_value = Column(JSONB, nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=_now)
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, default=_now, onupdate=_now)
