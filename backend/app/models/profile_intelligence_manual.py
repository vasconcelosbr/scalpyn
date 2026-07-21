"""Persistence models for operator-controlled Profile Intelligence changes."""

from datetime import datetime, timezone
import uuid

from sqlalchemy import Boolean, Column, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.types import TIMESTAMP

from ..database import Base


class ProfileIntelligenceManualAdjustment(Base):
    __tablename__ = "profile_intelligence_manual_adjustments"
    __table_args__ = (
        Index("idx_pi_manual_user_state", "user_id", "state", "created_at"),
        Index("idx_pi_manual_profile", "profile_id", "created_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False)
    run_id = Column(UUID(as_uuid=True), nullable=True)
    indicator_stat_id = Column(UUID(as_uuid=True), nullable=True)
    profile_id = Column(UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="RESTRICT"), nullable=False)
    base_profile_version_id = Column(UUID(as_uuid=True), ForeignKey("profile_versions.id", ondelete="RESTRICT"), nullable=False)
    applied_profile_version_id = Column(UUID(as_uuid=True), ForeignKey("profile_versions.id", ondelete="RESTRICT"), nullable=True)
    rollback_profile_version_id = Column(UUID(as_uuid=True), ForeignKey("profile_versions.id", ondelete="RESTRICT"), nullable=True)
    action_type = Column(String(50), nullable=False)
    target_path = Column(Text, nullable=True)
    current_value = Column(JSONB, nullable=True)
    proposed_value = Column(JSONB, nullable=True)
    before_config = Column(JSONB, nullable=True)
    after_config = Column(JSONB, nullable=True)
    diff = Column(JSONB, nullable=True)
    evidence_json = Column(JSONB, nullable=False, default=dict)
    statistical_warnings = Column(JSONB, nullable=False, default=list)
    config_hash_before = Column(String(64), nullable=True)
    config_hash_after = Column(String(64), nullable=True)
    preview_hash = Column(String(64), nullable=True)
    state = Column(String(40), nullable=False, default="MANUAL_DRAFT")
    idempotency_key = Column(String(160), nullable=False)
    justification = Column(Text, nullable=True)
    risk_confirmed = Column(Boolean, nullable=False, default=False)
    approved_by = Column(UUID(as_uuid=True), nullable=True)
    rollback_reason = Column(Text, nullable=True)
    mutation_source = Column(String(60), nullable=False, default="MANUAL_PROFILE_INTELLIGENCE")
    autopilot_applied = Column(Boolean, nullable=False, default=False)
    ml_training_mutated = Column(Boolean, nullable=False, default=False)
    historical_dataset_mutated = Column(Boolean, nullable=False, default=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    previewed_at = Column(TIMESTAMP(timezone=True), nullable=True)
    approved_at = Column(TIMESTAMP(timezone=True), nullable=True)
    applied_at = Column(TIMESTAMP(timezone=True), nullable=True)
    rolled_back_at = Column(TIMESTAMP(timezone=True), nullable=True)


class ProfileIntelligenceManualAdjustmentEvent(Base):
    __tablename__ = "profile_intelligence_manual_adjustment_events"
    __table_args__ = (Index("idx_pi_manual_events_adjustment", "adjustment_id", "created_at"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    adjustment_id = Column(UUID(as_uuid=True), ForeignKey("profile_intelligence_manual_adjustments.id", ondelete="RESTRICT"), nullable=False)
    user_id = Column(UUID(as_uuid=True), nullable=False)
    event_type = Column(String(60), nullable=False)
    actor_user_id = Column(UUID(as_uuid=True), nullable=True)
    payload_json = Column(JSONB, nullable=False, default=dict)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
