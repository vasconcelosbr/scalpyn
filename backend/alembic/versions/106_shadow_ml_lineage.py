"""Add model_lane and ranking_id to shadow_trades for inline ML lineage.

Revision ID: 106_shadow_ml_lineage
Revises: 105_ml_opp_rankings
Create Date: 2026-06-24

Contexto (Profile Intelligence Adaptive Loop reformulation, audit 2026-06-24,
Fase 8 da especificacao de implementacao):

  shadow_trades ja tinha ml_model_id / ml_probability / final_priority_score
  (migration 102, orchestrator_payload) mas NENHUM INSERT em todo o codebase
  preenchia essas colunas no momento da criacao do shadow — ficavam NULL
  ate alguem chamar manualmente POST /api/ml/orchestrator/backfill.

  Faltavam ainda duas colunas para fechar a linhagem:
    model_lane  VARCHAR NULL  — 'L1_SPECTRUM' | 'L3_PROFILE', a mesma lane
                                 usada pelo Promotion Gate (migration 105/
                                 backend/app/ml/promotion_gate.py). Sem isso
                                 nao e possivel saber, so olhando o shadow,
                                 qual lane gerou o ml_probability gravado.
    ranking_id  UUID NULL FK ml_opportunity_rankings.id — liga o shadow a
                                 linha exata do ML Opportunity Ranking job
                                 (Fase 6, ainda nao implementada) que produziu
                                 o score. NULL ate o job existir e comecar a
                                 popular ml_opportunity_rankings.

  asyncpg: cada op.execute() contem exatamente um statement.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "106_shadow_ml_lineage"
down_revision = "105_ml_opp_rankings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "shadow_trades",
        sa.Column("model_lane", sa.String, nullable=True),
    )
    op.add_column(
        "shadow_trades",
        sa.Column("ranking_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_shadow_trades_ranking_id",
        "shadow_trades", "ml_opportunity_rankings",
        ["ranking_id"], ["id"],
    )
    op.execute(
        "COMMENT ON COLUMN shadow_trades.model_lane IS "
        "'ML lane (L1_SPECTRUM | L3_PROFILE) that produced ml_probability, "
        "when computed inline at shadow creation. NULL for older rows.'"
    )
    op.execute(
        "COMMENT ON COLUMN shadow_trades.ranking_id IS "
        "'FK to ml_opportunity_rankings.id — full lineage of the score that "
        "produced this shadow, when available.'"
    )


def downgrade() -> None:
    op.drop_constraint("fk_shadow_trades_ranking_id", "shadow_trades", type_="foreignkey")
    op.drop_column("shadow_trades", "ranking_id")
    op.drop_column("shadow_trades", "model_lane")
