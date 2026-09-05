"""Persisted ownership and composition identity for governed indicators."""

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func

from ..database import Base


class IndicatorRegistry(Base):
    __tablename__ = "indicator_registry"

    indicator_id = Column(Text, primary_key=True)
    alias_of = Column(
        Text,
        ForeignKey("indicator_registry.indicator_id"),
        nullable=True,
    )
    phenomenon = Column(Text, nullable=False)
    owning_layer = Column(Text, nullable=False)
    timeframe = Column(Text, nullable=False)
    producer = Column(Text, nullable=True)
    source_family = Column(Text, nullable=False)
    is_blocking = Column(Boolean, nullable=False)
    composed_inputs = Column(JSONB, nullable=False)
    contract_version = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
