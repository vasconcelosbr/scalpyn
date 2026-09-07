"""Immutable append-only audit of profile configuration and status changes."""

from sqlalchemy import Boolean, Column, String, Text, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.types import TIMESTAMP
import uuid
from datetime import datetime, timezone
from ..database import Base


class ProfileAuditLog(Base):
    __tablename__ = "profile_audit_log"
    __table_args__ = (
        Index("idx_profile_audit_profile_created", "user_id", "profile_id", "created_at"),
        Index("idx_profile_audit_profile_id",      "profile_id", "created_at"),
    )

    id         = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id    = Column(UUID(as_uuid=True), nullable=False)
    profile_id = Column(UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False)
    changed_by = Column(UUID(as_uuid=True), ForeignKey("users.id",    ondelete="SET NULL"), nullable=True)

    change_source      = Column(String(50), nullable=True)
    change_description = Column(Text,       nullable=True)
    previous_config    = Column(JSONB,      nullable=True)
    new_config         = Column(JSONB,      nullable=True)

    previous_profile_version = Column(TIMESTAMP(timezone=True), nullable=True)
    new_profile_version      = Column(TIMESTAMP(timezone=True), nullable=True)

    # Status-only operations are deliberately separate from configuration
    # versioning.  These nullable fields keep older config audit rows valid
    # while recording the exact operational transition and human reason.
    previous_is_active = Column(Boolean, nullable=True)
    new_is_active      = Column(Boolean, nullable=True)
    status_reason      = Column(Text, nullable=True)

    created_at = Column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
