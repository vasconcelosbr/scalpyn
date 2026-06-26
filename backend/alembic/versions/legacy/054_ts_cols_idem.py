"""Idempotent ADD COLUMN time_to_result + source on trade_simulations.

Revision ID: 054_ts_cols_idem
Revises: 053_shadow_dir_canon
Create Date: 2026-05-15

Contexto
--------
Migration 025 criou ``trade_simulations`` via ``CREATE TABLE IF NOT EXISTS``
incluindo as colunas ``time_to_result INTEGER`` e ``source VARCHAR(30)``.
Em produção a tabela já existia (schema anterior), então 025 foi no-op
silencioso e as colunas nunca foram adicionadas.

Resultado: ``GET /api/dashboard/ml-dataset`` e ``/ml-dataset/export``
retornam 503 com::

    asyncpg.exceptions.UndefinedColumnError: column "time_to_result" does not exist

O mesmo padrão foi corrigido para ``decision_type`` na migration 050.

Fix: ADD COLUMN IF NOT EXISTS + backfill defensivo — ambas as operações
são idempotentes e seguras de re-executar.

ATENÇÃO — ID curto obrigatório: ``alembic_version.version_num`` é
``VARCHAR(32)`` em produção. IDs de revisão devem ter ≤ 32 caracteres
ou o ``alembic upgrade head`` quebra com
``StringDataRightTruncationError`` ao gravar a versão aplicada.

NÃO incluídas em CRITICAL_COLUMNS nesta migration (regra N/N+1).
"""

from alembic import op
import sqlalchemy as sa


revision = "054_ts_cols_idem"
down_revision = "053_shadow_dir_canon"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── time_to_result ────────────────────────────────────────────────────────
    # Nullable INTEGER — sem backfill necessário (valor correto é computado
    # no momento do fechamento da simulação; linhas antigas ficam NULL).
    op.execute(sa.text("""
        ALTER TABLE trade_simulations
            ADD COLUMN IF NOT EXISTS time_to_result INTEGER
    """))

    # ── source ────────────────────────────────────────────────────────────────
    # VARCHAR(30) com default 'SIMULATION' — backfill para linhas antigas
    # que não têm o campo (todas são simulações legadas).
    op.execute(sa.text("""
        ALTER TABLE trade_simulations
            ADD COLUMN IF NOT EXISTS source VARCHAR(30) DEFAULT 'SIMULATION'
    """))

    op.execute(sa.text("""
        UPDATE trade_simulations
           SET source = 'SIMULATION'
         WHERE source IS NULL
    """))

    # Índice parcial para unicidade shadow (idempotente).
    op.execute(sa.text("""
        CREATE UNIQUE INDEX IF NOT EXISTS ix_trade_simulations_shadow_decision_uniq
            ON trade_simulations (decision_id)
         WHERE source = 'SHADOW'
           AND decision_id IS NOT NULL
    """))


def downgrade() -> None:
    op.execute(sa.text(
        "DROP INDEX IF EXISTS ix_trade_simulations_shadow_decision_uniq"
    ))
    op.execute(sa.text(
        "ALTER TABLE trade_simulations DROP COLUMN IF EXISTS source"
    ))
    op.execute(sa.text(
        "ALTER TABLE trade_simulations DROP COLUMN IF EXISTS time_to_result"
    ))
