"""opportunity_snapshots — all assets evaluated at the L3 gate

Records every asset that was evaluated at L3, regardless of whether any profile
approved it. Enables contrafactual analysis and discovery of untested combinations.

Revision ID: 080_opportunity_snapshots
Revises: 079_lab_shadow_source
Create Date: 2026-06-17
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "080_opportunity_snapshots"
down_revision = "079_lab_shadow_source"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS opportunity_snapshots (
            id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id                 UUID        NOT NULL,
            symbol                  VARCHAR(30) NOT NULL,
            watchlist_id            UUID        NULL,
            execution_id            VARCHAR(64) NULL,
            source                  VARCHAR(30) NOT NULL DEFAULT 'L3_GATE',
            timeframe               VARCHAR(10) NULL,
            price                   NUMERIC     NULL,
            features_json           JSONB       NOT NULL DEFAULT '{}'::jsonb,
            profiles_evaluated      UUID[]      NULL,
            profiles_approved       UUID[]      NULL,
            profiles_rejected       UUID[]      NULL,
            rejection_reasons       JSONB       NULL,
            active_profiles_result_json JSONB   NULL,
            future_outcome          VARCHAR(20) NULL,
            future_pnl_pct          NUMERIC     NULL,
            future_time_to_tp_seconds INTEGER   NULL,
            future_time_to_sl_seconds INTEGER   NULL,
            future_mae_pct          NUMERIC     NULL,
            future_mfe_pct          NUMERIC     NULL,
            future_evaluated_at     TIMESTAMPTZ NULL,
            created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))

    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_opp_snap_user_created
        ON opportunity_snapshots (user_id, created_at DESC)
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_opp_snap_symbol_created
        ON opportunity_snapshots (symbol, created_at DESC)
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_opp_snap_user_symbol_created
        ON opportunity_snapshots (user_id, symbol, created_at DESC)
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_opp_snap_execution
        ON opportunity_snapshots (execution_id)
        WHERE execution_id IS NOT NULL
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_opp_snap_features
        ON opportunity_snapshots USING GIN (features_json)
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_opp_snap_profiles_result
        ON opportunity_snapshots USING GIN (active_profiles_result_json)
        WHERE active_profiles_result_json IS NOT NULL
    """))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS opportunity_snapshots"))
