"""Record observable Shadow exit and measurement provenance.

Revision ID: 204_shadow_exit_measurement
Revises: 203_shadow_measurement_rev
"""

from alembic import op
import sqlalchemy as sa


revision = "204_shadow_exit_measurement"
down_revision = "203_shadow_measurement_rev"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable, no-default columns keep this additive change compatible with
    # historical rows and avoid a table-wide data rewrite.
    op.add_column("shadow_trades", sa.Column("exit_price_nominal", sa.Float(), nullable=True))
    op.add_column("shadow_trades", sa.Column("exit_price_observed", sa.Float(), nullable=True))
    op.add_column("shadow_trades", sa.Column("exit_price_semantics", sa.String(length=40), nullable=True))
    op.add_column("shadow_trades", sa.Column("barrier_overshoot_pct", sa.Float(), nullable=True))

    op.add_column(
        "shadow_trade_measurement_revisions",
        sa.Column("exit_price_nominal", sa.Numeric(24, 12), nullable=True),
    )
    op.add_column(
        "shadow_trade_measurement_revisions",
        sa.Column("exit_price_observed", sa.Numeric(24, 12), nullable=True),
    )
    op.add_column(
        "shadow_trade_measurement_revisions",
        sa.Column("exit_price_semantics", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "shadow_trade_measurement_revisions",
        sa.Column("barrier_overshoot_pct", sa.Numeric(18, 9), nullable=True),
    )
    op.add_column(
        "shadow_trade_measurement_revisions",
        sa.Column("mfe_mae_source", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "shadow_trade_measurement_revisions",
        sa.Column("mfe_mae_recomputed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "shadow_trade_measurement_revisions",
        sa.Column("mfe_mae_method_version", sa.String(length=80), nullable=True),
    )

    # Do not UPDATE historical rows: migration 203 enforces append-only
    # evidence.  New v2 revisions always provide these values.


def downgrade() -> None:
    op.drop_column("shadow_trade_measurement_revisions", "mfe_mae_method_version")
    op.drop_column("shadow_trade_measurement_revisions", "mfe_mae_recomputed_at")
    op.drop_column("shadow_trade_measurement_revisions", "mfe_mae_source")
    op.drop_column("shadow_trade_measurement_revisions", "barrier_overshoot_pct")
    op.drop_column("shadow_trade_measurement_revisions", "exit_price_semantics")
    op.drop_column("shadow_trade_measurement_revisions", "exit_price_observed")
    op.drop_column("shadow_trade_measurement_revisions", "exit_price_nominal")
    op.drop_column("shadow_trades", "barrier_overshoot_pct")
    op.drop_column("shadow_trades", "exit_price_semantics")
    op.drop_column("shadow_trades", "exit_price_observed")
    op.drop_column("shadow_trades", "exit_price_nominal")
