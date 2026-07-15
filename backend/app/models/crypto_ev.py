from sqlalchemy import BigInteger, Boolean, CheckConstraint, Column, DateTime, ForeignKey, Integer, Numeric, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func

from ..database import Base


class CryptoEVL3ReplayFlag(Base):
    __tablename__ = "crypto_ev_l3_replay_flags"

    shadow_trade_id = Column(UUID(as_uuid=True), ForeignKey("shadow_trades.id", ondelete="CASCADE"), primary_key=True)
    computed_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    would_pass_l3 = Column(Boolean, nullable=True)
    replay_status = Column(Text, nullable=False)
    l3_config_version = Column(Text, nullable=False)
    replay_reason = Column(Text, nullable=False)
    replay_details = Column(JSONB, nullable=False, default=dict)


class CryptoEVSnapshot(Base):
    __tablename__ = "crypto_ev_snapshots"
    __table_args__ = (
        CheckConstraint("view IN ('executable','spectrum')", name="ck_crypto_ev_snapshots_view"),
        CheckConstraint(
            "state IN ('FAVORABLE','NEUTRAL','RISKY','AVOID','INSUFFICIENT_DATA')",
            name="ck_crypto_ev_snapshots_state",
        ),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    computed_at = Column(DateTime(timezone=True), primary_key=True, nullable=False, server_default=func.now())
    symbol = Column(Text, nullable=False)
    view = Column(Text, nullable=False)
    window_hours = Column(Integer, nullable=False)
    n_trades = Column(Integer, nullable=False)
    n_excluded_no_pnl = Column(Integer, nullable=False, default=0)
    n_excluded_unreplayable = Column(Integer, nullable=False, default=0)
    ev_symbol = Column(Numeric, nullable=True)
    ev_prior = Column(Numeric, nullable=False)
    atr_bucket = Column(Text, nullable=False)
    shrinkage_k = Column(Integer, nullable=False)
    w = Column(Numeric, nullable=False)
    ev_shrunk = Column(Numeric, nullable=False)
    score = Column(Numeric, nullable=False)
    state = Column(Text, nullable=False)
    ml_component_applied = Column(Boolean, nullable=False, default=False)
    ml_component_value = Column(Numeric, nullable=True)
    ml_model_version = Column(Text, nullable=True)
    config_version = Column(Text, nullable=False)
    l3_config_version = Column(Text, nullable=True)
    audit_json = Column(JSONB, nullable=False, default=dict)
