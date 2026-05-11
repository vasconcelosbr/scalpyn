"""Position lifecycle model — one row per logical trade (Task #257).

Produced by ``position_lifecycle_service`` from ``exchange_executions`` via a
FIFO matching algorithm. This is the new single source of truth for the
performance dashboard at ``/dashboard/performance``.
"""

from sqlalchemy import BigInteger, Column, DateTime, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from datetime import datetime, timezone

from ..database import Base


class PositionLifecycle(Base):
    __tablename__ = "position_lifecycle"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(UUID(as_uuid=True), nullable=True)
    exchange = Column(String(20), nullable=False, default="gate")
    symbol = Column(String(40), nullable=False)
    market_type = Column(String(10), nullable=False)     # spot | futures
    direction = Column(String(10), nullable=False)       # long | short
    opened_at = Column(DateTime(timezone=True), nullable=False)
    closed_at = Column(DateTime(timezone=True), nullable=True)
    holding_seconds = Column(Integer, nullable=True)
    qty = Column(Numeric(28, 12), nullable=False)
    avg_entry = Column(Numeric(28, 12), nullable=False)
    avg_exit = Column(Numeric(28, 12), nullable=True)
    invested_usdt = Column(Numeric(28, 8), nullable=False)
    final_usdt = Column(Numeric(28, 8), nullable=True)
    fees_total = Column(Numeric(28, 8), nullable=False, default=0)
    pnl_usdt = Column(Numeric(28, 8), nullable=True)
    pnl_pct = Column(Numeric(14, 6), nullable=True)
    roi = Column(Numeric(14, 6), nullable=True)
    status = Column(String(20), nullable=False, default="open")    # open | closed | partial
    n_fills_in = Column(Integer, nullable=False, default=0)
    n_fills_out = Column(Integer, nullable=False, default=0)
    entry_trade_ids = Column(JSONB, nullable=True)
    exit_trade_ids = Column(JSONB, nullable=True)
    slippage_estimate = Column(Numeric(14, 6), nullable=True)
    maker_taker_ratio = Column(Numeric(6, 4), nullable=True)
    data_quality = Column(String(10), nullable=False, default="OK")  # OK | PARTIAL | DRIFT
    created_at = Column(DateTime(timezone=True), nullable=False,
                        default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=False,
                        default=lambda: datetime.now(timezone.utc))
