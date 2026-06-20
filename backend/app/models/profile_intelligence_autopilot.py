"""Persistence models for the Profile Intelligence Spot Auto-Pilot."""

from datetime import datetime, timezone
import uuid

from sqlalchemy import Boolean, Column, ForeignKey, Integer, Numeric, String, Text, Index
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.types import TIMESTAMP

from ..database import Base


def _now():
    return datetime.now(timezone.utc)


class ProfileIntelligenceAutopilotSettings(Base):
    __tablename__ = "profile_intelligence_autopilot_settings"

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    enabled = Column(Boolean, nullable=False, default=False)
    settings_json = Column(JSONB, nullable=False, default=dict)
    enabled_at = Column(TIMESTAMP(timezone=True), nullable=True)
    disabled_at = Column(TIMESTAMP(timezone=True), nullable=True)
    last_cycle_at = Column(TIMESTAMP(timezone=True), nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=_now)
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, default=_now)


class ProfileIntelligenceAutopilotCycle(Base):
    __tablename__ = "profile_intelligence_autopilot_cycles"
    __table_args__ = (
        Index("idx_pi_autopilot_cycles_user_window", "user_id", "window_start"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    window_start = Column(TIMESTAMP(timezone=True), nullable=False)
    idempotency_key = Column(String(180), nullable=False, unique=True)
    status = Column(String(40), nullable=False)
    checkpoint = Column(String(80), nullable=True)
    analysis_run_id = Column(UUID(as_uuid=True), ForeignKey("profile_intelligence_runs.id", ondelete="SET NULL"))
    metrics_json = Column(JSONB, nullable=False, default=dict)
    errors_json = Column(JSONB, nullable=False, default=list)
    started_at = Column(TIMESTAMP(timezone=True), nullable=False, default=_now)
    completed_at = Column(TIMESTAMP(timezone=True), nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=_now)


class ProfileIntelligenceAutopilotCandidate(Base):
    __tablename__ = "profile_intelligence_autopilot_candidates"
    __table_args__ = (
        Index("idx_pi_autopilot_candidates_user_state", "user_id", "state", "created_at"),
        Index("idx_pi_autopilot_candidates_signature", "user_id", "canonical_signature"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    cycle_id = Column(UUID(as_uuid=True), ForeignKey("profile_intelligence_autopilot_cycles.id", ondelete="SET NULL"))
    profile_id = Column(UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="RESTRICT"), nullable=False, unique=True)
    origin_profile_id = Column(UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="SET NULL"))
    previous_profile_id = Column(UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="SET NULL"))
    shadow_watchlist_id = Column(UUID(as_uuid=True), ForeignKey("pipeline_watchlists.id", ondelete="SET NULL"))
    target_watchlist_id = Column(UUID(as_uuid=True), ForeignKey("pipeline_watchlists.id", ondelete="SET NULL"))
    source_combination_id = Column(UUID(as_uuid=True), ForeignKey("profile_rule_combinations.id", ondelete="SET NULL"))
    source_suggestion_id = Column(UUID(as_uuid=True), ForeignKey("profile_suggestions.id", ondelete="SET NULL"))
    state = Column(String(40), nullable=False)
    canonical_signature = Column(String(64), nullable=False)
    canonical_rules_json = Column(JSONB, nullable=False, default=list)
    evidence_json = Column(JSONB, nullable=False, default=dict)
    version_number = Column(Integer, nullable=False, default=1)
    shadow_started_at = Column(TIMESTAMP(timezone=True), nullable=False, default=_now)
    review_after = Column(TIMESTAMP(timezone=True), nullable=True)
    observed_trades = Column(Integer, nullable=False, default=0)
    observed_win_rate = Column(Numeric(10, 6), nullable=True)
    observed_avg_pnl_pct = Column(Numeric(12, 8), nullable=True)
    promotion_win_rate = Column(Numeric(10, 6), nullable=True)
    promotion_avg_pnl_pct = Column(Numeric(12, 8), nullable=True)
    approval_status = Column(String(30), nullable=False, default="pending")
    approval_required = Column(Boolean, nullable=False, default=True)
    approved_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    approved_at = Column(TIMESTAMP(timezone=True), nullable=True)
    approval_reason = Column(Text, nullable=True)
    approval_source = Column(String(80), nullable=True)
    approval_snapshot_json = Column(JSONB, nullable=True)
    promotion_blocked_reason = Column(Text, nullable=True)
    rollback_payload = Column(JSONB, nullable=True)
    live_activation_attempted_at = Column(TIMESTAMP(timezone=True), nullable=True)
    live_activated_at = Column(TIMESTAMP(timezone=True), nullable=True)
    promoted_at = Column(TIMESTAMP(timezone=True), nullable=True)
    rejected_at = Column(TIMESTAMP(timezone=True), nullable=True)
    rollback_at = Column(TIMESTAMP(timezone=True), nullable=True)
    decision_reason = Column(Text, nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=_now)
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, default=_now)


