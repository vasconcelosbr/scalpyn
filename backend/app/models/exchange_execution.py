"""Exchange execution model — one row per raw fill from the exchange (Task #257)."""

from sqlalchemy import BigInteger, Column, DateTime, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from datetime import datetime, timezone

from ..database import Base


class ExchangeExecution(Base):
    """One row per fill returned by Gate.io's /spot/my_trades or /futures/usdt/my_trades.

    Idempotent UPSERT key is ``(exchange, market_type, trade_id)``. The raw
    payload is preserved in ``raw_payload`` so the FIFO engine can be replayed
    end-to-end without re-fetching the exchange.
    """

    __tablename__ = "exchange_executions"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(UUID(as_uuid=True), nullable=True)
    exchange = Column(String(20), nullable=False, default="gate")
    market_type = Column(String(10), nullable=False)
    trade_id = Column(String(64), nullable=False)
    order_id = Column(String(64), nullable=True)
    symbol = Column(String(40), nullable=False)
    side = Column(String(10), nullable=False)            # buy | sell
    role = Column(String(10), nullable=True)             # maker | taker
    price = Column(Numeric(28, 12), nullable=False)
    quantity = Column(Numeric(28, 12), nullable=False)
    quote_quantity = Column(Numeric(28, 8), nullable=True)
    fee = Column(Numeric(28, 12), nullable=True)
    fee_currency = Column(String(20), nullable=True)
    executed_at = Column(DateTime(timezone=True), nullable=False)
    ingested_at = Column(DateTime(timezone=True), nullable=False,
                         default=lambda: datetime.now(timezone.utc))
    raw_payload = Column(JSONB, nullable=True)

    __table_args__ = (
        UniqueConstraint("exchange", "market_type", "trade_id",
                         name="uq_exchange_executions_dedup"),
    )
