from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Integer
from sqlalchemy.dialects.postgresql import UUID, BYTEA
import uuid
from datetime import datetime, timezone
from ..database import Base

class ExchangeConnection(Base):
    __tablename__ = 'exchange_connections'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    exchange_name = Column(String(50), nullable=False)
    api_key_encrypted = Column(BYTEA, nullable=False)
    api_secret_encrypted = Column(BYTEA, nullable=False)
    is_active = Column(Boolean, default=True)
    execution_priority = Column(Integer, default=1)
    last_connected_at = Column(DateTime(timezone=True), nullable=True)
    connection_status = Column(String(20), default='disconnected')
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
