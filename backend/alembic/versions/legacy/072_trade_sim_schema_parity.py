"""Trade simulations schema parity — migration 062 drift correction.

Revision ID: 072_trade_sim_schema_parity
Revises: 071_shadow_instrumentation
Create Date: 2026-06-10

Migration 062 added 7 columns to ``shadow_trades`` (MAE/MFE tracking +
exit_metrics_json) but never mirrored them to ``trade_simulations``,
breaking the invariant established in migration 071.

This migration restores parity. All columns are nullable (additive-only
— no UPDATE on existing rows).

Additional schema drift detected but NOT fixed here (separate prompt):
  - migration 052: btc_price_at_entry, btc_change_1h_pct,
    funding_rate_at_entry, n_concurrent_signals
  - migration 063: price_after_1h..24h, max_profit/drawdown_after_timeout_pct,
    delayed_tp, delayed_tp_hours, timeout_post_analysis_done
  - migration 065: ttt_* columns, elapsed_minutes, time_to_tp_minutes,
    profit_velocity, profit_velocity_per_hour, max_profit_first_*,
    candles_to_peak, candles_to_first_positive
These are observational / post-analysis fields; their absence from
trade_simulations does not block the current ML pipeline.
"""

from alembic import op
import sqlalchemy as sa

revision = "072_trade_sim_schema_parity"
down_revision = "071_shadow_instrumentation"
branch_labels = None
depends_on = None

_UPGRADE_DDL = """
    ALTER TABLE trade_simulations
        ADD COLUMN IF NOT EXISTS min_price_post_entry  DOUBLE PRECISION,
        ADD COLUMN IF NOT EXISTS max_price_post_entry  DOUBLE PRECISION,
        ADD COLUMN IF NOT EXISTS max_drawdown_pct      DOUBLE PRECISION,
        ADD COLUMN IF NOT EXISTS max_profit_pct        DOUBLE PRECISION,
        ADD COLUMN IF NOT EXISTS mae_pct               DOUBLE PRECISION,
        ADD COLUMN IF NOT EXISTS mfe_pct               DOUBLE PRECISION,
        ADD COLUMN IF NOT EXISTS exit_metrics_json     JSONB
"""

_DOWNGRADE_DDL = """
    ALTER TABLE trade_simulations
        DROP COLUMN IF EXISTS min_price_post_entry,
        DROP COLUMN IF EXISTS max_price_post_entry,
        DROP COLUMN IF EXISTS max_drawdown_pct,
        DROP COLUMN IF EXISTS max_profit_pct,
        DROP COLUMN IF EXISTS mae_pct,
        DROP COLUMN IF EXISTS mfe_pct,
        DROP COLUMN IF EXISTS exit_metrics_json
"""


def upgrade() -> None:
    op.execute(sa.text(_UPGRADE_DDL))


def downgrade() -> None:
    op.execute(sa.text(_DOWNGRADE_DDL))
