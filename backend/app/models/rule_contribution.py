"""RuleContribution — per-rule win/loss attribution cache."""

from sqlalchemy import Column, String, Integer, Numeric, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.types import TIMESTAMP
import uuid
from datetime import datetime, timezone
from ..database import Base


class RuleContribution(Base):
    __tablename__ = "rule_contribution"
    __table_args__ = (
        Index("idx_rule_contribution_profile", "user_id", "profile_id", "calculated_at"),
        Index("idx_rule_contribution_hash",    "rule_hash"),
    )

    id           = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id      = Column(UUID(as_uuid=True), nullable=False)
    profile_id   = Column(UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="SET NULL"), nullable=True)
    rule_hash    = Column(String(64), nullable=False)
    rule_type    = Column(String(30), nullable=True)
    indicator    = Column(String(60), nullable=True)
    operator     = Column(String(10), nullable=True)
    value_text   = Column(String(60), nullable=True)
    bucket_label = Column(String(60), nullable=True)

    total_cases      = Column(Integer, nullable=False, default=0)
    wins             = Column(Integer, nullable=False, default=0)
    losses           = Column(Integer, nullable=False, default=0)
    win_rate         = Column(Numeric(8, 4), nullable=True)
    avg_pnl_pct      = Column(Numeric(8, 4), nullable=True)
    avg_mae_pct      = Column(Numeric(8, 4), nullable=True)
    avg_mfe_pct      = Column(Numeric(8, 4), nullable=True)
    lift_vs_base     = Column(Numeric(8, 4), nullable=True)
    confidence_score = Column(Numeric(8, 4), nullable=True)
    extra_json       = Column(JSONB, nullable=True)

    calculated_at = Column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
