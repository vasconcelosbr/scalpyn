"""Add immutable profile status transition evidence.

Revision ID: 221_profile_status_audit
Revises: 220_shadow_l3_closure_path
"""

from alembic import op
import sqlalchemy as sa


revision = "221_profile_status_audit"
down_revision = "220_shadow_l3_closure_path"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "profile_audit_log",
        sa.Column("previous_is_active", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "profile_audit_log",
        sa.Column("new_is_active", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "profile_audit_log",
        sa.Column("status_reason", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("profile_audit_log", "status_reason")
    op.drop_column("profile_audit_log", "new_is_active")
    op.drop_column("profile_audit_log", "previous_is_active")
