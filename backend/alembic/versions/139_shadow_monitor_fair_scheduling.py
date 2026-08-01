"""Add a dedicated fair-scheduling cursor to the shadow monitor.

Revision ID: 139_shadow_monitor_fairness
Revises: 138_l1_readiness_governance
Create Date: 2026-07-30
"""

from alembic import op
import sqlalchemy as sa


revision = "139_shadow_monitor_fairness"
down_revision = "138_l1_readiness_governance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "shadow_trades",
        sa.Column(
            "monitor_checked_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("shadow_trades", "monitor_checked_at")
