from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID, JSONB
import uuid
from datetime import datetime, timezone
from ..database import Base

class ConfigProfile(Base):
    __tablename__ = 'config_profiles'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    pool_id = Column(UUID(as_uuid=True), ForeignKey('pools.id', ondelete='CASCADE'), nullable=True)
    config_type = Column(String(50), nullable=False)
    config_json = Column(JSONB, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

class ConfigAuditLog(Base):
    __tablename__ = 'config_audit_log'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    config_id = Column(UUID(as_uuid=True), ForeignKey('config_profiles.id'), nullable=True)
    changed_by = Column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=True)
    previous_json = Column(JSONB, nullable=True)
    new_json = Column(JSONB, nullable=False)
    change_description = Column(Text, nullable=True)
    changed_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
