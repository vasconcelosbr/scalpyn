"""Create decisions_log table for pipeline audit trail

Revision ID: 015_decisions_log_pipeline
Revises: 014_backoffice
Create Date: 2026-04-21
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "015_decisions_log_pipeline"
down_revision = "014_backoffice"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "decisions_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("strategy", sa.String(length=50), nullable=False),
        sa.Column("timeframe", sa.String(length=10), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("decision", sa.String(length=10), nullable=False),
        sa.Column("l1_pass", sa.Boolean(), nullable=True),
        sa.Column("l2_pass", sa.Boolean(), nullable=True),
        sa.Column("l3_pass", sa.Boolean(), nullable=True),
        sa.Column("reasons", JSONB(), nullable=True),
        sa.Column("metrics", JSONB(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_decisions_symbol", "decisions_log", ["symbol"])
    op.create_index("idx_decisions_created_at", "decisions_log", ["created_at"])
    op.create_index("idx_decisions_score", "decisions_log", ["score"])
    op.create_index("idx_decisions_decision", "decisions_log", ["decision"])


def downgrade() -> None:
    op.drop_index("idx_decisions_decision", table_name="decisions_log")
    op.drop_index("idx_decisions_score", table_name="decisions_log")
    op.drop_index("idx_decisions_created_at", table_name="decisions_log")
    op.drop_index("idx_decisions_symbol", table_name="decisions_log")
    op.drop_table("decisions_log")
