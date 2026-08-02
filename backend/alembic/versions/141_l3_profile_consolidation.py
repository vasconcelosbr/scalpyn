"""Add opt-in uniqueness for consolidated canonical L3 shadows.

Revision ID: 141_l3_profile_consolidation
Revises: 140_shadow_detailed_report
Create Date: 2026-08-01

The boolean predicate is essential to the feature-flag contract: legacy rows
and new rows created with the flag disabled remain outside the unique index.
When enabled, PostgreSQL rejects a second active canonical L3 shadow for the
same tenant, symbol, and direction.
"""

from alembic import op
from sqlalchemy import text


revision = "141_l3_profile_consolidation"
down_revision = "140_shadow_detailed_report"
branch_labels = None
depends_on = None

_INDEX_NAME = "ux_shadow_l3_consolidated_active"


def upgrade() -> None:
    op.execute(text("SET LOCAL lock_timeout = '10s'"))
    op.execute(
        text(
            """
            ALTER TABLE shadow_trades
            ADD COLUMN IF NOT EXISTS l3_consolidation_enforced BOOLEAN
            NOT NULL DEFAULT FALSE
            """
        )
    )
    op.execute(
        text(
            f"""
            CREATE UNIQUE INDEX IF NOT EXISTS {_INDEX_NAME}
                ON shadow_trades (user_id, symbol, direction)
             WHERE source = 'L3'
               AND l3_consolidation_enforced = TRUE
               AND status IN ('PENDING', 'RUNNING')
            """
        )
    )


def downgrade() -> None:
    op.execute(text(f"DROP INDEX IF EXISTS {_INDEX_NAME}"))
    op.execute(
        text(
            """
            ALTER TABLE shadow_trades
            DROP COLUMN IF EXISTS l3_consolidation_enforced
            """
        )
    )
