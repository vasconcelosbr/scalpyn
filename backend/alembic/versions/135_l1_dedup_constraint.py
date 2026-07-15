"""Fase 1.3 (Passo 2) — idempotência do capture L1_SPECTRUM.

Revision ID: 135_l1_dedup_constraint
Revises: 134_fase1_integrity_cert
Create Date: 2026-07-15

Causa-raiz (I10): create_l1_spectrum_shadows amostra com hash(symbol:execution_id)
e o execution_id muda a cada scan → decisão de amostragem independente entre
ciclos do pipeline_scan (cadência 5 min). Dois scans consecutivos recapturam o
mesmo símbolo no mesmo entry candle, sem dedup por chave natural. Hipótese
H-overlap confirmada (event_ids distintos por linha; acks_late=False descarta
H-retry; deltas de minutos descartam H-doubleworker).

Fix: índice único parcial por chave natural (user_id, symbol, entry_timestamp)
para source='L1_SPECTRUM'. O INSERT em _create_from_decision trata o
IntegrityError deste índice retornando None (skip idempotente), espelhando o
tratamento de uq_shadow_lab_active_profile_symbol.

Escopo full-L1 aprovado pelo operador (Fase 1.3 Passo 2.2, opção B). O dedup
por id das 21 linhas conhecidas foi executado read/write antes desta migration;
o DELETE defensivo abaixo (mantém menor created_at por grupo) é um guard
idempotente contra qualquer duplicata criada na janela entre o dedup e o deploy
— sem ele, uma corrida faria o CREATE UNIQUE INDEX falhar e a API entrar em
crash-loop no boot.

Revision id mantido <= 32 chars (alembic_version.version_num é VARCHAR(32)).
"""

from alembic import op
from sqlalchemy import text


revision = "135_l1_dedup_constraint"
down_revision = "134_fase1_integrity_cert"
branch_labels = None
depends_on = None

_INDEX_NAME = "ux_shadow_l1_symbol_entry"


def upgrade() -> None:
    # Guard defensivo: remove qualquer duplicata L1 residual mantendo a de menor
    # created_at por (user_id, symbol, entry_timestamp). Statement único
    # (asyncpg não aceita múltiplos statements por op.execute).
    op.execute(text(
        """
        DELETE FROM shadow_trades s
        USING (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY user_id, symbol, entry_timestamp
                       ORDER BY created_at ASC
                   ) AS rn
            FROM shadow_trades
            WHERE source = 'L1_SPECTRUM' AND entry_timestamp IS NOT NULL
        ) d
        WHERE s.id = d.id AND d.rn > 1
        """
    ))
    op.execute(text(
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS {_INDEX_NAME}
        ON shadow_trades (user_id, symbol, entry_timestamp)
        WHERE source = 'L1_SPECTRUM' AND entry_timestamp IS NOT NULL
        """
    ))


def downgrade() -> None:
    op.execute(text(f"DROP INDEX IF EXISTS {_INDEX_NAME}"))
