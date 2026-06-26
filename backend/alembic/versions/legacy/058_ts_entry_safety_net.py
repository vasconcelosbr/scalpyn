"""Safety-net: add timestamp_entry to trade_simulations.

Revision ID: 058_ts_entry_safety_net
Revises: 057_shadow_dec_id_null
Create Date: 2026-05-20

Contexto
--------
Migration 025 criou ``trade_simulations`` via ``CREATE TABLE IF NOT EXISTS``
incluindo ``timestamp_entry TIMESTAMPTZ NOT NULL``.  Em produção a tabela
já existia (schema anterior sem essa coluna), então 025 foi no-op silencioso
e a coluna nunca foi adicionada.

Resultado: INSERT em ``trade_simulations`` levanta
``UndefinedColumnError: column "timestamp_entry" does not exist at character 58``
(detectado nos logs Cloud SQL 2026-05-20 16:19:42 UTC).

O mesmo padrão foi corrigido para ``decision_type`` em 050, para
``time_to_result``/``source`` em 054/055.

Fix: ADD COLUMN IF NOT EXISTS + backfill de ``created_at`` para linhas
existentes + UNIQUE constraint + índices.  Idempotente — seguro re-executar.

ATENÇÃO — ID curto obrigatório: ``alembic_version.version_num`` é
``VARCHAR(32)``.  Este ID tem 23 chars — dentro do limite.
"""

from alembic import op
import sqlalchemy as sa


revision = "058_ts_entry_safety_net"
down_revision = "057_shadow_dec_id_null"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── timestamp_entry ───────────────────────────────────────────────────────
    # Adicionada como nullable para compatibilidade com linhas existentes.
    # Migration 025 declarava NOT NULL, mas linhas legadas não têm valor —
    # NOT NULL seria imposto com UPDATE + ALTER COLUMN após backfill.
    op.execute(sa.text("""
        ALTER TABLE trade_simulations
            ADD COLUMN IF NOT EXISTS timestamp_entry TIMESTAMPTZ
    """))

    # Backfill: linhas existentes recebem created_at como proxy do timestamp
    # de entrada (melhor aproximação disponível para dados legados).
    op.execute(sa.text("""
        UPDATE trade_simulations
           SET timestamp_entry = created_at
         WHERE timestamp_entry IS NULL
    """))

    # ── UNIQUE constraint ─────────────────────────────────────────────────────
    # Necessária para ``ON CONFLICT (symbol, timestamp_entry, direction) DO NOTHING``
    # em simulation_repository.bulk_insert_simulations.
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.table_constraints
                 WHERE table_name = 'trade_simulations'
                   AND constraint_name = 'uq_simulation_symbol_entry_direction'
            ) THEN
                ALTER TABLE trade_simulations
                    ADD CONSTRAINT uq_simulation_symbol_entry_direction
                    UNIQUE (symbol, timestamp_entry, direction);
            END IF;
        END $$
    """))

    # ── Índices (idempotentes) ────────────────────────────────────────────────
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_trade_simulations_timestamp_entry
            ON trade_simulations (timestamp_entry)
    """))

    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_trade_simulations_symbol_timestamp
            ON trade_simulations (symbol, timestamp_entry DESC)
    """))


def downgrade() -> None:
    op.execute(sa.text(
        "DROP INDEX IF EXISTS idx_trade_simulations_symbol_timestamp"
    ))
    op.execute(sa.text(
        "DROP INDEX IF EXISTS idx_trade_simulations_timestamp_entry"
    ))
    op.execute(sa.text("""
        ALTER TABLE trade_simulations
            DROP CONSTRAINT IF EXISTS uq_simulation_symbol_entry_direction
    """))
    op.execute(sa.text(
        "ALTER TABLE trade_simulations DROP COLUMN IF EXISTS timestamp_entry"
    ))
