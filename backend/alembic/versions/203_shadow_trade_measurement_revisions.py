"""Add immutable Shadow Portfolio measurement revisions.

Revision ID: 203_shadow_measurement_rev
Revises: 202_l3_authorization_v3_outbox
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "203_shadow_measurement_rev"
down_revision = "202_l3_authorization_v3_outbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "shadow_trade_measurement_revisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("shadow_trade_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("measurement_contract_version", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("method", sa.String(length=80), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("timeframe", sa.String(length=16), nullable=True),
        sa.Column("resolution_seconds", sa.Integer(), nullable=True),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("input_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("legacy_entry_price", sa.Numeric(24, 12), nullable=True),
        sa.Column("entry_price_reference", sa.Numeric(24, 12), nullable=True),
        sa.Column("entry_price_observed", sa.Numeric(24, 12), nullable=True),
        sa.Column("entry_price_realized", sa.Numeric(24, 12), nullable=True),
        sa.Column("entry_price_source_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("entry_price_lag_seconds", sa.Numeric(16, 6), nullable=True),
        sa.Column("entry_quality", sa.String(length=24), nullable=False),
        sa.Column("legacy_mae_pct", sa.Numeric(18, 9), nullable=True),
        sa.Column("legacy_mfe_pct", sa.Numeric(18, 9), nullable=True),
        sa.Column("legacy_mae_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("legacy_mfe_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("mae_pct", sa.Numeric(18, 9), nullable=True),
        sa.Column("mfe_pct", sa.Numeric(18, 9), nullable=True),
        sa.Column("mae_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("mfe_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("entry_boundary_partial", sa.Boolean(), nullable=False),
        sa.Column("exit_boundary_partial", sa.Boolean(), nullable=False),
        sa.Column("gross_return_pct", sa.Numeric(18, 9), nullable=True),
        sa.Column("fee_roundtrip_pct_applied", sa.Numeric(18, 9), nullable=True),
        sa.Column("net_return_pct", sa.Numeric(18, 9), nullable=True),
        sa.Column("cost_contract_version", sa.String(length=80), nullable=False),
        sa.Column("unavailable_reason", sa.String(length=160), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status <> 'READY' OR (mfe_pct >= 0 AND mae_pct <= 0 "
            "AND gross_return_pct IS NOT NULL "
            "AND mae_pct <= gross_return_pct AND gross_return_pct <= mfe_pct)",
            name="ck_shadow_measurement_ready_extrema",
        ),
        sa.ForeignKeyConstraint(["shadow_trade_id"], ["shadow_trades.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "shadow_trade_id",
            "measurement_contract_version",
            "input_hash",
            name="uq_shadow_measurement_revision_input",
        ),
    )
    op.create_index(
        "ix_shadow_measurement_trade_created",
        "shadow_trade_measurement_revisions",
        ["shadow_trade_id", "created_at"],
        unique=False,
    )
    op.execute(
        """
        CREATE FUNCTION reject_shadow_measurement_revision_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'shadow_trade_measurement_revisions is append-only';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_shadow_measurement_revision_append_only
        BEFORE UPDATE OR DELETE ON shadow_trade_measurement_revisions
        FOR EACH ROW EXECUTE FUNCTION reject_shadow_measurement_revision_mutation()
        """
    )
    op.create_index(
        "ix_shadow_measurement_status_created",
        "shadow_trade_measurement_revisions",
        ["status", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_shadow_measurement_revision_append_only "
        "ON shadow_trade_measurement_revisions"
    )
    op.execute("DROP FUNCTION IF EXISTS reject_shadow_measurement_revision_mutation()")
    op.drop_index("ix_shadow_measurement_status_created", table_name="shadow_trade_measurement_revisions")
    op.drop_index("ix_shadow_measurement_trade_created", table_name="shadow_trade_measurement_revisions")
    op.drop_table("shadow_trade_measurement_revisions")
