"""Create the regime history table used by macro regime writers.

Revision ID: 136_regime_history
Revises: 135_l1_dedup_constraint
Create Date: 2026-07-20
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "136_regime_history"
down_revision = "135_l1_dedup_constraint"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
    op.create_table(
        "regime_history",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("regime", sa.String(length=30), nullable=False),
        sa.Column(
            "confidence",
            sa.Float(),
            nullable=False,
            server_default=sa.text("1.0"),
        ),
        sa.Column(
            "source",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'macro'"),
        ),
        sa.Column("indicators_snapshot", postgresql.JSONB(), nullable=True),
        sa.Column(
            "detected_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "idx_regime_history_detected",
        "regime_history",
        [sa.text("detected_at DESC")],
    )
    op.create_index(
        "idx_regime_history_regime",
        "regime_history",
        ["regime", sa.text("detected_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("idx_regime_history_regime", table_name="regime_history")
    op.drop_index("idx_regime_history_detected", table_name="regime_history")
    op.drop_table("regime_history")
