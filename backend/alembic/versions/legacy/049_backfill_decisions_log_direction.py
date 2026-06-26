"""Backfill decisions_log.direction NULL → 'SPOT' (Task #292).

Revision ID: 049_backfill_dl_direction
Revises: 048_shadow_sim_unique
Create Date: 2026-05-12

Context
-------
Antes da Task #292, ``pipeline_scan._apply_robust_authoritative_scoring``
só populava ``asset["futures_direction"]`` no path ``is_futures=True``.
Para watchlists SPOT (caso atual: pool 100% spot), ``decisions_log.direction``
ficava NULL, travando o gate Shadow Portfolio que filtra por
``direction IN ('SPOT','LONG')``.

Esta migration é best-effort: assume que TODA decisão ALLOW recente
(últimos 7 dias) com ``direction=NULL`` é SPOT, baseado em:

  1. Pool produtivo atual é 100% spot (``pool_coins`` filtrado por
     ``is_active=true``).
  2. O fix de código (T001/T002) já está no deploy concomitante, então
     decisões NOVAS já vão entrar com ``direction='SPOT'``. Esta migration
     só limpa o histórico das últimas 7d para destravar o gate Shadow
     imediatamente, sem esperar o pipeline regenerar.
  3. Linhas > 7d ficam NULL — irrelevantes para Shadow (que promove
     decisões recentes via ``MAX(created_at)``).

Rule N/N+1
----------
NÃO adiciona ``NOT NULL`` constraint. Esperar 1 semana de observação
em prod com o fix antes de promover (Skill #7,
alembic-migration-guardrails).

Idempotência
------------
``UPDATE ... WHERE direction IS NULL`` — segunda execução é noop.
"""

from alembic import op
import sqlalchemy as sa


revision = "049_backfill_dl_direction"
down_revision = "048_shadow_sim_unique"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Per-statement cap; evita lock prolongado em decisions_log (hot table).
    op.execute(sa.text("SET LOCAL lock_timeout = '5s'"))
    op.execute(sa.text("""
        UPDATE decisions_log
           SET direction = 'SPOT'
         WHERE direction IS NULL
           AND decision = 'ALLOW'
           AND created_at > NOW() - INTERVAL '7 days'
    """))


def downgrade() -> None:
    # Reverter o backfill recoloca NULL nos registros que foram tocados
    # nesta migration. Como NÃO podemos distinguir entre 'SPOT' setado
    # pelo backfill vs 'SPOT' setado pelo pipeline novo (T001), o
    # downgrade só limpa LINHAS HISTÓRICAS (>2 dias atrás), preservando
    # o estado novo do pipeline. Janela conservadora.
    op.execute(sa.text("""
        UPDATE decisions_log
           SET direction = NULL
         WHERE direction = 'SPOT'
           AND decision = 'ALLOW'
           AND created_at < NOW() - INTERVAL '2 days'
    """))
