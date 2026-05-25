"""MAE/MFE tracking + exit_metrics_json para shadow_trades.

Revision ID: 062_shadow_mae_mfe
Revises: 061_sim_exit_ts_safety_net
Create Date: 2026-05-25

Contexto
--------
Fase Quant 1 — MAE/MFE Tracking:
  Adiciona rastreamento contínuo de trajetória do trade no Shadow Portfolio.
  Os 6 campos são preenchidos pelo shadow_trade_monitor candle-a-candle e
  finalizados no encerramento. NÃO são usados em inferência do XGBoost nesta fase.

  MAE (Maximum Adverse Excursion): maior drawdown % desde a entrada.
  MFE (Maximum Favorable Excursion): maior lucro % desde a entrada.

  Fórmulas:
    mae_pct = (min_price_post_entry - entry_price) / entry_price * 100
    mfe_pct = (max_price_post_entry - entry_price) / entry_price * 100

Fase Quant 2 — Exit Metrics Snapshot:
  exit_metrics_json: snapshot rico no encerramento (indicadores + PnL + MAE/MFE).

Todos os campos são nullable para back-compat com trades existentes.
Idempotente — seguro re-executar (ADD COLUMN IF NOT EXISTS).
"""

from alembic import op
import sqlalchemy as sa


revision = "062_shadow_mae_mfe"
down_revision = "061_sim_exit_ts_safety_net"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Fase Quant 1: MAE/MFE tracking ───────────────────────────────────────
    op.execute(sa.text("""
        ALTER TABLE shadow_trades
            ADD COLUMN IF NOT EXISTS min_price_post_entry DOUBLE PRECISION,
            ADD COLUMN IF NOT EXISTS max_price_post_entry DOUBLE PRECISION,
            ADD COLUMN IF NOT EXISTS max_drawdown_pct     DOUBLE PRECISION,
            ADD COLUMN IF NOT EXISTS max_profit_pct       DOUBLE PRECISION,
            ADD COLUMN IF NOT EXISTS mae_pct              DOUBLE PRECISION,
            ADD COLUMN IF NOT EXISTS mfe_pct              DOUBLE PRECISION
    """))

    # ── Fase Quant 2: exit metrics snapshot ──────────────────────────────────
    op.execute(sa.text("""
        ALTER TABLE shadow_trades
            ADD COLUMN IF NOT EXISTS exit_metrics_json JSONB
    """))


def downgrade() -> None:
    op.execute(sa.text("""
        ALTER TABLE shadow_trades
            DROP COLUMN IF EXISTS min_price_post_entry,
            DROP COLUMN IF EXISTS max_price_post_entry,
            DROP COLUMN IF EXISTS max_drawdown_pct,
            DROP COLUMN IF EXISTS max_profit_pct,
            DROP COLUMN IF EXISTS mae_pct,
            DROP COLUMN IF EXISTS mfe_pct,
            DROP COLUMN IF EXISTS exit_metrics_json
    """))
