from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID, JSONB
import uuid
from datetime import datetime, timezone
from ..database import Base

class Pool(Base):
    __tablename__ = 'pools'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)
    mode = Column(String(20), default='paper')  # paper, live
    market_type = Column(String(20), default='spot')  # spot, futures, tradfi
    profile_id = Column(UUID(as_uuid=True), ForeignKey('profiles.id', ondelete='SET NULL'), nullable=True)
    overrides = Column(JSONB, nullable=True, default=dict)
    autopilot_enabled = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

class PoolCoin(Base):
    __tablename__ = 'pool_coins'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pool_id = Column(UUID(as_uuid=True), ForeignKey('pools.id', ondelete='CASCADE'), nullable=False)
    symbol = Column(String(20), nullable=False)
    market_type = Column(String(10), default='spot')
    is_active = Column(Boolean, default=True)
    # Task #232 — operator approval is now split in two domains:
    #   is_approved : legacy "operator authorised collection AND trading"
    #                 kept for backwards-compat until the rolling-deploy
    #                 trigger is removed (migration N+2).
    #   is_tradable : NEW execution-only gate. ``False`` means the
    #                 collector/funnel still observe the symbol but
    #                 evaluate_signals + execute_buy must SKIP it.
    is_approved = Column(Boolean, default=False)
    is_tradable = Column(Boolean, default=False)
    added_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    origin = Column(String(20), default='manual')          # "manual" or "discovered"
    discovered_at = Column(DateTime(timezone=True), nullable=True)


class PoolAssetExclusion(Base):
    """Operator-owned denylist for automatic pool discovery.

    Deleting a coin must not be interpreted as permission for a later discovery
    run to recreate it.  A deliberate manual add clears this exclusion.
    """

    __tablename__ = "pool_asset_exclusions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pool_id = Column(UUID(as_uuid=True), ForeignKey("pools.id", ondelete="CASCADE"), nullable=False)
    symbol = Column(String(20), nullable=False)
    reason = Column(String(32), nullable=False, default="manual_removal")
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
