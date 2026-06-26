"""Safety-net: re-apply time_to_result + source on trade_simulations.

Revision ID: 055_sim_cols_safety_net
Revises: 054_ts_cols_idem
Create Date: 2026-05-15

Contexto
--------
Migration 054 adicionou ``time_to_result`` e ``source`` a
``trade_simulations`` via ``ADD COLUMN IF NOT EXISTS``.  Em produção, se
``alembic upgrade head`` falhou por lock contention e o fallback
``alembic stamp head`` foi usado, a ``alembic_version`` avança para
``054_ts_cols_idem`` MAS o DDL NUNCA foi executado — colunas seguem
ausentes.  Resultado: ``record_as_simulation`` levanta
``UndefinedColumnError``, a exceção é capturada em Python mas o asyncpg
marca a transação como ABORTED; quando SQLAlchemy tenta COMMIT, o
PostgreSQL faz ROLLBACK silencioso, desfazendo ``shadow.status='COMPLETED'``
— trade volta para RUNNING mesmo depois de 24h+ acima do TP.

Esta migration é um safety net idempotente: aplica exatamente o mesmo DDL
de 054 (``IF NOT EXISTS`` em tudo) para garantir convergência
independentemente do estado real do schema.  Pode ser aplicada sobre um
banco que JÁ tem as colunas (no-op) ou sobre um que nunca as recebeu.

ATENÇÃO — ID curto obrigatório: ``alembic_version.version_num`` é
``VARCHAR(32)``.  Este ID tem 23 chars — dentro do limite.
"""

from alembic import op
import sqlalchemy as sa


revision = "055_sim_cols_safety_net"
down_revision = "054_ts_cols_idem"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── time_to_result ────────────────────────────────────────────────────────
    op.execute(sa.text("""
        ALTER TABLE trade_simulations
            ADD COLUMN IF NOT EXISTS time_to_result INTEGER
    """))

    # ── source ────────────────────────────────────────────────────────────────
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
    # ATENÇÃO — semântica de rollback parcial:
    # Este downgrade remove as colunas/índice que tanto 054 quanto 055
    # definem.  Fazendo downgrade APENAS de 055 (sem fazer downgrade de 054),
    # o schema ficaria divergente do estado que 054 deveria ter produzido.
    # Isso é aceitável para um workflow prod-forward (nunca se faz downgrade
    # seletivo em produção), mas deve ser observado em ambientes de teste:
    # após ``alembic downgrade 054_ts_cols_idem`` as colunas ainda
    # existem (055 não rodou seu downgrade); após
    # ``alembic downgrade base`` ambas as migrations fazem drop.
    # Em produção, o caminho de rollback recomendado é sempre
    # ``alembic downgrade -1`` (sequencial), nunca downgrade seletivo.
    op.execute(sa.text(
        "DROP INDEX IF EXISTS ix_trade_simulations_shadow_decision_uniq"
    ))
    op.execute(sa.text(
        "ALTER TABLE trade_simulations DROP COLUMN IF EXISTS source"
    ))
    op.execute(sa.text(
        "ALTER TABLE trade_simulations DROP COLUMN IF EXISTS time_to_result"
    ))
