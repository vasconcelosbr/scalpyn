"""Add missing unique constraint on pipeline_watchlist_assets(watchlist_id, symbol).

Revision ID: 066_fix_pipeline_assets_unique_constraint
Revises: 065_ttt_shadow_columns
Create Date: 2026-06-09

Contexto
--------
Ao migrar para o Railway com banco fresh, o fluxo foi:
  1. init_db.py (Base.metadata.create_all) — cria a tabela SEM o unique constraint
  2. alembic stamp 020 — marca migração 012 como já aplicada (incorreto)
  3. alembic upgrade head — roda 021+ que adicionam colunas mas não o constraint

Resultado: pipeline_scan.scan falha com:
  asyncpg.exceptions.InvalidColumnReferenceError:
  there is no unique or exclusion constraint matching the ON CONFLICT specification

Fix: adiciona o constraint IF NOT EXISTS (idempotente em DBs que já têm ele).
"""

from alembic import op
import sqlalchemy as sa

revision = "066_fix_pipeline_assets_unique_constraint"
down_revision = "065_ttt_shadow_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM   pg_constraint
                WHERE  conname = 'uq_pipeline_asset_watchlist_symbol'
            ) THEN
                ALTER TABLE pipeline_watchlist_assets
                    ADD CONSTRAINT uq_pipeline_asset_watchlist_symbol
                    UNIQUE (watchlist_id, symbol);
            END IF;
        END $$;
    """))


def downgrade() -> None:
    op.execute(sa.text("""
        ALTER TABLE pipeline_watchlist_assets
            DROP CONSTRAINT IF EXISTS uq_pipeline_asset_watchlist_symbol;
    """))
