"""Persistence models for the Profile Intelligence operational Co-Pilot."""

from datetime import datetime, timezone
import uuid

from sqlalchemy import Boolean, Column, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.types import TIMESTAMP

from ..database import Base


def _now():
    return datetime.now(timezone.utc)


class CopilotSession(Base):
    __tablename__ = "copilot_sessions"
    __table_args__ = (Index("idx_copilot_sessions_user_started", "user_id", "started_at"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    started_at = Column(TIMESTAMP(timezone=True), nullable=False, default=_now)
    ended_at = Column(TIMESTAMP(timezone=True), nullable=True)
    context = Column(JSONB, nullable=False, default=dict)
    status = Column(String(20), nullable=False, default="ACTIVE")


class CopilotMessage(Base):
    __tablename__ = "copilot_messages"
    __table_args__ = (Index("idx_copilot_messages_session_created", "session_id", "created_at"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(UUID(as_uuid=True), ForeignKey("copilot_sessions.id", ondelete="CASCADE"), nullable=False)
    role = Column(String(20), nullable=False)
    content = Column(Text, nullable=False)
    message_metadata = Column("metadata", JSONB, nullable=False, default=dict)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=_now)

class CopilotQueryRun(Base):
    __tablename__ = "copilot_query_runs"
    __table_args__ = (
        Index("idx_copilot_query_runs_user_created", "user_id", "created_at"),
        Index("idx_copilot_query_runs_session", "session_id", "created_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    session_id = Column(UUID(as_uuid=True), ForeignKey("copilot_sessions.id", ondelete="SET NULL"), nullable=True)
    query_text = Column(Text, nullable=False)
    query_hash = Column(String(64), nullable=False)
    query_type = Column(String(30), nullable=False)
    reason = Column(Text, nullable=True)
    parameters = Column(JSONB, nullable=False, default=dict)
    status = Column(String(30), nullable=False)
    rows_returned = Column(Integer, nullable=True)
    execution_ms = Column(Integer, nullable=True)
    result_preview = Column(JSONB, nullable=True)
    result_truncated = Column(Boolean, nullable=False, default=False)
    error = Column(Text, nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=_now)


class CopilotActionPlan(Base):
    __tablename__ = "copilot_action_plans"
    __table_args__ = (Index("idx_copilot_actions_user_status", "user_id", "status", "created_at"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    session_id = Column(UUID(as_uuid=True), ForeignKey("copilot_sessions.id", ondelete="SET NULL"), nullable=True)
    action_type = Column(String(80), nullable=False)
    target_type = Column(String(60), nullable=False)
    target_id = Column(String(100), nullable=True)
    objective = Column(Text, nullable=False)
    evidence = Column(JSONB, nullable=False, default=dict)
    proposed_diff = Column(JSONB, nullable=False, default=list)
    execution_payload = Column(JSONB, nullable=False, default=dict)
    risk_assessment = Column(Text, nullable=True)
    rollback_plan = Column(JSONB, nullable=False, default=dict)
    target_state_hash = Column(String(64), nullable=True)
    status = Column(String(30), nullable=False, default="DRY_RUN")
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=_now)
    approved_at = Column(TIMESTAMP(timezone=True), nullable=True)
    approved_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    approval_text = Column(String(80), nullable=True)
    executed_at = Column(TIMESTAMP(timezone=True), nullable=True)
    execution_result = Column(JSONB, nullable=True)


class CopilotSkill(Base):
    __tablename__ = "copilot_skills"
    __table_args__ = (
        UniqueConstraint("user_id", "name", "version", name="uq_copilot_skill_user_name_version"),
        Index("idx_copilot_skills_retrieval", "user_id", "status", "skill_type"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(160), nullable=False)
    skill_type = Column(String(50), nullable=False)
    content = Column(Text, nullable=False)
    skill_metadata = Column("metadata", JSONB, nullable=False, default=dict)
    version = Column(Integer, nullable=False, default=1)
    status = Column(String(30), nullable=False, default="ACTIVE")
    confidence = Column(Numeric(5, 4), nullable=True)
    source = Column(String(160), nullable=True)
    requires_approval = Column(Boolean, nullable=False, default=False)
    approved_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    approved_at = Column(TIMESTAMP(timezone=True), nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=_now)
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, default=_now)


class CopilotAuditLog(Base):
    __tablename__ = "copilot_audit_logs"
    __table_args__ = (Index("idx_copilot_audit_user_created", "user_id", "created_at"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    session_id = Column(UUID(as_uuid=True), ForeignKey("copilot_sessions.id", ondelete="SET NULL"), nullable=True)
    event_type = Column(String(80), nullable=False)
    actor_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    action_plan_id = Column(UUID(as_uuid=True), ForeignKey("copilot_action_plans.id", ondelete="SET NULL"), nullable=True)
    payload = Column(JSONB, nullable=False, default=dict)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=_now)
