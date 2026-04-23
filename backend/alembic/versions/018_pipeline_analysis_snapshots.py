"""Add normalized analysis_snapshot payloads for pipeline watchlists

Revision ID: 018_pipeline_analysis_snapshots
Revises: 017_pipeline_execution_consistency
Create Date: 2026-04-23
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "018_pipeline_analysis_snapshots"
down_revision = "017_pipeline_execution_consistency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pipeline_watchlist_assets",
        sa.Column("analysis_snapshot", JSONB(), nullable=True, server_default=sa.text("'{}'::jsonb")),
    )
    op.add_column(
        "pipeline_watchlist_rejections",
        sa.Column("analysis_snapshot", JSONB(), nullable=True, server_default=sa.text("'{}'::jsonb")),
    )
    op.execute(sa.text("ALTER TABLE pipeline_watchlist_assets ALTER COLUMN analysis_snapshot DROP DEFAULT"))
    op.execute(sa.text("ALTER TABLE pipeline_watchlist_rejections ALTER COLUMN analysis_snapshot DROP DEFAULT"))


def downgrade() -> None:
    op.drop_column("pipeline_watchlist_rejections", "analysis_snapshot")
    op.drop_column("pipeline_watchlist_assets", "analysis_snapshot")
