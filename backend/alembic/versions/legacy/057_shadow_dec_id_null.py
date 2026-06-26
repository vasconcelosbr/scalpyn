"""Shadow trades: torna decision_id nullable para suportar fallback L3 vivo.

Revision ID: 057_shadow_dec_id_null
Revises: 056_shadow_renew_cycle
Create Date: 2026-05-19

Contexto (Task #303)
--------------------
``pipeline_scan._should_log_decision`` só grava em ``decisions_log`` em
transições de estado. Símbolos em ALLOW estável (BTC/ETH/SOL no print
original) NUNCA tiveram linha em ``decisions_log`` — o Shadow ignorava
eles porque ``_resolve_decision`` retornava None.

A correção é fazer o resolver cair pra um snapshot vivo da L3
(``pipeline_watchlist_assets``) quando não há DecisionLog. Esse path
precisa criar a shadow SEM ``decision_id`` (não existe linha em
``decisions_log`` pra apontar). A coluna era ``NOT NULL`` desde a criação
do shadow_trades — relaxamos aqui para suportar shadows sintéticas de
fonte ``live_l3``.

Idempotente: ALTER COLUMN ... DROP NOT NULL é metadata-only no Postgres,
custa microssegundos. Shadow_trades NÃO é hot table (sem writer concorrente
no path crítico do collector), então é seguro rodar na janela de cold start.
"""

from alembic import op
import sqlalchemy as sa


revision = "057_shadow_dec_id_null"
down_revision = "056_shadow_renew_cycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        ALTER TABLE shadow_trades
            ALTER COLUMN decision_id DROP NOT NULL
    """))


def downgrade() -> None:
    # Backfill defensivo antes do NOT NULL: shadows sintéticas (decision_id
    # NULL) precisam ser limpas ou linkadas. Em prod assumimos que ninguém
    # rolará o downgrade com dados sintéticos no ar — apenas re-aplica o
    # NOT NULL idempotente.
    op.execute(sa.text("""
        ALTER TABLE shadow_trades
            ALTER COLUMN decision_id SET NOT NULL
    """))
