"""Add autopilot_enabled to pools table

Revision ID: 011_autopilot_pool
Revises: 010_ai_skills
Create Date: 2026-03-25
"""

from alembic import op
import sqlalchemy as sa

revision = "011_autopilot_pool"
down_revision = "010_ai_skills"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        DO $$
        BEGIN
            ALTER TABLE pools ADD COLUMN IF NOT EXISTS autopilot_enabled BOOLEAN NOT NULL DEFAULT false;
        END $$;
    """))


def downgrade() -> None:
    op.execute(sa.text("ALTER TABLE pools DROP COLUMN IF EXISTS autopilot_enabled;"))
