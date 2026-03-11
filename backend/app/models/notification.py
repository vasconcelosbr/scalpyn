from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Text, Time
from sqlalchemy.dialects.postgresql import UUID
import uuid
from datetime import datetime, timezone, time
from ..database import Base

class NotificationSetting(Base):
    __tablename__ = 'notification_settings'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    
    slack_webhook_url = Column(Text, nullable=True)
    slack_enabled = Column(Boolean, default=False)
    push_enabled = Column(Boolean, default=False)
    email_enabled = Column(Boolean, default=False)
    
    notify_on_buy = Column(Boolean, default=True)
    notify_on_sell = Column(Boolean, default=True)
    notify_on_stop_loss = Column(Boolean, default=True)
    notify_on_take_profit = Column(Boolean, default=True)
    notify_on_circuit_breaker = Column(Boolean, default=True)
    
    daily_summary_enabled = Column(Boolean, default=True)
    daily_summary_time = Column(Time, default=time(20, 0))
    
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
