"""Add shadow_trades table — Shadow Portfolio (Fase 1).

Revision ID: 046_shadow_trades
Revises: 045_trade_decisions
Create Date: 2026-05-12

Context
-------
A promoção L3 (``decisions_log.decision='ALLOW' AND
decisions_log.direction='up'``) que chega em ``execute_buy.py`` mas é
barrada por gate de capital/risco (saldo insuficiente, max_positions,
circuit breaker, cooldown, etc.) hoje some sem rastro. ``shadow_trades``
é a tabela que registra essas oportunidades como se fossem trades
simulados de U$1000 USDT, com os mesmos TP/SL do config real, para:

  * dar visibilidade ao usuário (aba "Shadow Trade");
  * alimentar o dataset de ML (via ``trade_simulations.source='SHADOW'``
    gravado pelo monitor da Fase 3);
  * NUNCA contaminar P&L real, win rate real ou capital em uso.

Status do registro: PENDING (aguardando entry) → RUNNING (em
acompanhamento) → COMPLETED (TP / SL / TIMEOUT) ou ERROR (sem OHLCV
disponível).

Rule N/N+1
----------
Esta tabela NÃO é adicionada a ``_critical_schema.py`` neste deploy.
Entra apenas no deploy N+1, depois que produção confirmar que as
colunas existem. Ver Skill #7 (alembic-migration-guardrails).
"""

from alembic import op
import sqlalchemy as sa


revision = "046_shadow_trades"
down_revision = "045_trade_decisions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS shadow_trades (
            id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            decision_id       BIGINT NOT NULL REFERENCES decisions_log(id),
            user_id           UUID NOT NULL REFERENCES users(id),
            symbol            VARCHAR(20) NOT NULL,
            strategy          VARCHAR(50) NULL,
            direction         VARCHAR(10) NULL,
            amount_usdt       DOUBLE PRECISION NOT NULL DEFAULT 1000.0,
            entry_price       DOUBLE PRECISION NULL,
            entry_timestamp   TIMESTAMPTZ NULL,
            tp_price          DOUBLE PRECISION NULL,
            sl_price          DOUBLE PRECISION NULL,
            tp_pct            DOUBLE PRECISION NULL,
            sl_pct            DOUBLE PRECISION NULL,
            timeout_candles   INTEGER NULL,
            exit_price        DOUBLE PRECISION NULL,
            exit_timestamp    TIMESTAMPTZ NULL,
            outcome           VARCHAR(20) NULL,
            pnl_pct           DOUBLE PRECISION NULL,
            pnl_usdt          DOUBLE PRECISION NULL,
            holding_seconds   INTEGER NULL,
            status            VARCHAR(20) NOT NULL DEFAULT 'PENDING',
            skip_reason       VARCHAR(50) NULL,
            config_snapshot   JSONB NULL,
            features_snapshot JSONB NULL,
            last_processed_time TIMESTAMPTZ NULL,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            completed_at      TIMESTAMPTZ NULL
        )
    """))

    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_shadow_trades_user_id
            ON shadow_trades (user_id)
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_shadow_trades_decision_id
            ON shadow_trades (decision_id)
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_shadow_trades_status
            ON shadow_trades (status)
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_shadow_trades_symbol
            ON shadow_trades (symbol)
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_shadow_trades_created_at
            ON shadow_trades (created_at DESC)
    """))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS shadow_trades"))
