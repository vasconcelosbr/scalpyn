"""Add orchestrator_payload JSONB to shadow_trades.

Revision ID: 102_shadow_trades_orchestrator_payload
Revises: 101_ml_models_contract
Create Date: 2026-06-21

Contexto:
  - orchestrator_payload: armazena p_l1_win, p_l3_profile_win, reason_codes,
    l1_model_id, l3_model_id, weights e scored_at do Decision Orchestrator.
  - ml_probability (campo existente) permanece intacto — pertence ao modelo
    original da decisão e NÃO deve ser sobrescrito pelo orquestrador.
  - final_priority_score (campo existente) recebe o score combinado L1+L3.
"""

from alembic import op
import sqlalchemy as sa


revision = "102_shadow_trades_orchestrator_payload"
down_revision = "101_ml_models_contract"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        ALTER TABLE shadow_trades
        ADD COLUMN IF NOT EXISTS orchestrator_payload JSONB
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_shadow_trades_orch_payload
        ON shadow_trades USING GIN (orchestrator_payload)
        WHERE orchestrator_payload IS NOT NULL
    """))


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS ix_shadow_trades_orch_payload"))
    op.execute(sa.text("ALTER TABLE shadow_trades DROP COLUMN IF EXISTS orchestrator_payload"))
