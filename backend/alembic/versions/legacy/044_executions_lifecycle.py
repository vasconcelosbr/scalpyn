"""Add exchange_executions + position_lifecycle (Task #257).

Revision ID: 044_executions_lifecycle
Revises: 043_pool_coins_is_tradable
Create Date: 2026-05-11

Context (Task #257)
-------------------
Today the app has THREE different sources of truth for "what trades happened":

  * ``trades``         — imported by trade_sync_service from /spot/orders,
                         FIFO-matched in Python (spot only, no fills, no
                         futures, partial closes collapsed).
  * ``trade_tracking`` — written by Decision Log Enricher; mirrors live
                         pipeline decisions, not actual exchange fills.
  * portfolio_service  — reads exchange balances at request time.

This migration introduces the new normalised model the institutional
performance dashboard reads from:

  * ``exchange_executions``   — one row per raw fill from Gate.io
                                (spot.my_trades + futures.my_trades).
                                Idempotent UPSERT key
                                ``(exchange, market_type, trade_id)``.
  * ``position_lifecycle``    — one row per closed (or partially closed)
                                logical trade produced by the FIFO engine
                                in ``position_lifecycle_service``.

Rule N/N+1
----------
NEITHER table is added to ``_critical_schema.py`` here.  They are
added in deploy N+1 once production proves the columns exist.
"""

from alembic import op
import sqlalchemy as sa


revision = "044_executions_lifecycle"
down_revision = "043_pool_coins_is_tradable"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS exchange_executions (
            id              BIGSERIAL PRIMARY KEY,
            user_id         UUID NULL,
            exchange        VARCHAR(20)  NOT NULL DEFAULT 'gate',
            market_type     VARCHAR(10)  NOT NULL,
            trade_id        VARCHAR(64)  NOT NULL,
            order_id        VARCHAR(64)  NULL,
            symbol          VARCHAR(40)  NOT NULL,
            side            VARCHAR(10)  NOT NULL,
            role            VARCHAR(10)  NULL,
            price           NUMERIC(28,12) NOT NULL,
            quantity        NUMERIC(28,12) NOT NULL,
            quote_quantity  NUMERIC(28,8)  NULL,
            fee             NUMERIC(28,12) NULL,
            fee_currency    VARCHAR(20)  NULL,
            executed_at     TIMESTAMPTZ  NOT NULL,
            ingested_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            raw_payload     JSONB        NULL,
            CONSTRAINT uq_exchange_executions_dedup
                UNIQUE (exchange, market_type, trade_id)
        )
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_exchange_executions_user_time
            ON exchange_executions (user_id, executed_at DESC)
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_exchange_executions_symbol_time
            ON exchange_executions (symbol, executed_at DESC)
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_exchange_executions_order
            ON exchange_executions (order_id)
            WHERE order_id IS NOT NULL
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS position_lifecycle (
            id                   BIGSERIAL PRIMARY KEY,
            user_id              UUID NULL,
            exchange             VARCHAR(20)  NOT NULL DEFAULT 'gate',
            symbol               VARCHAR(40)  NOT NULL,
            market_type          VARCHAR(10)  NOT NULL,
            direction            VARCHAR(10)  NOT NULL,
            opened_at            TIMESTAMPTZ NOT NULL,
            closed_at            TIMESTAMPTZ NULL,
            holding_seconds      INTEGER NULL,
            qty                  NUMERIC(28,12) NOT NULL,
            avg_entry            NUMERIC(28,12) NOT NULL,
            avg_exit             NUMERIC(28,12) NULL,
            invested_usdt        NUMERIC(28,8)  NOT NULL,
            final_usdt           NUMERIC(28,8)  NULL,
            fees_total           NUMERIC(28,8)  NOT NULL DEFAULT 0,
            pnl_usdt             NUMERIC(28,8)  NULL,
            pnl_pct              NUMERIC(14,6)  NULL,
            roi                  NUMERIC(14,6)  NULL,
            status               VARCHAR(20)  NOT NULL DEFAULT 'open',
            n_fills_in           INTEGER NOT NULL DEFAULT 0,
            n_fills_out          INTEGER NOT NULL DEFAULT 0,
            entry_trade_ids      JSONB NULL,
            exit_trade_ids       JSONB NULL,
            slippage_estimate    NUMERIC(14,6) NULL,
            maker_taker_ratio    NUMERIC(6,4) NULL,
            data_quality         VARCHAR(10) NOT NULL DEFAULT 'OK',
            created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_position_lifecycle_user_closed
            ON position_lifecycle (user_id, closed_at DESC)
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_position_lifecycle_symbol_closed
            ON position_lifecycle (symbol, market_type, closed_at DESC)
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_position_lifecycle_status
            ON position_lifecycle (status)
            WHERE status <> 'closed'
    """))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS position_lifecycle"))
    op.execute(sa.text("DROP TABLE IF EXISTS exchange_executions"))
