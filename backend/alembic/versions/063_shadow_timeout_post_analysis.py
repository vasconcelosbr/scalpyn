"""Post-timeout passive analysis columns for shadow_trades.

Revision ID: 063_shadow_timeout_post_analysis
Revises: 062_shadow_mae_mfe
Create Date: 2026-05-25

Contexto
--------
Fase Quant: Timeout Post-Analysis.

Para trades com outcome='TIMEOUT', rastreamos passivamente o que aconteceu
com o preço após o encerramento (+1h, +2h, +4h, +12h, +24h) — usando OHLCV
histórico, sem reabrir o trade ou alterar o outcome original.

Isso permite calcular:
  - Timeout Recovery Rate: % de timeouts que teriam atingido TP pós-encerramento
  - Delayed TP Rate e Delayed TP Time (horas até TP tardio)
  - MFE/MAE adicionais após timeout (risco temporal oculto)

Todos os campos nullable. timeout_post_analysis_done marca o trade como
processado para evitar reprocessamento. Idempotente (ADD COLUMN IF NOT EXISTS).
"""

from alembic import op
import sqlalchemy as sa


revision = "063_shadow_timeout_post_analysis"
down_revision = "062_shadow_mae_mfe"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Preços pós-timeout em horizontes fixos ────────────────────────────────
    # Calculados a partir de OHLCV histórico (close mais próximo do intervalo).
    # NULL = dado indisponível no OHLCV ou trade ainda não processado.
    op.execute(sa.text("""
        ALTER TABLE shadow_trades
            ADD COLUMN IF NOT EXISTS price_after_1h   DOUBLE PRECISION,
            ADD COLUMN IF NOT EXISTS price_after_2h   DOUBLE PRECISION,
            ADD COLUMN IF NOT EXISTS price_after_4h   DOUBLE PRECISION,
            ADD COLUMN IF NOT EXISTS price_after_12h  DOUBLE PRECISION,
            ADD COLUMN IF NOT EXISTS price_after_24h  DOUBLE PRECISION
    """))

    # ── Excursão pós-timeout (high-water / low-water nas 24h seguintes) ───────
    op.execute(sa.text("""
        ALTER TABLE shadow_trades
            ADD COLUMN IF NOT EXISTS max_profit_after_timeout_pct   DOUBLE PRECISION,
            ADD COLUMN IF NOT EXISTS max_drawdown_after_timeout_pct DOUBLE PRECISION
    """))

    # ── Delayed TP: teria atingido TP dentro das 24h seguintes? ──────────────
    op.execute(sa.text("""
        ALTER TABLE shadow_trades
            ADD COLUMN IF NOT EXISTS delayed_tp        BOOLEAN,
            ADD COLUMN IF NOT EXISTS delayed_tp_hours  DOUBLE PRECISION
    """))

    # ── Flag de controle: evita reprocessamento ───────────────────────────────
    op.execute(sa.text("""
        ALTER TABLE shadow_trades
            ADD COLUMN IF NOT EXISTS timeout_post_analysis_done BOOLEAN DEFAULT FALSE
    """))

    # Índice para o analyzer buscar trades não processados eficientemente.
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_shadow_trades_timeout_pending_analysis
            ON shadow_trades (outcome, timeout_post_analysis_done, exit_timestamp)
            WHERE outcome = 'TIMEOUT' AND timeout_post_analysis_done = FALSE
    """))


def downgrade() -> None:
    op.execute(sa.text(
        "DROP INDEX IF EXISTS idx_shadow_trades_timeout_pending_analysis"
    ))
    op.execute(sa.text("""
        ALTER TABLE shadow_trades
            DROP COLUMN IF EXISTS price_after_1h,
            DROP COLUMN IF EXISTS price_after_2h,
            DROP COLUMN IF EXISTS price_after_4h,
            DROP COLUMN IF EXISTS price_after_12h,
            DROP COLUMN IF EXISTS price_after_24h,
            DROP COLUMN IF EXISTS max_profit_after_timeout_pct,
            DROP COLUMN IF EXISTS max_drawdown_after_timeout_pct,
            DROP COLUMN IF EXISTS delayed_tp,
            DROP COLUMN IF EXISTS delayed_tp_hours,
            DROP COLUMN IF EXISTS timeout_post_analysis_done
    """))
