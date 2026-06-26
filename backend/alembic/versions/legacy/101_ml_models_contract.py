"""Add label_version, dataset_contract_id, model_lane to ml_models.

Revision ID: 101_ml_models_contract
Revises: 100_pi_run_trigger_source
Create Date: 2026-06-21

Contexto:
  - label_version: rastreia a definição de label usada no treino (ex.: "is_win_fast_v1")
    Necessário para governance: saber que dois modelos foram treinados com labels incompatíveis.
  - dataset_contract_id: identificador estável da "promessa" de dataset (schema + label + fontes).
    Permite verificar se um modelo foi treinado com o mesmo contrato que o atual.
  - model_lane: identifica a lane de arquitetura ("L1_SPECTRUM" ou "L3_PROFILE").
    Separa os dois modelos da arquitetura 2-lanes e evita promoção cruzada.
"""

from alembic import op
import sqlalchemy as sa


revision = "101_ml_models_contract"
down_revision = "100_pi_run_trigger_source"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        ALTER TABLE ml_models
        ADD COLUMN IF NOT EXISTS label_version VARCHAR(50)
    """))
    op.execute(sa.text("""
        ALTER TABLE ml_models
        ADD COLUMN IF NOT EXISTS dataset_contract_id VARCHAR(100)
    """))
    op.execute(sa.text("""
        ALTER TABLE ml_models
        ADD COLUMN IF NOT EXISTS model_lane VARCHAR(30)
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_ml_models_lane
        ON ml_models (model_lane)
        WHERE model_lane IS NOT NULL
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_ml_models_label_version
        ON ml_models (label_version)
        WHERE label_version IS NOT NULL
    """))


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS ix_ml_models_label_version"))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_ml_models_lane"))
    op.execute(sa.text("ALTER TABLE ml_models DROP COLUMN IF EXISTS model_lane"))
    op.execute(sa.text("ALTER TABLE ml_models DROP COLUMN IF EXISTS dataset_contract_id"))
    op.execute(sa.text("ALTER TABLE ml_models DROP COLUMN IF EXISTS label_version"))
