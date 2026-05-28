"""TTT (Time-To-Target) ML labeling columns for shadow_trades.

Revision ID: 065_ttt_shadow_columns
Revises: 064_autopilot_system
Create Date: 2026-05-28

Contexto
--------
Adiciona a infraestrutura de dados para a política TTT (Time-To-Target):
uma camada de labeling de ML que avalia eficiência TEMPORAL dos trades,
classificando-os como FAST_WIN ou TIMEOUT independentemente do outcome
original (TP_HIT / SL_HIT / TIMEOUT).

Regras da política TTT
----------------------
  FAST_WIN  = preço atingiu ttt_tp_pct (padrão 1.0%) dentro de
              ttt_timeout_minutes (padrão 180 min = 3h) desde entry_timestamp.
  TIMEOUT   = não atingiu ttt_tp_pct no prazo.

Invariantes obrigatórios
------------------------
* NÃO altera outcome, pnl_pct, pnl_usdt, TP, SL, timeout originais.
* ttt_outcome é EXCLUSIVAMENTE para labeling de ML — nunca para P&L real.
* ttt_analysis_done=FALSE: shadow aguarda processamento pelo ttt_analyzer.
* ttt_analysis_done=TRUE: shadow já processado; idempotência garantida.
* Todos os campos nullable: back-compat com shadows antes desta migration.
* Sem lookahead bias: campos TTT são preenchidos post-trade (analytics only).

Arquitetura de preenchimento
-----------------------------
* ttt_enabled / ttt_tp_pct / ttt_timeout_minutes:
    gravados pelo shadow_trade_service na criação do shadow (snapshot).
* elapsed_minutes / profit_velocity / profit_velocity_per_hour:
    computados pelo shadow_trade_monitor._compute_ttt_outcome no fechamento.
* time_to_tp_minutes / max_profit_first_15m/30m/60m / candles_to_peak
  / candles_to_first_positive / ttt_outcome / ttt_close_reason
  / ttt_fast_win_bucket:
    * Path 1m-candles: rastreados inline pelo monitor durante o scan.
    * Path live-close / sem OHLCV: preenchidos pelo ttt_analyzer.py
      (post-analysis, seguindo padrão shadow_timeout_analyzer).
"""

from alembic import op
import sqlalchemy as sa


