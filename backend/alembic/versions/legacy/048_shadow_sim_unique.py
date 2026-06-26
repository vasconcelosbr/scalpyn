"""Partial UNIQUE on trade_simulations(decision_id) WHERE source='SHADOW'.

Revision ID: 048_shadow_sim_unique
Revises: 047_shadow_unique_dec
Create Date: 2026-05-12

Context
-------
``ShadowTradeService.record_as_simulation`` (Fase 3) precisa de
idempotência hard em ``trade_simulations`` por ``decision_id`` quando
``source='SHADOW'``. Sem isso, duas execuções concorrentes do
``shadow_trade_monitor`` (ou mesmo retry após SIGKILL) podem inserir
duas simulações para a mesma promoção L3 — contaminando o dataset de
ML.

Não dá pra adicionar UNIQUE total em ``trade_simulations(decision_id)``
porque outras fontes (``SIMULATION``, ``BLOCK``, ``REAL``) também
escrevem com ``decision_id`` e podem reusar IDs de decisão diferentes
mas sobrepostos no espaço de simulação. UNIQUE PARCIAL com predicado
``WHERE source='SHADOW'`` resolve sem afetar outras fontes.

Pré-dedupe defensivo cobre o caso de duplicatas já criadas antes deste
fix (mantém a row mais antiga via ROW_NUMBER, mesmo padrão da
migration 047).

Rule N/N+1
----------
Esta tabela continua FORA de ``_critical_schema.py``.
"""

from alembic import op
import sqlalchemy as sa


revision = "048_shadow_sim_unique"
down_revision = "047_shadow_unique_dec"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Defensive dedupe: mantém uma única simulação SHADOW por decision_id
    # com desempate determinístico (created_at ASC, id ASC). Idempotente.
    op.execute(sa.text("""
        WITH ranked AS (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY decision_id
                       ORDER BY created_at ASC, id ASC
                   ) AS rn
              FROM trade_simulations
             WHERE source = 'SHADOW'
               AND decision_id IS NOT NULL
        )
        DELETE FROM trade_simulations s
              USING ranked r
              WHERE s.id = r.id
                AND r.rn > 1
    """))

    op.execute(sa.text("""
        CREATE UNIQUE INDEX IF NOT EXISTS
            ix_trade_simulations_shadow_decision_uniq
            ON trade_simulations (decision_id)
            WHERE source = 'SHADOW' AND decision_id IS NOT NULL
    """))


def downgrade() -> None:
    op.execute(sa.text("""
        DROP INDEX IF EXISTS ix_trade_simulations_shadow_decision_uniq
    """))
