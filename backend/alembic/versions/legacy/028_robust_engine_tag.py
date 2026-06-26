"""Add engine_tag columns for Robust Indicators Phase 2 gradual rollout.

Revision ID: 028_robust_engine_tag
Revises: 027_indicator_snapshots
Create Date: 2026-05-01
"""

from alembic import op
import sqlalchemy as sa

revision = "028_robust_engine_tag"
down_revision = "027_indicator_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Per-statement cap; complements the session-wide lock_timeout in env.py.
    op.execute(sa.text("SET LOCAL lock_timeout = '5s'"))
    op.execute(sa.text("""
        ALTER TABLE pipeline_watchlist_assets
            ADD COLUMN IF NOT EXISTS engine_tag VARCHAR(16)
    """))
    op.execute(sa.text("""
        ALTER TABLE pipeline_watchlist_rejections
            ADD COLUMN IF NOT EXISTS engine_tag VARCHAR(16)
    """))


def downgrade() -> None:
    op.execute(sa.text("""
        ALTER TABLE pipeline_watchlist_assets
            DROP COLUMN IF EXISTS engine_tag
    """))
    op.execute(sa.text("""
        ALTER TABLE pipeline_watchlist_rejections
            DROP COLUMN IF EXISTS engine_tag
    """))
