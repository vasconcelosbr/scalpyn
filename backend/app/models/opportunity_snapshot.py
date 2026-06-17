"""OpportunitySnapshot — every asset evaluated at the L3 gate, approved or not."""

from sqlalchemy import Column, String, Integer, ForeignKey, Index, Numeric
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY
from sqlalchemy.types import TIMESTAMP
import uuid
from datetime import datetime, timezone
from ..database import Base


class OpportunitySnapshot(Base):
    __tablename__ = "opportunity_snapshots"
    __table_args__ = (
        Index("idx_opp_snap_user_created",    "user_id", "created_at"),
        Index("idx_opp_snap_symbol_created",  "symbol",  "created_at"),
        Index("idx_opp_snap_user_symbol_created", "user_id", "symbol", "created_at"),
    )

    id             = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id        = Column(UUID(as_uuid=True), nullable=False)
    symbol         = Column(String(30), nullable=False)
    watchlist_id   = Column(UUID(as_uuid=True), nullable=True)
    execution_id   = Column(String(64), nullable=True)
    source         = Column(String(30), nullable=False, default="L3_GATE")
    timeframe      = Column(String(10), nullable=True)
    price          = Column(Numeric, nullable=True)
    features_json  = Column(JSONB, nullable=False, default=dict)
    profiles_evaluated = Column(ARRAY(UUID(as_uuid=True)), nullable=True)
    profiles_approved  = Column(ARRAY(UUID(as_uuid=True)), nullable=True)
    profiles_rejected  = Column(ARRAY(UUID(as_uuid=True)), nullable=True)
    rejection_reasons  = Column(JSONB, nullable=True)
    active_profiles_result_json = Column(JSONB, nullable=True)

    # Future outcome fields — populated by a background job after the trade closes
    future_outcome             = Column(String(20), nullable=True)
    future_pnl_pct             = Column(Numeric, nullable=True)
    future_time_to_tp_seconds  = Column(Integer, nullable=True)
    future_time_to_sl_seconds  = Column(Integer, nullable=True)
    future_mae_pct             = Column(Numeric, nullable=True)
    future_mfe_pct             = Column(Numeric, nullable=True)
    future_evaluated_at        = Column(TIMESTAMP(timezone=True), nullable=True)

    created_at = Column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
