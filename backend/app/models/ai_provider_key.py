import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Boolean, BigInteger, LargeBinary, DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from ..database import Base


class AIProviderKey(Base):
    __tablename__ = "ai_provider_keys"

    id                   = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id              = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    provider             = Column(String(50),  nullable=False)
    api_key_encrypted    = Column(LargeBinary, nullable=False)
    api_secret_encrypted = Column(LargeBinary, nullable=True)
    key_hint             = Column(String(20),  nullable=True)
    label                = Column(String(100), nullable=True)
    is_active            = Column(Boolean,     default=True,  nullable=False)
    is_validated         = Column(Boolean,     default=False, nullable=False)
    last_used_at         = Column(DateTime(timezone=True), nullable=True)
    last_tested_at       = Column(DateTime(timezone=True), nullable=True)
    test_status          = Column(String(20),  nullable=True)
    test_error           = Column(Text,        nullable=True)
    monthly_token_limit  = Column(BigInteger,  nullable=True)
    tokens_used_month    = Column(BigInteger,  default=0, nullable=False)
    created_at           = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at           = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                                  onupdate=lambda: datetime.now(timezone.utc))
