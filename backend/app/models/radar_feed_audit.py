"""Seven-day receipt evidence, independent of trading authorization."""
import uuid
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Index
from sqlalchemy.dialects.postgresql import UUID
from ..database import Base


class RadarFeedReceipt(Base):
    __tablename__ = "radar_feed_receipts"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pool_id = Column(UUID(as_uuid=True), ForeignKey("pools.id", ondelete="CASCADE"), nullable=False)
    received_at = Column(DateTime(timezone=True), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    status = Column(String(32), nullable=False)
    source_count = Column(Integer, nullable=False)
    reason = Column(String(80), nullable=True)
    # Which feed produced the receipt: 'radar' (Market Catalyst) or 'pump_monitor'.
    source_type = Column(String(32), nullable=False, default="radar", server_default="radar")
    __table_args__ = (Index("ix_radar_receipt_pool_time", "pool_id", "received_at"),)


class RadarFeedItem(Base):
    __tablename__ = "radar_feed_items"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    receipt_id = Column(UUID(as_uuid=True), ForeignKey("radar_feed_receipts.id", ondelete="CASCADE"), nullable=False, index=True)
    symbol = Column(String(120), nullable=False)
    source_updated_at = Column(DateTime(timezone=True), nullable=True)
    pool_result = Column(String(32), nullable=False)
