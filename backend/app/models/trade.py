from sqlalchemy import Column, String, DateTime, ForeignKey, Numeric, Integer
from sqlalchemy.dialects.postgresql import UUID, JSONB
import uuid
from datetime import datetime, timezone
from ..database import Base

class Trade(Base):
    __tablename__ = 'trades'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=True)
    pool_id = Column(UUID(as_uuid=True), ForeignKey('pools.id'), nullable=True)
    symbol = Column(String(20), nullable=False)
    side = Column(String(10), nullable=False)
    direction = Column(String(10), nullable=True)
    market_type = Column(String(10), nullable=False)
    exchange = Column(String(50), nullable=False)
    
    entry_price = Column(Numeric(20, 8), nullable=False)
    exit_price = Column(Numeric(20, 8), nullable=True)
    quantity = Column(Numeric(20, 8), nullable=False)
    invested_value = Column(Numeric(20, 2), nullable=False)
    profit_loss = Column(Numeric(20, 2), nullable=True)
    profit_loss_pct = Column(Numeric(10, 4), nullable=True)
    fee = Column(Numeric(20, 8), nullable=True)
    status = Column(String(20), default='open')
    
    alpha_score_at_entry = Column(Numeric(5, 2), nullable=True)
    indicators_at_entry = Column(JSONB, nullable=True)
    take_profit_price = Column(Numeric(20, 8), nullable=True)
    stop_loss_price = Column(Numeric(20, 8), nullable=True)
    
    entry_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    exit_at = Column(DateTime(timezone=True), nullable=True)
    holding_seconds = Column(Integer, nullable=True)
