"""Tenant-scoped, immutable-parent conversation records for Intelligence Runs."""

from datetime import datetime, timezone
import uuid

from sqlalchemy import Boolean, CheckConstraint, Column, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.types import BigInteger, TIMESTAMP

from ..database import Base


def _now():
    return datetime.now(timezone.utc)


class AIAnalysisConversation(Base):
    __tablename__ = "ai_analysis_conversations"
    __table_args__ = (
        UniqueConstraint("thread_id", name="uq_ai_analysis_conversation_thread"),
        Index("ix_ai_analysis_conversation_tenant_parent", "tenant_id", "parent_analysis_run_id", "created_at"),
        CheckConstraint("message_count >= 0", name="ck_ai_analysis_conversation_message_count"),
        CheckConstraint(
            "total_tokens_input >= 0 AND total_tokens_output >= 0 AND total_cost_usd >= 0",
            name="ck_ai_analysis_conversation_usage_nonnegative",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    parent_analysis_run_id = Column(
        UUID(as_uuid=True), ForeignKey("ai_graph_runs.id", ondelete="RESTRICT"), nullable=False
    )
    parent_result_id = Column(UUID(as_uuid=True), ForeignKey("ai_results.id", ondelete="RESTRICT"), nullable=False)
    thread_id = Column(String(200), nullable=False)
    title = Column(String(200))
    status = Column(String(24), nullable=False, default="ACTIVE")
    created_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    running_summary = Column(Text)
    summary_version = Column(String(40))
    summary_hash = Column(String(64))
    summarized_through_sequence = Column(Integer, nullable=False, default=0)
    message_count = Column(Integer, nullable=False, default=0)
    total_tokens_input = Column(BigInteger, nullable=False, default=0)
    total_tokens_output = Column(BigInteger, nullable=False, default=0)
    total_cost_usd = Column(Numeric(18, 8), nullable=False, default=0)
    lock_version = Column(Integer, nullable=False, default=0)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=_now)
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, default=_now, onupdate=_now)
    last_message_at = Column(TIMESTAMP(timezone=True))
    archived_at = Column(TIMESTAMP(timezone=True))


class AIAnalysisMessage(Base):
    __tablename__ = "ai_analysis_messages"
    __table_args__ = (
        UniqueConstraint("conversation_id", "sequence_number", name="uq_ai_analysis_message_sequence"),
        UniqueConstraint("tenant_id", "conversation_id", "idempotency_key", name="uq_ai_analysis_message_idempotency"),
        Index("ix_ai_analysis_message_conversation_time", "tenant_id", "conversation_id", "sequence_number"),
        Index("ix_ai_analysis_message_graph_run", "graph_run_id"),
        CheckConstraint("sequence_number > 0", name="ck_ai_analysis_message_sequence_positive"),
        CheckConstraint(
            "(tokens_input IS NULL OR tokens_input >= 0) AND "
            "(tokens_output IS NULL OR tokens_output >= 0) AND "
            "(cost_usd IS NULL OR cost_usd >= 0)",
            name="ck_ai_analysis_message_usage_nonnegative",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id = Column(
        UUID(as_uuid=True), ForeignKey("ai_analysis_conversations.id", ondelete="RESTRICT"), nullable=False
    )
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    sequence_number = Column(Integer, nullable=False)
    role = Column(String(16), nullable=False)
    message_type = Column(String(40), nullable=False)
    status = Column(String(24), nullable=False)
    content = Column(Text)
    content_hash = Column(String(64))
    parent_message_id = Column(UUID(as_uuid=True), ForeignKey("ai_analysis_messages.id", ondelete="RESTRICT"))
    idempotency_key = Column(String(160))
    request_kind = Column(String(40))
    data_mode = Column(String(40))
    answer_type = Column(String(40))
    ai_request_id = Column(UUID(as_uuid=True), ForeignKey("ai_requests.id", ondelete="RESTRICT"))
    ai_result_id = Column(UUID(as_uuid=True), ForeignKey("ai_results.id", ondelete="RESTRICT"))
    graph_run_id = Column(UUID(as_uuid=True), ForeignKey("ai_graph_runs.id", ondelete="RESTRICT"))
    child_analysis_run_id = Column(UUID(as_uuid=True), ForeignKey("ai_graph_runs.id", ondelete="RESTRICT"))
    proposal_id = Column(UUID(as_uuid=True))
    configured_provider = Column(String(40))
    configured_model = Column(String(200))
    effective_provider = Column(String(40))
    effective_model = Column(String(200))
    prompt_version_id = Column(UUID(as_uuid=True), ForeignKey("ai_prompt_versions.id", ondelete="RESTRICT"))
    evidence_refs_json = Column(JSONB, nullable=False, default=list)
    tool_call_ids_json = Column(JSONB, nullable=False, default=list)
    modules_consulted_json = Column(JSONB, nullable=False, default=list)
    warnings_json = Column(JSONB, nullable=False, default=list)
    limitations_json = Column(JSONB, nullable=False, default=list)
    suggested_questions_json = Column(JSONB, nullable=False, default=list)
    new_data_queried = Column(Boolean, nullable=False, default=False)
    provider_transport_attempted = Column(Boolean)
    tokens_input = Column(Integer)
    tokens_output = Column(Integer)
    cost_usd = Column(Numeric(18, 8))
    created_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    lock_version = Column(Integer, nullable=False, default=0)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=_now)
    completed_at = Column(TIMESTAMP(timezone=True))
    cancelled_at = Column(TIMESTAMP(timezone=True))


class AIAnalysisMessageEvidence(Base):
    __tablename__ = "ai_analysis_message_evidence"
    __table_args__ = (
        UniqueConstraint("message_id", "evidence_id", "relation_type", name="uq_ai_analysis_message_evidence"),
        Index("ix_ai_analysis_message_evidence_tenant", "tenant_id", "message_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    message_id = Column(UUID(as_uuid=True), ForeignKey("ai_analysis_messages.id", ondelete="RESTRICT"), nullable=False)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    evidence_id = Column(UUID(as_uuid=True), ForeignKey("ai_tool_evidence.id", ondelete="RESTRICT"), nullable=False)
    source_run_id = Column(UUID(as_uuid=True), ForeignKey("ai_graph_runs.id", ondelete="RESTRICT"), nullable=False)
    tool_call_id = Column(UUID(as_uuid=True), ForeignKey("ai_tool_call_audits.id", ondelete="RESTRICT"))
    relation_type = Column(String(40), nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=_now)
