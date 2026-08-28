"""Immutable measurement revisions for Shadow Portfolio trades."""

import uuid

from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func

from ..database import Base


class ShadowTradeMeasurementRevision(Base):
    __tablename__ = "shadow_trade_measurement_revisions"
    __table_args__ = (
        UniqueConstraint(
            "shadow_trade_id",
            "measurement_contract_version",
            "input_hash",
            name="uq_shadow_measurement_revision_input",
        ),
        CheckConstraint(
            "status <> 'READY' OR (mfe_pct >= 0 AND mae_pct <= 0 "
            "AND gross_return_pct IS NOT NULL "
            "AND mae_pct <= gross_return_pct AND gross_return_pct <= mfe_pct)",
            name="ck_shadow_measurement_ready_extrema",
        ),
        Index("ix_shadow_measurement_trade_created", "shadow_trade_id", "created_at"),
        Index("ix_shadow_measurement_status_created", "status", "created_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    shadow_trade_id = Column(
        UUID(as_uuid=True),
        ForeignKey("shadow_trades.id", ondelete="RESTRICT"),
        nullable=False,
    )
    measurement_contract_version = Column(String(80), nullable=False)
    status = Column(String(20), nullable=False)
    method = Column(String(80), nullable=False)
    source = Column(String(40), nullable=False)
    timeframe = Column(String(16), nullable=True)
    resolution_seconds = Column(Integer, nullable=True)
    input_hash = Column(String(64), nullable=False)
    input_snapshot = Column(JSONB, nullable=False)

    legacy_entry_price = Column(Numeric(24, 12), nullable=True)
    entry_price_reference = Column(Numeric(24, 12), nullable=True)
    entry_price_observed = Column(Numeric(24, 12), nullable=True)
    entry_price_realized = Column(Numeric(24, 12), nullable=True)
    entry_price_source_at = Column(DateTime(timezone=True), nullable=True)
    entry_price_lag_seconds = Column(Numeric(16, 6), nullable=True)
    entry_quality = Column(String(24), nullable=False)

    legacy_mae_pct = Column(Numeric(18, 9), nullable=True)
    legacy_mfe_pct = Column(Numeric(18, 9), nullable=True)
    legacy_mae_at = Column(DateTime(timezone=True), nullable=True)
    legacy_mfe_at = Column(DateTime(timezone=True), nullable=True)
    mae_pct = Column(Numeric(18, 9), nullable=True)
    mfe_pct = Column(Numeric(18, 9), nullable=True)
    mae_at = Column(DateTime(timezone=True), nullable=True)
    mfe_at = Column(DateTime(timezone=True), nullable=True)
    entry_boundary_partial = Column(Boolean, nullable=False)
    exit_boundary_partial = Column(Boolean, nullable=False)

    exit_price_nominal = Column(Numeric(24, 12), nullable=True)
    exit_price_observed = Column(Numeric(24, 12), nullable=True)
    exit_price_semantics = Column(String(40), nullable=True)
    barrier_overshoot_pct = Column(Numeric(18, 9), nullable=True)
    # Nullable at the schema boundary so append-only historical v1 revisions
    # remain untouched; every v2 writer supplies all three fields.
    mfe_mae_source = Column(String(40), nullable=True)
    mfe_mae_recomputed_at = Column(DateTime(timezone=True), nullable=True)
    mfe_mae_method_version = Column(String(80), nullable=True)

    gross_return_pct = Column(Numeric(18, 9), nullable=True)
    fee_roundtrip_pct_applied = Column(Numeric(18, 9), nullable=True)
    net_return_pct = Column(Numeric(18, 9), nullable=True)
    cost_contract_version = Column(String(80), nullable=False)
    unavailable_reason = Column(String(160), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
