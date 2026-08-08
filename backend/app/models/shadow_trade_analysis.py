"""Persistent report snapshots and AI analysis jobs for Shadow Portfolio."""

from datetime import datetime, timezone
import uuid

from sqlalchemy import Column, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.types import TIMESTAMP

from ..database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ShadowTradeReportRun(Base):
    __tablename__ = "shadow_trade_report_runs"
    __table_args__ = (
        Index("idx_shadow_report_runs_user_created", "user_id", "created_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    filters = Column(JSONB, nullable=False, default=dict)
    filters_hash = Column(String(64), nullable=False)
    trade_ids_hash = Column(String(64), nullable=False)
    timezone = Column(String(80), nullable=False, default="UTC")
    total_trades = Column(Integer, nullable=False, default=0)
    status = Column(String(30), nullable=False, default="READY")
    completeness = Column(JSONB, nullable=False, default=dict)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=_now)


class ShadowTradeReportItem(Base):
    __tablename__ = "shadow_trade_report_items"
    __table_args__ = (
        UniqueConstraint("report_run_id", "shadow_trade_id", name="uq_shadow_report_item_trade"),
        UniqueConstraint("report_run_id", "position", name="uq_shadow_report_item_position"),
        Index("idx_shadow_report_items_run_position", "report_run_id", "position"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    report_run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("shadow_trade_report_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    shadow_trade_id = Column(
        UUID(as_uuid=True),
        ForeignKey("shadow_trades.id", ondelete="CASCADE"),
        nullable=False,
    )
    position = Column(Integer, nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=_now)


class ShadowTradeAnalysisJob(Base):
    __tablename__ = "shadow_trade_analysis_jobs"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_shadow_analysis_user_idempotency"),
        Index("idx_shadow_analysis_jobs_user_created", "user_id", "created_at"),
        Index("idx_shadow_analysis_jobs_status", "status", "created_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    tenant_id = Column(UUID(as_uuid=True), nullable=True)
    ai_request_id = Column(UUID(as_uuid=True), nullable=True)
    scope = Column(String(30), nullable=False)  # TRADE | REPORT
    shadow_trade_id = Column(
        UUID(as_uuid=True),
        ForeignKey("shadow_trades.id", ondelete="SET NULL"),
        nullable=True,
    )
    report_run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("shadow_trade_report_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    provider = Column(String(40), nullable=False)
    model = Column(String(160), nullable=False)
    prompt_version = Column(String(40), nullable=False, default="shadow-analysis-v1")
    input_hash = Column(String(64), nullable=False)
    idempotency_key = Column(String(64), nullable=False)
    status = Column(String(30), nullable=False, default="PENDING")
    result_json = Column(JSONB, nullable=True)
    raw_response = Column(Text, nullable=True)
    usage = Column(JSONB, nullable=False, default=dict)
    error = Column(Text, nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=_now)
    started_at = Column(TIMESTAMP(timezone=True), nullable=True)
    heartbeat_at = Column(TIMESTAMP(timezone=True), nullable=True)
    lease_owner = Column(String(160), nullable=True)
    lease_expires_at = Column(TIMESTAMP(timezone=True), nullable=True)
    attempt = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=3)
    retry_after = Column(TIMESTAMP(timezone=True), nullable=True)
    completed_at = Column(TIMESTAMP(timezone=True), nullable=True)
    terminal_reason = Column(String(160), nullable=True)
    last_error_code = Column(String(80), nullable=True)
    last_error_safe_message = Column(Text, nullable=True)
