"""Idempotency index — profiles.generated_from_suggestion_id unique where not null.

Also adds idx_profiles_generated_by for fast queries by generator type.

Revision ID: 091_profiles_suggestion_unique
Revises: 090_pi_audit_log
Create Date: 2026-06-17
"""

from alembic import op
import sqlalchemy as sa

revision = "091_profiles_suggestion_unique"
down_revision = "090_pi_audit_log"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Partial unique: one profile per suggestion (idempotent create-from-suggestion).
    # WHERE NOT NULL so regular profiles (NULL) are unaffected.
    op.execute(sa.text("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_profiles_from_suggestion
        ON profiles (generated_from_suggestion_id)
        WHERE generated_from_suggestion_id IS NOT NULL
    """))

    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_profiles_generated_by
        ON profiles (generated_by)
        WHERE generated_by IS NOT NULL
    """))


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS uq_profiles_from_suggestion"))
    op.execute(sa.text("DROP INDEX IF EXISTS idx_profiles_generated_by"))
