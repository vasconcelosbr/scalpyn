"""Profile model — strategy definition layer for dynamic trading configurations."""

from sqlalchemy import Column, String, Boolean, DateTime, Text, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
import uuid
from datetime import datetime, timezone
from ..database import Base


class Profile(Base):
    """
    Profile represents a complete strategy configuration.
    
    Contains:
    - Filters (L1): Asset filtering conditions
    - Scoring: Custom Alpha Score weights
    - Signals: Entry condition definitions
    """
    __tablename__ = 'profiles'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)
    
    # Full strategy configuration as JSONB
    # Structure:
    # {
    #   "filters": {
    #     "logic": "AND" | "OR",
    #     "conditions": [
    #       {"field": "volume_24h", "operator": ">", "value": 10000000},
    #       {"field": "atr_percent", "operator": ">", "value": 0.5}
    #     ]
    #   },
    #   "scoring": {
    #     "weights": {
    #       "liquidity": 30,
    #       "market_structure": 20,
    #       "momentum": 30,
    #       "signal": 20
    #     }
    #   },
    #   "signals": {
    #     "logic": "AND",
    #     "conditions": [
    #       {"field": "adx", "operator": ">", "value": 25, "required": true},
    #       {"field": "rsi", "operator": "<", "value": 45, "required": false}
    #     ]
    #   }
    # }
    config = Column(JSONB, nullable=False, default=dict)
    
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class WatchlistProfile(Base):
    """
    Junction table linking watchlists to profiles.
    Allows a watchlist to have an active profile for filtering/scoring.
    """
    __tablename__ = 'watchlist_profiles'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    watchlist_id = Column(String(100), nullable=False)  # Can be "default" or custom watchlist ID
    profile_id = Column(UUID(as_uuid=True), ForeignKey('profiles.id', ondelete='CASCADE'), nullable=True)
    is_enabled = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
