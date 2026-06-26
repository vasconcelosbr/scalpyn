"""Add missing shadow_trades indexes skipped by stamp-at-020 bootstrap.

Revision ID: 067_shadow_missing_indexes
Revises: 066_pipeline_assets_uq
Create Date: 2026-06-09

Context: Railway DB bootstrapped via init_db (Base.metadata.create_all) +
alembic stamp 020. Migrations 046-065 were skipped, leaving shadow_trades
without critical indexes:

- ix_shadow_trades_decision_id_uniq (047) — replaced by 056 below
- ux_shadow_running_user_symbol (056) — partial unique index required by
  ON CONFLICT (user_id, symbol) WHERE status = 'RUNNING' in _INSERT_SHADOW_SQL

Also adds DEFAULT gen_random_uuid() to shadow_trades.id which was created
by create_all without a server-side default (Python-side only).

All statements use IF NOT EXISTS — idempotent on DBs that already ran the
original migrations.
"""

from alembic import op
import sqlalchemy as sa

revision = "067_shadow_missing_indexes"
down_revision = "066_pipeline_assets_uq"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        DO $$
        BEGIN
            -- 1. Add server-side DEFAULT gen_random_uuid() to shadow_trades.id
            --    (create_all only sets Python-side default; raw INSERT SQL needs server default)
            ALTER TABLE shadow_trades
                ALTER COLUMN id SET DEFAULT gen_random_uuid();

            -- 2. Partial unique index: at most one RUNNING shadow per (user_id, symbol).
            --    Required by ON CONFLICT (user_id, symbol) WHERE status = 'RUNNING'.
            --    Migration 056 dropped ix_shadow_trades_decision_id_uniq and replaced
            --    it with this index; both were skipped on stamped-at-020 DBs.
            IF NOT EXISTS (
                SELECT 1 FROM pg_indexes
                WHERE indexname = 'ux_shadow_running_user_symbol'
            ) THEN
                CREATE UNIQUE INDEX ux_shadow_running_user_symbol
                    ON shadow_trades (user_id, symbol)
                    WHERE status = 'RUNNING';
            END IF;

            -- 3. Regular indexes on shadow_trades from migration 046
            --    (all idempotent via IF NOT EXISTS)
            IF NOT EXISTS (
                SELECT 1 FROM pg_indexes WHERE indexname = 'ix_shadow_trades_user_id'
            ) THEN
                CREATE INDEX ix_shadow_trades_user_id ON shadow_trades (user_id);
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM pg_indexes WHERE indexname = 'ix_shadow_trades_decision_id'
            ) THEN
                CREATE INDEX ix_shadow_trades_decision_id ON shadow_trades (decision_id);
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM pg_indexes WHERE indexname = 'ix_shadow_trades_status'
            ) THEN
                CREATE INDEX ix_shadow_trades_status ON shadow_trades (status);
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM pg_indexes WHERE indexname = 'ix_shadow_trades_symbol'
            ) THEN
                CREATE INDEX ix_shadow_trades_symbol ON shadow_trades (symbol);
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM pg_indexes WHERE indexname = 'ix_shadow_trades_created_at'
            ) THEN
                CREATE INDEX ix_shadow_trades_created_at ON shadow_trades (created_at DESC);
            END IF;

        END $$;
    """))


def downgrade() -> None:
    op.execute(sa.text("""
        DROP INDEX IF EXISTS ux_shadow_running_user_symbol;
        ALTER TABLE shadow_trades ALTER COLUMN id DROP DEFAULT;
    """))
