"""Trade tracking model — open-trade records spawned by the Decision Log Enricher."""

from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
import uuid
from datetime import datetime, timezone

from ..database import Base


class TradeTracking(Base):
    """Lightweight open-trade record created from every ALLOW decision.

    Created by the Decision Log Enricher (Module 1).  Downstream modules
    are responsible for updating *status*, computing P&L, and confirming
    whether the entry was real or purely simulated.
    """

    __tablename__ = "trade_tracking"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    decision_id = Column(BigInteger, ForeignKey("decisions_log.id", ondelete="SET NULL"), nullable=True)

    symbol = Column(String(20), nullable=False)
    market_type = Column(String(10), nullable=False, default="spot")
    position_side = Column(String(10), nullable=False, default="long")

    is_simulated = Column(Boolean, nullable=False, default=True)

    entry_price = Column(Numeric(20, 8), nullable=False)
    entry_time = Column(DateTime(timezone=True), nullable=False)

    # Set by the Trade Reconciliation service (Module 2) when a simulated row
    # is confirmed as real.  Preserves the original decision entry_price for
    # slippage analysis (real_entry_price − entry_price = slippage).
    real_entry_price = Column(Numeric(20, 8), nullable=True)

    target_price = Column(Numeric(20, 8), nullable=True)
    stop_price = Column(Numeric(20, 8), nullable=True)

    status = Column(String(20), nullable=False, default="open")

    # Set by the Trade Reconciliation service (Module 2) when a real Gate trade
    # is matched or a new external trade is ingested.
    external_id = Column(String(100), nullable=True)

    # Set by the Trade Monitor (Module 3) when a TP / SL / timeout condition
    # is triggered.  Columns remain NULL while the trade is still open.
    exit_price = Column(Numeric(20, 8), nullable=True)
    exit_time = Column(DateTime(timezone=True), nullable=True)
    outcome = Column(String(20), nullable=True)       # 'tp' | 'sl' | 'timeout'
    pnl_pct = Column(Numeric(10, 4), nullable=True)
    holding_seconds = Column(Integer, nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
