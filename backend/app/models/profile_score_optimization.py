"""Persistence models for Profile Score Intelligence optimization evidence."""

from __future__ import annotations

import uuid

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func

from ..database import Base


class ProfileScoreOptimizationRun(Base):
    __tablename__ = "profile_score_optimization_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    status = Column(String(32), nullable=False)
    lookback_days = Column(Integer, nullable=False)
    cutoff_at = Column(DateTime(timezone=True), nullable=False)
    dataset_contract = Column(String(64), nullable=False)
    input_hash = Column(String(64), nullable=False)
    idempotency_key = Column(String(160), nullable=False)
    evidence_json = Column(JSONB, nullable=False)
    executive_report = Column(JSONB, nullable=True)
    adjustment_envelope = Column(JSONB, nullable=True)
    provider = Column(String(32), nullable=True)
    model = Column(String(120), nullable=True)
    skill_id = Column(UUID(as_uuid=True), ForeignKey("ai_skills.id", ondelete="SET NULL"), nullable=True)
    error_code = Column(String(120), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_pi_score_run_user_idempotency"),
    )


class ProfileScoreReplayResult(Base):
    __tablename__ = "profile_score_replay_results"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("profile_score_optimization_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    profile_id = Column(UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False)
    champion_profile_version_id = Column(
        UUID(as_uuid=True), ForeignKey("profile_versions.id", ondelete="RESTRICT"), nullable=False
    )
    champion_score_engine_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("score_engine_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    candidate_config_hash = Column(String(64), nullable=False)
    candidate_config = Column(JSONB, nullable=False)
    window_from = Column(DateTime(timezone=True), nullable=False)
    window_to = Column(DateTime(timezone=True), nullable=False)
    champion_metrics = Column(JSONB, nullable=False)
    challenger_metrics = Column(JSONB, nullable=False)
    delta_metrics = Column(JSONB, nullable=False)
    gates = Column(JSONB, nullable=False)
    status = Column(String(32), nullable=False)
    evidence_hash = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("run_id", "profile_id", name="uq_pi_score_replay_run_profile"),
    )


class ProfileScoreOptimizationChallenger(Base):
    __tablename__ = "profile_score_optimization_challengers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("profile_score_optimization_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    replay_result_id = Column(
        UUID(as_uuid=True),
        ForeignKey("profile_score_replay_results.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    profile_id = Column(UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False)
    champion_profile_version_id = Column(
        UUID(as_uuid=True), ForeignKey("profile_versions.id", ondelete="RESTRICT"), nullable=False
    )
    challenger_profile_version_id = Column(
        UUID(as_uuid=True), ForeignKey("profile_versions.id", ondelete="RESTRICT"), nullable=False
    )
    status = Column(String(32), nullable=False)
    validation_gate = Column(JSONB, nullable=False)
    collection_started_at = Column(DateTime(timezone=True), nullable=False)
    validated_at = Column(DateTime(timezone=True), nullable=True)
    failure_reason = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ProfileScorePerformanceDaily(Base):
    __tablename__ = "profile_score_performance_daily"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    challenger_id = Column(
        UUID(as_uuid=True),
        ForeignKey("profile_score_optimization_challengers.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    profile_id = Column(UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False)
    profile_version_id = Column(
        UUID(as_uuid=True), ForeignKey("profile_versions.id", ondelete="RESTRICT"), nullable=False
    )
    score_engine_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("score_engine_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    variant = Column(String(24), nullable=False)
    source = Column(String(32), nullable=False)
    metric_date = Column(Date, nullable=False)
    closed_trades = Column(Integer, nullable=False)
    tp = Column(Integer, nullable=False)
    sl = Column(Integer, nullable=False)
    timeout = Column(Integer, nullable=False)
    rapid_sl = Column(Integer, nullable=False)
    pnl_sum_pct = Column(Numeric(18, 8), nullable=True)
    avg_pnl_pct = Column(Numeric(18, 8), nullable=True)
    avg_mae_pct = Column(Numeric(18, 8), nullable=True)
    avg_mfe_pct = Column(Numeric(18, 8), nullable=True)
    distinct_symbols = Column(Integer, nullable=False)
    computed_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "challenger_id",
            "variant",
            "source",
            "metric_date",
            name="uq_pi_score_daily_challenger_variant_date",
        ),
    )
