"""Immutable point-in-time social-intelligence observations."""

import uuid

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from ..database import Base


class SocialIntelligenceRun(Base):
    __tablename__ = "social_intelligence_runs"
    __table_args__ = (
        UniqueConstraint("source", "external_run_id", name="uq_social_runs_source_external"),
        CheckConstraint("window_start < window_end", name="ck_social_runs_window_order"),
        CheckConstraint("window_end <= collected_at", name="ck_social_runs_collected_after_window"),
        Index("ix_social_runs_window_end", "window_end"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    contract_version = Column(String(64), nullable=False)
    external_run_id = Column(String(128), nullable=False)
    source = Column(String(64), nullable=False)
    model = Column(String(128), nullable=False)
    prompt_version = Column(String(128), nullable=False)
    window_start = Column(DateTime(timezone=True), nullable=False)
    window_end = Column(DateTime(timezone=True), nullable=False)
    collected_at = Column(DateTime(timezone=True), nullable=False)
    payload_hash = Column(String(64), nullable=False)
    status = Column(String(20), nullable=False)
    accepted_count = Column(Integer, nullable=False, default=0)
    rejected_count = Column(Integer, nullable=False, default=0)
    validation_errors = Column(JSONB, nullable=False, default=list)
    received_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class SocialAssetObservation(Base):
    __tablename__ = "social_asset_observations"
    __table_args__ = (
        UniqueConstraint("run_id", "symbol", name="uq_social_observations_run_symbol"),
        CheckConstraint("attention_score >= 0 AND attention_score <= 100", name="ck_social_attention_range"),
        CheckConstraint("sentiment_score >= 0 AND sentiment_score <= 100", name="ck_social_sentiment_range"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_social_confidence_range"),
        Index("ix_social_observations_symbol_window", "symbol", "window_end"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("social_intelligence_runs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    symbol = Column(String(20), nullable=False)
    attention_score = Column(Float, nullable=False)
    sentiment_score = Column(Float, nullable=False)
    confidence = Column(Float, nullable=False)
    sentiment_label = Column(String(32), nullable=False)
    recommendation = Column(String(32), nullable=False)
    summary = Column(Text, nullable=False)
    narratives = Column(JSONB, nullable=False, default=list)
    anomalies = Column(JSONB, nullable=False, default=list)
    metrics = Column(JSONB, nullable=False, default=dict)
    sources = Column(JSONB, nullable=False)
    contract_version = Column(String(64), nullable=False)
    window_start = Column(DateTime(timezone=True), nullable=False)
    window_end = Column(DateTime(timezone=True), nullable=False)
    collected_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
