"""Add shadow_trades.features_snapshot_exit — snapshot dos indicadores
no momento em que TP/SL/TIMEOUT é atingido.

Revision ID: 051_shadow_exit_snap
Revises: 050_ts_decision_type_idem
Create Date: 2026-05-13

Contexto
--------
Até esta migração, ``shadow_trades.features_snapshot`` (entry) era o
único snapshot persistido. O ML conseguia aprender "indicadores que
levaram à entrada" mas NÃO "indicadores no momento da saída". O usuário
pediu explicitamente: "quando bater o alvo todos indicadores deverão
ser registrados … rsi, adx, volume e todos os indicadores envolvidos.
o ML vai estudar estes dados".

Fix
---
* Coluna ``features_snapshot_exit JSONB NULL`` (nullable — old rows
  e shadows ainda RUNNING ficam em NULL até bater outcome).
* O monitor (`shadow_trade_monitor._advance_shadow`) preenche essa
  coluna no momento em que `outcome` é setado (TP/SL/TIMEOUT) usando
  ``indicators_provider.get_merged_indicators`` +
  ``build_indicators_snapshot`` flatten — mesmo formato do entry.

Rule N/N+1
----------
Coluna fora de ``_critical_schema.py`` (não bloqueia startup). Pode
ser adicionada no próximo deploy se a leitura virar crítica.
"""

from alembic import op


revision = "051_shadow_exit_snap"
down_revision = "050_ts_decision_type_idem"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE shadow_trades "
        "ADD COLUMN IF NOT EXISTS features_snapshot_exit JSONB NULL"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE shadow_trades DROP COLUMN IF EXISTS features_snapshot_exit"
    )
