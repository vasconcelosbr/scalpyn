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
    #   "default_timeframe": "5m",     # profile-level default timeframe
    #   "filters": {
    #     "logic": "AND" | "OR",
    #     "conditions": [
    #       {"field": "volume_24h", "operator": ">", "value": 10000000},
    #       {"field": "rsi", "operator": ">", "value": 50, "timeframe": "15m", "period": 14}
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
    #       {"field": "adx", "operator": ">", "value": 25, "required": true, "timeframe": "5m", "period": 14},
    #       {"field": "rsi", "operator": "<", "value": 45, "required": false}
    #     ]
    #   },
    #   "block_rules": {
    #     "blocks": [
    #       {"id": "...", "indicator": "rsi", "operator": ">", "value": 80, "timeframe": "15m", "period": 14}
    #     ]
    #   },
    #   "entry_triggers": {
    #     "logic": "AND",
    #     "conditions": [
    #       {"indicator": "volume_spike", "operator": ">=", "value": 2, "timeframe": "1m", "period": 7}
    #     ]
    #   }
    # }
    config = Column(JSONB, nullable=False, default=dict)

    # ── Preset IA + Auto-Pilot fields ─────────────────────────────────────────
    profile_role    = Column(String(50),  nullable=True)   # universe_filter | primary_filter | score_engine | acquisition_queue
    pipeline_order  = Column(String(3),   nullable=False, default="99")  # "0","1","2","3"
    pipeline_label  = Column(String(100), nullable=True)
    auto_pilot_enabled = Column(Boolean,  default=False)
    auto_pilot_config  = Column(JSONB,    nullable=False, default=dict)
    preset_ia_last_run = Column(DateTime(timezone=True), nullable=True)
    preset_ia_config   = Column(JSONB,    nullable=True)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class WatchlistProfile(Base):
    """
    Junction table linking watchlists to profiles.
    Allows a watchlist to have separate profiles for L2 (Ranking) and L3 (Signals).
    """
    __tablename__ = 'watchlist_profiles'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    watchlist_id = Column(String(100), nullable=False)  # Custom watchlist ID
    profile_type = Column(String(10), nullable=False, default="L2")  # "L2" or "L3"
    profile_id = Column(UUID(as_uuid=True), ForeignKey('profiles.id', ondelete='CASCADE'), nullable=True)
    is_enabled = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
