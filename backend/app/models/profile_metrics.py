"""ProfileMetrics — daily aggregated performance cache per profile."""

from sqlalchemy import Column, String, Integer, Numeric, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.types import TIMESTAMP
import uuid
from datetime import datetime, timezone
from ..database import Base


class ProfileMetrics(Base):
    __tablename__ = "profile_metrics"
    __table_args__ = (
        Index("idx_profile_metrics_profile_period", "user_id", "profile_id", "period_end"),
        Index("idx_profile_metrics_calculated",     "user_id", "calculated_at"),
    )

    id           = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id      = Column(UUID(as_uuid=True), nullable=False)
    profile_id   = Column(UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False)
    profile_name = Column(String(255), nullable=True)
    source       = Column(String(30),  nullable=True)
    period_start = Column(TIMESTAMP(timezone=True), nullable=True)
    period_end   = Column(TIMESTAMP(timezone=True), nullable=True)

    total_trades  = Column(Integer, nullable=False, default=0)
    closed_trades = Column(Integer, nullable=False, default=0)
    open_trades   = Column(Integer, nullable=False, default=0)
    wins          = Column(Integer, nullable=False, default=0)
    losses        = Column(Integer, nullable=False, default=0)
    timeouts      = Column(Integer, nullable=False, default=0)

    win_rate                   = Column(Numeric(8, 4),  nullable=True)
    pnl_total_pct              = Column(Numeric(12, 4), nullable=True)
    avg_pnl_pct                = Column(Numeric(8, 4),  nullable=True)
    avg_holding_seconds        = Column(Numeric(12, 2), nullable=True)
    avg_winner_holding_seconds = Column(Numeric(12, 2), nullable=True)
    avg_mae_pct                = Column(Numeric(8, 4),  nullable=True)
    avg_mfe_pct                = Column(Numeric(8, 4),  nullable=True)
    tp_15m_rate                = Column(Numeric(8, 4),  nullable=True)
    tp_30m_rate                = Column(Numeric(8, 4),  nullable=True)
    tp_60m_rate                = Column(Numeric(8, 4),  nullable=True)
    confidence_level           = Column(String(20),     nullable=True)
    extra_json                 = Column(JSONB,          nullable=True)

    calculated_at = Column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
