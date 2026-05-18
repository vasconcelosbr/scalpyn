"""Shadow trades: ciclo de renovação contínua por símbolo aprovado.

Revision ID: 056_shadow_renew_cycle
Revises: 055_sim_cols_safety_net
Create Date: 2026-05-18

Contexto
--------
Migration 047 adicionou ``UNIQUE INDEX ix_shadow_trades_decision_id_uniq``
sobre ``decision_id``.  Isso garantia "uma shadow por decisão" — correto
para o modelo original onde cada transição BLOCK→ALLOW gerava uma nova linha
em ``decisions_log`` e uma nova shadow associada.

Porém ``pipeline_scan._should_log_decision`` só grava em ``decisions_log``
em **transições de estado** (BLOCK→ALLOW, ALLOW→ALLOW com delta de score >5
etc.).  Símbolo em ALLOW estável NÃO gera nova linha.  Resultado: após uma
shadow fechar por TP/SL, o símbolo continua aprovado em L3 mas a tentativa
de criar nova shadow conflita no ``decision_id`` já existente → nenhuma nova
shadow é criada → o requisito de negócio ("manter shadow ativo enquanto o
ativo estiver na L3") quebra silenciosamente.

Fix
---
* Dropa ``ix_shadow_trades_decision_id_uniq`` (UNIQUE por decision_id).
* Adiciona índice parcial único ``ux_shadow_running_user_symbol``:
  ``(user_id, symbol) WHERE status = 'RUNNING'`` — garante no máximo uma
  shadow RUNNING por ativo por usuário (regra de negócio correta), sem
  impedir múltiplas shadows históricas (COMPLETED/CANCELLED) pelo mesmo
  ativo/decisão.
* Mantém index regular ``ix_shadow_trades_decision_id`` para FK-lookup.

O serviço passa a usar
``ON CONFLICT (user_id, symbol) WHERE status = 'RUNNING' DO NOTHING``.
"""

from alembic import op
import sqlalchemy as sa


revision = "056_shadow_renew_cycle"
down_revision = "055_sim_cols_safety_net"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Dropa o índice único antigo por decision_id
    op.execute(sa.text("""
        DROP INDEX IF EXISTS ix_shadow_trades_decision_id_uniq
    """))

    # 2. Recria índice regular (não-único) para FK-lookup eficiente
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_shadow_trades_decision_id
            ON shadow_trades (decision_id)
    """))

    # 3. Índice parcial único: apenas uma RUNNING shadow por (user_id, symbol)
    op.execute(sa.text("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_shadow_running_user_symbol
            ON shadow_trades (user_id, symbol)
            WHERE status = 'RUNNING'
    """))


def downgrade() -> None:
    op.execute(sa.text("""
        DROP INDEX IF EXISTS ux_shadow_running_user_symbol
    """))
    op.execute(sa.text("""
        DROP INDEX IF EXISTS ix_shadow_trades_decision_id
    """))
    op.execute(sa.text("""
        CREATE UNIQUE INDEX IF NOT EXISTS ix_shadow_trades_decision_id_uniq
            ON shadow_trades (decision_id)
    """))
