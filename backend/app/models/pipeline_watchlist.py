"""Pipeline Watchlist models — 4-level institutional funnel system.

Tables: pipeline_watchlists, pipeline_watchlist_assets, pipeline_watchlist_rejections
These are NEW tables and do NOT conflict with existing custom_watchlists.
"""

from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Numeric, Text
from sqlalchemy.dialects.postgresql import UUID, JSONB
import uuid
from datetime import datetime, timezone
from ..database import Base


class PipelineWatchlist(Base):
    """
    A watchlist in the institutional pipeline.
    Source can be either a Pool or another PipelineWatchlist (recursive).
    """
    __tablename__ = 'pipeline_watchlists'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey('users.id', ondelete='CASCADE'),
        nullable=False,
    )
    name = Column(String(100), nullable=False)
    level = Column(String(10), nullable=False, default='custom')  # POOL / L1 / L2 / L3 / custom

    # Source — one of these must be set
    source_pool_id = Column(
        UUID(as_uuid=True),
        ForeignKey('pools.id', ondelete='SET NULL'),
        nullable=True,
    )
    source_watchlist_id = Column(
        UUID(as_uuid=True),
        ForeignKey('pipeline_watchlists.id', ondelete='SET NULL'),
        nullable=True,
    )

    # Profile to apply scoring/signal analysis
    profile_id = Column(
        UUID(as_uuid=True),
        ForeignKey('profiles.id', ondelete='SET NULL'),
        nullable=True,
    )

    auto_refresh = Column(Boolean, default=True)

    # Level-specific filters — DEPRECATED at runtime.
    # All filtering is now driven exclusively by the associated Profile
    # (profile.config.filters.conditions). This field is retained for
    # backward compatibility but IGNORED during pipeline resolution.
    # Only max_stay_minutes (if present) is still used for asset expiry.
    filters_json = Column(JSONB, default=dict)

    # Pipeline scan tracking — set after every scan attempt so UI can show freshness
    last_scanned_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class PipelineWatchlistAsset(Base):
    """
    Snapshot of assets in a pipeline watchlist with live data + level tracking.
    """
    __tablename__ = 'pipeline_watchlist_assets'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    watchlist_id = Column(
        UUID(as_uuid=True),
        ForeignKey('pipeline_watchlists.id', ondelete='CASCADE'),
        nullable=False,
    )
    symbol = Column(String(20), nullable=False)

    # Live data (updated via WebSocket / refresh)
    current_price = Column(Numeric(20, 8), nullable=True)
    price_change_24h = Column(Numeric(8, 4), nullable=True)
    volume_24h = Column(Numeric(20, 8), nullable=True)
    market_cap = Column(Numeric(20, 2), nullable=True)

    # Calculated score (from profile)
    alpha_score = Column(Numeric(5, 2), nullable=True)

    # Level tracking
    entered_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    # Updated every time the pipeline scan re-confirms this asset (staleness detection)
    refreshed_at = Column(DateTime(timezone=True), nullable=True)
    previous_level = Column(String(10), nullable=True)
    level_change_at = Column(DateTime(timezone=True), nullable=True)
    level_direction = Column(String(4), nullable=True)   # "up" or "down"
    analysis_snapshot = Column(JSONB, nullable=True, default=dict)
    execution_id = Column(UUID(as_uuid=True), nullable=True)


class PipelineWatchlistRejection(Base):
    """Current rejection snapshot for a pipeline watchlist symbol."""
    __tablename__ = 'pipeline_watchlist_rejections'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    watchlist_id = Column(
        UUID(as_uuid=True),
        ForeignKey('pipeline_watchlists.id', ondelete='CASCADE'),
        nullable=False,
    )
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey('users.id', ondelete='CASCADE'),
        nullable=False,
    )
    profile_id = Column(
        UUID(as_uuid=True),
        ForeignKey('profiles.id', ondelete='SET NULL'),
        nullable=True,
    )
    symbol = Column(String(20), nullable=False)
    stage = Column(String(10), nullable=False)
    failed_type = Column(String(20), nullable=False)
    failed_indicator = Column(String(255), nullable=False)
    condition_text = Column(Text, nullable=False)
    current_value = Column(JSONB, nullable=True)
    expected_value = Column(String(255), nullable=True)
    evaluation_trace = Column(JSONB, nullable=True, default=list)
    analysis_snapshot = Column(JSONB, nullable=True, default=dict)
    execution_id = Column(UUID(as_uuid=True), nullable=True)
    recorded_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
