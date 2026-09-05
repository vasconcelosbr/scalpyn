"""Persisted governance records for the observational Spot MTF pipeline."""

from __future__ import annotations

import uuid

from sqlalchemy import Column, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func

from ..database import Base


class MTFCalibrationRun(Base):
    __tablename__ = "mtf_calibration_runs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_mtf_calibration_run_key"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    config_profile_id = Column(
        UUID(as_uuid=True), ForeignKey("config_profiles.id", ondelete="RESTRICT"),
        nullable=False,
    )
    config_hash = Column(String(64), nullable=False)
    policy_version = Column(String(80), nullable=False)
    approved_policy_at = Column(DateTime(timezone=True), nullable=False)
    approved_policy_hash = Column(String(64), nullable=False)
    idempotency_key = Column(String(160), nullable=False)
    status = Column(String(32), nullable=False)
    dataset_manifest = Column(JSONB, nullable=False, default=dict)
    dataset_hash = Column(String(64), nullable=True)
    results_json = Column(JSONB, nullable=False, default=dict)
    selected_profiles = Column(JSONB, nullable=False, default=dict)
    failure_reason = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class MTFL2SetupState(Base):
    __tablename__ = "mtf_l2_setup_states"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "symbol", "profile_version_id",
            name="uq_mtf_l2_state_identity",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    symbol = Column(String(32), nullable=False)
    profile_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("profile_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    last_candle_open_at = Column(DateTime(timezone=True), nullable=False)
    last_indicators_hash = Column(String(64), nullable=False)
    state = Column(String(32), nullable=False)
    state_payload = Column(JSONB, nullable=False, default=dict)
    state_hash = Column(String(64), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), nullable=False,
        server_default=func.now(), onupdate=func.now(),
    )
