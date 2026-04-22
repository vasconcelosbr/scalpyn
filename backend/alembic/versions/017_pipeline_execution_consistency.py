"""Add execution_id tracing and normalize pipeline source config

Revision ID: 017_pipeline_execution_consistency
Revises: 016_pipeline_rejected_snapshot
Create Date: 2026-04-22
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "017_pipeline_execution_consistency"
down_revision = "016_pipeline_rejected_snapshot"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("pipeline_watchlist_assets", sa.Column("execution_id", UUID(as_uuid=True), nullable=True))
    op.add_column("pipeline_watchlist_rejections", sa.Column("execution_id", UUID(as_uuid=True), nullable=True))

    # Defensive data normalization for legacy inconsistent configs:
    # For L1/L2/L3, migrate source_pool_id → source_watchlist_id when possible.
    # Uses DISTINCT ON + JOIN instead of LATERAL to avoid "invalid reference to
    # FROM-clause entry for table 'child'" in PostgreSQL UPDATE...FROM LATERAL.
    op.execute(
        """
        UPDATE pipeline_watchlists
        SET source_watchlist_id = parent_lookup.parent_id
        FROM (
            SELECT DISTINCT ON (c.id)
                c.id          AS child_id,
                pw.id         AS parent_id
            FROM pipeline_watchlists c
            JOIN pipeline_watchlists pw
                ON  pw.user_id        = c.user_id
                AND pw.source_pool_id = c.source_pool_id
                AND UPPER(pw.level)   = 'POOL'
            WHERE UPPER(c.level) IN ('L1', 'L2', 'L3')
              AND c.source_pool_id       IS NOT NULL
              AND c.source_watchlist_id  IS NULL
            ORDER BY c.id, pw.created_at ASC
        ) AS parent_lookup
        WHERE pipeline_watchlists.id = parent_lookup.child_id
        """
    )

    # Enforce level/source invariants for existing data.
    op.execute(
        """
        UPDATE pipeline_watchlists
        SET source_pool_id = NULL
        WHERE UPPER(level) IN ('L1', 'L2', 'L3')
        """
    )
    op.execute(
        """
        UPDATE pipeline_watchlists
        SET source_watchlist_id = NULL
        WHERE UPPER(level) = 'POOL'
        """
    )


def downgrade() -> None:
    op.drop_column("pipeline_watchlist_rejections", "execution_id")
    op.drop_column("pipeline_watchlist_assets", "execution_id")
