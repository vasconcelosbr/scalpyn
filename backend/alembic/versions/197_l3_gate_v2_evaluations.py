"""Add durable observational L3 gate v2 evaluations.

Revision ID: 197_l3_gate_v2_evaluations
Revises: 196_entry_risk_observation
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "197_l3_gate_v2_evaluations"
down_revision = "196_entry_risk_observation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "l3_gate_v2_evaluations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("evaluation_envelope_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("watchlist_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("profile_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("profile_name", sa.String(length=255), nullable=True),
        sa.Column("symbol", sa.String(length=50), nullable=False),
        sa.Column("timeframe", sa.String(length=10), nullable=True),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("legacy_decision", sa.String(length=16), nullable=False),
        sa.Column("shadow_decision", sa.String(length=16), nullable=False),
        sa.Column("decision_drift", sa.Boolean(), nullable=False),
        sa.Column(
            "operational_effect",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("decision_id", sa.BigInteger(), nullable=True),
        sa.Column("shadow_trade_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "capture_attempts", sa.Integer(), nullable=False, server_default="1"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "operational_effect = false",
            name="ck_l3_gate_v2_observational_only",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "evaluation_envelope_hash", name="uq_l3_gate_v2_envelope_hash"
        ),
    )
    op.create_index(
        "ix_l3_gate_v2_created_at",
        "l3_gate_v2_evaluations",
        ["created_at"],
    )
    op.create_index(
        "ix_l3_gate_v2_drift_created",
        "l3_gate_v2_evaluations",
        ["decision_drift", "created_at"],
    )
    op.create_index(
        "ix_l3_gate_v2_profile_symbol",
        "l3_gate_v2_evaluations",
        ["profile_id", "symbol", "evaluated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_l3_gate_v2_profile_symbol", table_name="l3_gate_v2_evaluations")
    op.drop_index("ix_l3_gate_v2_drift_created", table_name="l3_gate_v2_evaluations")
    op.drop_index("ix_l3_gate_v2_created_at", table_name="l3_gate_v2_evaluations")
    op.drop_table("l3_gate_v2_evaluations")

