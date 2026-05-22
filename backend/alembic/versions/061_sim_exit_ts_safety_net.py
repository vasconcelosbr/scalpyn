"""Safety-net: add exit_timestamp + exit_price to trade_simulations.

Revision ID: 061_sim_exit_ts_safety_net
Revises: 060_shadow_source
Create Date: 2026-05-21

Contexto
--------
Migration 025 criou ``trade_simulations`` via ``CREATE TABLE IF NOT EXISTS``
incluindo ``exit_timestamp TIMESTAMPTZ`` e ``exit_price NUMERIC(20,8)``.
Em produção a tabela já existia (schema anterior sem essas colunas), então
025 foi no-op silencioso e as colunas nunca foram adicionadas.

O mesmo padrão foi corrigido para ``decision_type`` em 050, para
``time_to_result``/``source`` em 054/055, para ``timestamp_entry`` em 058.

Resultado: SELECT/INSERT em ``trade_simulations`` levanta
``UndefinedColumnError: column "exit_timestamp" does not exist at character 136``
(detectado nos logs Cloud SQL 2026-05-21 15:00:31 UTC, PID 370385).

Fix: ADD COLUMN IF NOT EXISTS para ambas as colunas.  Nullable para
compatibilidade com linhas existentes (trades ainda não encerrados).
Idempotente — seguro re-executar.

ATENÇÃO — ID curto obrigatório: ``alembic_version.version_num`` é
``VARCHAR(32)``.  Este ID tem 26 chars — dentro do limite.
"""

from alembic import op
import sqlalchemy as sa


revision = "061_sim_exit_ts_safety_net"
down_revision = "060_shadow_source"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── exit_timestamp ────────────────────────────────────────────────────────
    # Nullable: trades ainda abertos não têm timestamp de saída.
    op.execute(sa.text("""
        ALTER TABLE trade_simulations
            ADD COLUMN IF NOT EXISTS exit_timestamp TIMESTAMPTZ
    """))

    # ── exit_price ────────────────────────────────────────────────────────────
    # Nullable: trades ainda abertos não têm preço de saída.
    op.execute(sa.text("""
        ALTER TABLE trade_simulations
            ADD COLUMN IF NOT EXISTS exit_price NUMERIC(20, 8)
    """))

    # ── Índice em exit_timestamp (idempotente) ────────────────────────────────
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_trade_simulations_exit_timestamp
            ON trade_simulations (exit_timestamp)
    """))


def downgrade() -> None:
    op.execute(sa.text(
        "DROP INDEX IF EXISTS idx_trade_simulations_exit_timestamp"
    ))
    op.execute(sa.text(
        "ALTER TABLE trade_simulations DROP COLUMN IF EXISTS exit_price"
    ))
    op.execute(sa.text(
        "ALTER TABLE trade_simulations DROP COLUMN IF EXISTS exit_timestamp"
    ))
