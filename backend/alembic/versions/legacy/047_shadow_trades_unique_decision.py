"""Add UNIQUE(decision_id) on shadow_trades — close idempotency race.

Revision ID: 047_shadow_trades_unique_decision
Revises: 046_shadow_trades
Create Date: 2026-05-12

Context
-------
Migration 046 criou ``shadow_trades.decision_id`` como FK + index não
único. ``ShadowTradeService._create_from_decision`` (Fase 2)
deduplicava em duas etapas (SELECT existence-check + INSERT separado),
o que abre janela de race entre coroutines/workers concorrentes:
duas TXs podem passar o SELECT antes que qualquer uma INSERTe,
resultando em duplicatas para a mesma promoção L3.

Fix
---
* Adiciona ``UNIQUE INDEX ix_shadow_trades_decision_id_uniq`` que
  serve TANTO como dedup hard-stop no banco quanto como índice de
  busca (substitui o ``ix_shadow_trades_decision_id`` não-único da
  046, que vira redundante).
* O serviço passa a usar ``INSERT ... ON CONFLICT (decision_id) DO
  NOTHING RETURNING id`` (atomic, sem race).

A migração faz pré-dedupe defensivo (caso já existam duplicatas em
ambientes onde a 046 rodou + Fase 2 inseriu antes deste fix), mantendo
sempre a row mais antiga por ``decision_id``.

Rule N/N+1
----------
Esta tabela continua FORA de ``_critical_schema.py`` neste deploy.
"""

from alembic import op
import sqlalchemy as sa


revision = "047_shadow_unique_dec"
down_revision = "046_shadow_trades"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Defensive dedupe: se algum ambiente já tem duplicatas (Fase 2
    # rodando antes deste fix), mantém uma única row por decision_id
    # com desempate determinístico (created_at ASC, id ASC) — cobre
    # também o caso de created_at empatado (duas TXs concorrentes
    # podem inserir com mesmo NOW() sub-microssegundo). Idempotente.
    op.execute(sa.text("""
        WITH ranked AS (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY decision_id
                       ORDER BY created_at ASC, id ASC
                   ) AS rn
              FROM shadow_trades
        )
        DELETE FROM shadow_trades s
              USING ranked r
              WHERE s.id = r.id
                AND r.rn > 1
    """))

    # UNIQUE index serve tanto como hard-stop quanto como índice de
    # busca. O índice não-único ix_shadow_trades_decision_id da 046
    # vira redundante — drop pra não duplicar.
    op.execute(sa.text("""
        CREATE UNIQUE INDEX IF NOT EXISTS ix_shadow_trades_decision_id_uniq
            ON shadow_trades (decision_id)
    """))
    op.execute(sa.text("""
        DROP INDEX IF EXISTS ix_shadow_trades_decision_id
    """))


def downgrade() -> None:
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_shadow_trades_decision_id
            ON shadow_trades (decision_id)
    """))
    op.execute(sa.text("""
        DROP INDEX IF EXISTS ix_shadow_trades_decision_id_uniq
    """))