revision = "065_ttt_shadow_columns"
down_revision = "064_autopilot_system"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Política TTT — snapshot gravado na criação do shadow ──────────────────
    # Imutáveis após inserção: preservam a política vigente no momento em que
    # o shadow foi criado, mesmo que a config_profiles mude depois.
    op.execute(sa.text("""
        ALTER TABLE shadow_trades
            ADD COLUMN IF NOT EXISTS ttt_enabled          BOOLEAN      DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS ttt_tp_pct           DOUBLE PRECISION,
            ADD COLUMN IF NOT EXISTS ttt_timeout_minutes  INTEGER
    """))

    # ── Label TTT (resultado do post-analysis) ────────────────────────────────
    # ttt_outcome: 'FAST_WIN' | 'TIMEOUT'
    # ttt_close_reason: 'TP_HIT_IN_WINDOW' | 'HARD_TIMEOUT'
    # ttt_fast_win_bucket: granularidade temporal do win
    #   'WIN_0_15M' | 'WIN_15_30M' | 'WIN_30_60M' | 'WIN_60_180M'
    op.execute(sa.text("""
        ALTER TABLE shadow_trades
            ADD COLUMN IF NOT EXISTS ttt_outcome          VARCHAR(20),
            ADD COLUMN IF NOT EXISTS ttt_close_reason     VARCHAR(30),
            ADD COLUMN IF NOT EXISTS ttt_fast_win_bucket  VARCHAR(20),
            ADD COLUMN IF NOT EXISTS ttt_analysis_done    BOOLEAN      DEFAULT FALSE
    """))

    # ── Métricas temporais ────────────────────────────────────────────────────
    # elapsed_minutes: duração total do trade em minutos (= holding_seconds/60)
    # time_to_tp_minutes: minutos desde entry_timestamp até price >= ttt_tp_pct
    #   NULL quando TTT target não foi atingido.
    # profit_velocity: max_profit_pct / max(elapsed_minutes, 1)  [% por minuto]
    # profit_velocity_per_hour: normalizado por hora [% por hora — mais intuitivo]
    op.execute(sa.text("""
        ALTER TABLE shadow_trades
            ADD COLUMN IF NOT EXISTS elapsed_minutes            DOUBLE PRECISION,
            ADD COLUMN IF NOT EXISTS time_to_tp_minutes         DOUBLE PRECISION,
            ADD COLUMN IF NOT EXISTS profit_velocity            DOUBLE PRECISION,
            ADD COLUMN IF NOT EXISTS profit_velocity_per_hour   DOUBLE PRECISION
    """))

    # ── Lucro máximo por janela temporal ─────────────────────────────────────
    # max(high - entry) / entry * 100 nos primeiros X minutos.
    # Captura o decaimento do edge: trade que não se move nos primeiros 15m
    # raramente atinge 1% dentro de 3h.
    # Preenchidos inline no scan de candles 1m, ou pelo ttt_analyzer via OHLCV.
    op.execute(sa.text("""
        ALTER TABLE shadow_trades
            ADD COLUMN IF NOT EXISTS max_profit_first_15m  DOUBLE PRECISION,
            ADD COLUMN IF NOT EXISTS max_profit_first_30m  DOUBLE PRECISION,
            ADD COLUMN IF NOT EXISTS max_profit_first_60m  DOUBLE PRECISION
    """))

    # ── Contadores de candles ─────────────────────────────────────────────────
    # candles_to_peak: número de candles 1m até o high máximo do trade.
    # candles_to_first_positive: número de candles 1m até close > entry_price.
    # ATENÇÃO: usáveis apenas como labels/analytics — NUNCA como feature de
    # entrada do XGBoost (informação futura em relação ao momento de entrada).
    op.execute(sa.text("""
        ALTER TABLE shadow_trades
            ADD COLUMN IF NOT EXISTS candles_to_peak            INTEGER,
            ADD COLUMN IF NOT EXISTS candles_to_first_positive  INTEGER
    """))

    # ── Índices ────────────────────────────────────────────────────────────────
    # idx 1: ttt_analyzer busca shadows pendentes de análise.
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_shadow_trades_ttt_pending
            ON shadow_trades (ttt_enabled, ttt_analysis_done, completed_at)
            WHERE ttt_enabled = TRUE
              AND (ttt_analysis_done = FALSE OR ttt_analysis_done IS NULL)
    """))

    # idx 2: ML dataset queries por ttt_outcome (FAST_WIN vs TIMEOUT).
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_shadow_trades_ttt_outcome
            ON shadow_trades (ttt_outcome, ttt_fast_win_bucket)
            WHERE ttt_outcome IS NOT NULL
    """))


def downgrade() -> None:
    op.execute(sa.text(
        "DROP INDEX IF EXISTS idx_shadow_trades_ttt_outcome"
    ))
    op.execute(sa.text(
        "DROP INDEX IF EXISTS idx_shadow_trades_ttt_pending"
    ))
    op.execute(sa.text("""
        ALTER TABLE shadow_trades
            DROP COLUMN IF EXISTS ttt_enabled,
            DROP COLUMN IF EXISTS ttt_tp_pct,
            DROP COLUMN IF EXISTS ttt_timeout_minutes,
            DROP COLUMN IF EXISTS ttt_outcome,
            DROP COLUMN IF EXISTS ttt_close_reason,
            DROP COLUMN IF EXISTS ttt_fast_win_bucket,
            DROP COLUMN IF EXISTS ttt_analysis_done,
            DROP COLUMN IF EXISTS elapsed_minutes,
            DROP COLUMN IF EXISTS time_to_tp_minutes,
            DROP COLUMN IF EXISTS profit_velocity,
            DROP COLUMN IF EXISTS profit_velocity_per_hour,
            DROP COLUMN IF EXISTS max_profit_first_15m,
            DROP COLUMN IF EXISTS max_profit_first_30m,
            DROP COLUMN IF EXISTS max_profit_first_60m,
            DROP COLUMN IF EXISTS candles_to_peak,
            DROP COLUMN IF EXISTS candles_to_first_positive
    """))
