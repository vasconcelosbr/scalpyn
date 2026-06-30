"""narrow ux_shadow_running_user_source to completed_at IS NULL

Revision ID: c003shadow_idx_narrowfix
Revises: c002rastreat
Create Date: 2026-06-30

ux_shadow_running_user_source was defined as
  (user_id, symbol, source) WHERE profile_id IS NULL
which covers both running and completed trades.

When deleting a profile, PostgreSQL's cascade SET NULL on shadow_trades.profile_id
moves the profile's (completed) trades into the scope of this index, causing a
duplicate key violation if a baseline (profile_id IS NULL) trade already exists
for the same (user, symbol, source).

Fix: narrow the partial index to only running trades (completed_at IS NULL),
so completed trades — including those that become profile_id=NULL via cascade —
never conflict.
"""
from alembic import op

revision = "c003shadow_idx_narrowfix"
down_revision = "c002rastreat"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "DROP INDEX IF EXISTS ux_shadow_running_user_source"
    )
    op.execute(
        """
        CREATE UNIQUE INDEX ux_shadow_running_user_source
        ON shadow_trades (user_id, symbol, source)
        WHERE profile_id IS NULL AND completed_at IS NULL
        """
    )


def downgrade():
    op.execute(
        "DROP INDEX IF EXISTS ux_shadow_running_user_source"
    )
    op.execute(
        """
        CREATE UNIQUE INDEX ux_shadow_running_user_source
        ON shadow_trades (user_id, symbol, source)
        WHERE profile_id IS NULL
        """
    )
