"""Create ml_opportunity_rankings table.

Revision ID: 105_ml_opp_rankings
Revises: 104_ml_metrics_json
Create Date: 2026-06-24

Contexto (Profile Intelligence Adaptive Loop reformulation, audit 2026-06-24,
Fase 4 da especificacao de implementacao):

  Hoje nao existe nenhum registro persistente e auditavel de "o ML Opportunity
  Ranking gerou este score, com este modelo, nesta lane, neste momento" antes
  de o score ser consumido por decision_orchestrator/pipeline_scan/watchlists.
  Sem essa tabela, uma regra absoluta do projeto ("todo score de ML usado em
  uma decisao ou Shadow deve ter linhagem completa") nao pode ser cumprida —
  nao ha onde gravar de qual model_id/model_lane/dataset_contract_id um score
  veio, nem se o Promotion Gate estava APPROVED no momento do ranking.

  ml_opportunity_rankings e uma tabela aditiva, somente-INSERT (nenhum
  processo existente e alterado por esta migration). Sera populada pelo ML
  Opportunity Ranking job (Fase 6) e referenciada por shadow_trades (Fase 8)
  e decisions_log via decision_id quando aplicavel.

  run_id agrupa todas as linhas produzidas por uma unica execucao do job
  (permite reconstruir "o ranking completo daquele ciclo").

  score_status / reason_code seguem o mesmo contrato de
  prediction_service.predict(): 'OK' | 'SKIPPED' com reason_code
  'NO_ELIGIBLE_MODEL_FOR_LANE' | 'model_unavailable_fail_closed', nunca um
  score inferido silenciosamente quando o modelo nao estava disponivel.

  asyncpg: cada op.execute() contem exatamente um statement.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision = "105_ml_opp_rankings"
down_revision = "104_ml_metrics_json"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ml_opportunity_rankings",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("run_id", UUID(as_uuid=True), nullable=False),
        sa.Column("symbol", sa.String, nullable=False),
        sa.Column("profile_id", UUID(as_uuid=True), nullable=True),
        sa.Column("watchlist_id", UUID(as_uuid=True), nullable=True),
        sa.Column("decision_id", sa.BigInteger, nullable=True),
        sa.Column("model_lane", sa.String, nullable=True),
        sa.Column("model_id", UUID(as_uuid=True), nullable=True),
        sa.Column("model_version", sa.String, nullable=True),
        sa.Column("dataset_contract_id", sa.String, nullable=True),
        sa.Column("promotion_gate_status", sa.String, nullable=True),
        sa.Column("win_fast_probability", sa.Float, nullable=True),
        sa.Column("p_l1_win", sa.Float, nullable=True),
        sa.Column("p_l3_profile_win", sa.Float, nullable=True),
        sa.Column("final_priority_score", sa.Float, nullable=True),
        sa.Column("rank_position", sa.Integer, nullable=True),
        sa.Column("score_status", sa.String, nullable=False, server_default="SKIPPED"),
        sa.Column("reason_code", sa.String, nullable=True),
        sa.Column("source", sa.String, nullable=False),
        sa.Column("features_snapshot", JSONB, nullable=True),
        sa.Column("ranked_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index(
        "ix_ml_opportunity_rankings_run_id", "ml_opportunity_rankings", ["run_id"]
    )
    op.create_index(
        "ix_ml_opportunity_rankings_symbol_ranked_at",
        "ml_opportunity_rankings", ["symbol", "ranked_at"]
    )
    op.create_index(
        "ix_ml_opportunity_rankings_decision_id", "ml_opportunity_rankings", ["decision_id"]
    )
    op.create_index(
        "ix_ml_opportunity_rankings_model_lane", "ml_opportunity_rankings", ["model_lane"]
    )
    op.execute(
        "COMMENT ON TABLE ml_opportunity_rankings IS "
        "'Auditable lineage of every ML Opportunity Ranking score, one row per (run_id, symbol). "
        "Populated by the ML Opportunity Ranking job, consumed by Shadow lineage and decisions_log.'"
    )


def downgrade() -> None:
    op.drop_index("ix_ml_opportunity_rankings_model_lane", table_name="ml_opportunity_rankings")
    op.drop_index("ix_ml_opportunity_rankings_decision_id", table_name="ml_opportunity_rankings")
    op.drop_index("ix_ml_opportunity_rankings_symbol_ranked_at", table_name="ml_opportunity_rankings")
    op.drop_index("ix_ml_opportunity_rankings_run_id", table_name="ml_opportunity_rankings")
    op.drop_table("ml_opportunity_rankings")
