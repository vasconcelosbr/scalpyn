"""Create pipeline watchlist rejection snapshots

Revision ID: 016_pipeline_rejected_snapshot
Revises: 015_decisions_log_pipeline
Create Date: 2026-04-22
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "016_pipeline_rejected_snapshot"
down_revision = "015_decisions_log_pipeline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pipeline_watchlist_rejections",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("watchlist_id", UUID(as_uuid=True), sa.ForeignKey("pipeline_watchlists.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("profile_id", UUID(as_uuid=True), sa.ForeignKey("profiles.id", ondelete="SET NULL"), nullable=True),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("stage", sa.String(length=10), nullable=False),
        sa.Column("failed_type", sa.String(length=20), nullable=False),
        sa.Column("failed_indicator", sa.String(length=255), nullable=False),
        sa.Column("condition_text", sa.Text(), nullable=False),
        sa.Column("current_value", JSONB(), nullable=True),
        sa.Column("expected_value", sa.String(length=255), nullable=True),
        sa.Column("evaluation_trace", JSONB(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "idx_pipeline_watchlist_rejections_watchlist_recorded_at",
        "pipeline_watchlist_rejections",
        ["watchlist_id", "recorded_at"],
    )
    op.create_index(
        "idx_pipeline_watchlist_rejections_stage",
        "pipeline_watchlist_rejections",
        ["stage"],
    )
    op.create_index(
        "idx_pipeline_watchlist_rejections_failed_type",
        "pipeline_watchlist_rejections",
        ["failed_type"],
    )


def downgrade() -> None:
    op.drop_index("idx_pipeline_watchlist_rejections_failed_type", table_name="pipeline_watchlist_rejections")
    op.drop_index("idx_pipeline_watchlist_rejections_stage", table_name="pipeline_watchlist_rejections")
    op.drop_index("idx_pipeline_watchlist_rejections_watchlist_recorded_at", table_name="pipeline_watchlist_rejections")
    op.drop_table("pipeline_watchlist_rejections")
