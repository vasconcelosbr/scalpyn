"""Add the L3 authorization v3 transactional outbox.

Revision ID: 202_l3_authorization_v3_outbox
Revises: 201_shadow_safety_net_lineage
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "202_l3_authorization_v3_outbox"
down_revision = "201_shadow_safety_net_lineage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "l3_authorization_outbox",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decision_id", sa.BigInteger(), nullable=False),
        sa.Column("authorization_contract_hash", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["decision_id"], ["decisions_log.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "decision_id", "authorization_contract_hash",
            name="uq_l3_authorization_outbox_decision_contract",
        ),
    )
    op.create_index(
        "ix_l3_authorization_outbox_status_created",
        "l3_authorization_outbox",
        ["status", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_l3_authorization_outbox_status_created",
        table_name="l3_authorization_outbox",
    )
    op.drop_table("l3_authorization_outbox")