class ProfileIntelligenceLossFamily(Base):
    __tablename__ = "profile_intelligence_loss_families"
    __table_args__ = (
        Index("idx_pi_loss_families_active", "user_id", "blocked_until"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    canonical_signature = Column(String(64), nullable=False)
    canonical_rules_json = Column(JSONB, nullable=False, default=list)
    metrics_json = Column(JSONB, nullable=False, default=dict)
    rejection_reason = Column(Text, nullable=False)
    blocked_at = Column(TIMESTAMP(timezone=True), nullable=False)
    blocked_until = Column(TIMESTAMP(timezone=True), nullable=False)
    candidate_id = Column(UUID(as_uuid=True), ForeignKey("profile_intelligence_autopilot_candidates.id", ondelete="SET NULL"))
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=_now)


class ProfileIntelligenceAutopilotAssociation(Base):
    __tablename__ = "profile_intelligence_autopilot_associations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    candidate_id = Column(UUID(as_uuid=True), ForeignKey("profile_intelligence_autopilot_candidates.id", ondelete="SET NULL"))
    watchlist_id = Column(UUID(as_uuid=True), ForeignKey("pipeline_watchlists.id", ondelete="RESTRICT"), nullable=False)
    previous_profile_id = Column(UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="SET NULL"))
    new_profile_id = Column(UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="SET NULL"))
    event_type = Column(String(30), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=_now)


class ProfileIntelligenceAutopilotReport(Base):
    __tablename__ = "profile_intelligence_autopilot_reports"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    cycle_id = Column(UUID(as_uuid=True), ForeignKey("profile_intelligence_autopilot_cycles.id", ondelete="CASCADE"), nullable=False, unique=True)
    report_json = Column(JSONB, nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=_now)


class ProfileIntelligenceAutopilotCompensation(Base):
    __tablename__ = "profile_intelligence_autopilot_compensations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    cycle_id = Column(UUID(as_uuid=True), ForeignKey("profile_intelligence_autopilot_cycles.id", ondelete="SET NULL"))
    candidate_id = Column(UUID(as_uuid=True), ForeignKey("profile_intelligence_autopilot_candidates.id", ondelete="SET NULL"))
    operation = Column(String(80), nullable=False)
    payload_json = Column(JSONB, nullable=False, default=dict)
    status = Column(String(30), nullable=False, default="PENDING")
    last_error = Column(Text, nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=_now)
    resolved_at = Column(TIMESTAMP(timezone=True), nullable=True)


class ProfileIntelligenceAutopilotAudit(Base):
    __tablename__ = "profile_intelligence_autopilot_audit"
    __table_args__ = (
        Index("idx_pi_autopilot_audit_user_created", "user_id", "created_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    actor_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    cycle_id = Column(UUID(as_uuid=True), ForeignKey("profile_intelligence_autopilot_cycles.id", ondelete="SET NULL"))
    candidate_id = Column(UUID(as_uuid=True), ForeignKey("profile_intelligence_autopilot_candidates.id", ondelete="SET NULL"))
    profile_id = Column(UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="SET NULL"))
    profile_version = Column(TIMESTAMP(timezone=True), nullable=True)
    watchlist_id = Column(UUID(as_uuid=True), ForeignKey("pipeline_watchlists.id", ondelete="SET NULL"))
    combination_id = Column(UUID(as_uuid=True), ForeignKey("profile_rule_combinations.id", ondelete="SET NULL"))
    suggestion_id = Column(UUID(as_uuid=True), ForeignKey("profile_suggestions.id", ondelete="SET NULL"))
    event_type = Column(String(80), nullable=False)
    input_metrics_json = Column(JSONB, nullable=False, default=dict)
    thresholds_json = Column(JSONB, nullable=False, default=dict)
    decision = Column(String(80), nullable=True)
    reason = Column(Text, nullable=True)
    result_json = Column(JSONB, nullable=False, default=dict)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=_now)
