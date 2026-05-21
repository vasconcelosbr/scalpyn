"""Add shadow_trades.source — distinguish L3 vs ARROW promotions.

Revision ID: 060_shadow_source
Revises: 059_tt_exit_metrics_json
Create Date: 2026-05-21

Contexto
--------
Task #321: a aba Arrow em ``/decisions`` e ``/dashboard/shadow-portfolio``
precisa mostrar APENAS os shadow trades promovidos a partir da watchlist
custom ``ArrowL1`` (profile "Arrow"), separados dos shadow trades L3
canônicos. Sem uma coluna de origem, as duas abas se misturam e quebram
a expectativa do usuário de "métricas por origem" (P&L Arrow vs P&L L3).

Fix (aditivo)
-------------
* Coluna ``source VARCHAR(20) NOT NULL DEFAULT 'L3'`` em
  ``shadow_trades``. Linhas pré-deploy ficam em ``'L3'`` (comportamento
  histórico — toda promoção antes desta task vinha do gate L3).
* Index parcial ``ix_shadow_trades_source`` para filtros frequentes
  da UI (``WHERE source = 'ARROW'``).

Rule N/N+1
----------
Coluna NÃO entra em ``_critical_schema.CRITICAL_COLUMNS`` neste deploy.
Promoção fica para deploy N+1 após 1 semana de observação (mesmo padrão
das migrations 053/056/057).

ID curto obrigatório (gotcha 2026-05-15): ``alembic_version.version_num``
é ``VARCHAR(32)``. Este ID tem 17 chars — dentro do limite.
"""

from alembic import op


revision = "060_shadow_source"
down_revision = "059_tt_exit_metrics_json"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE shadow_trades "
        "ADD COLUMN IF NOT EXISTS source VARCHAR(20) NOT NULL DEFAULT 'L3'"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_shadow_trades_source "
        "ON shadow_trades (source)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_shadow_trades_source")
    op.execute("ALTER TABLE shadow_trades DROP COLUMN IF EXISTS source")
