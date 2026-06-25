"""Add partial unique index on shadow_trades(decision_id) WHERE superseded_by_id IS NULL.

Revision ID: 110_shadow_decision_unique
Revises: 109_shadow_dup_audit
Create Date: 2026-06-24

Contexto (Profile Intelligence Adaptive Loop reformulation, audit 2026-06-24,
item 4 do checklist pos-VALIDACAO_GERAL):

  Pre-requisito desta migration: backend/scripts/fix_shadow_trade_duplicate_decision_id.py
  --commit ja rodou e marcou todos os 38 grupos historicos de decision_id
  duplicado (superseded_by_id preenchido nas 44 linhas nao-canonicas).
  Confirmado por query antes desta migration: 0 grupos com
  decision_id IS NOT NULL AND superseded_by_id IS NULL tendo COUNT(*) > 1.

  Sem esse backfill, este CREATE UNIQUE INDEX falharia (PostgreSQL nao cria
  indice unico sobre dados que já violam a unicidade).

  Com este indice, qualquer INSERT futuro com um decision_id ja usado por
  uma linha canonica (superseded_by_id IS NULL) viola a constraint. A
  query de insercao em shadow_trade_service.py (_INSERT_SHADOW_SQL) ja usa
  "ON CONFLICT DO NOTHING" SEM target explicito — em Postgres isso cobre
  QUALQUER constraint unica da tabela, incluindo esta nova, sem precisar
  alterar a query de insercao.

  asyncpg: cada op.execute() contem exatamente um statement.
"""

from alembic import op


revision = "110_shadow_decision_unique"
down_revision = "109_shadow_dup_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE UNIQUE INDEX ux_shadow_trades_decision_id_canonical "
        "ON shadow_trades (decision_id) "
        "WHERE decision_id IS NOT NULL AND superseded_by_id IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX ux_shadow_trades_decision_id_canonical")
