"""Create label_lab_runs table.

Revision ID: 107_label_lab_runs
Revises: 106_shadow_ml_lineage
Create Date: 2026-06-24

Contexto (Profile Intelligence Adaptive Loop reformulation, audit 2026-06-24,
Fase 5 da especificacao de implementacao):

  Label Lab (backend/app/services/profile_intelligence_label_lab.py) decide,
  ANTES de qualquer treino, se uma definicao de label (label_version +
  target_window_seconds + source_filter) tem dados suficientes e balanceados
  para ser aprendida. v41/v42 (is_tp_4h_v1) foram treinados sem essa checagem
  e o AUC de teste colapsou (0.497 e 0.422) — descoberto so depois do treino
  completo.

  label_lab_runs grava cada avaliacao (mesmo as que resultam em
  INSUFFICIENT_SAMPLES ou DEGENERATE_CLASS_BALANCE) para auditoria e para que
  o Profile Intelligence Feedback Engine (Fase 11) possa consultar o
  historico de viabilidade ao decidir se vale a pena re-treinar.

  Tabela aditiva, somente-INSERT.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision = "107_label_lab_runs"
down_revision = "106_shadow_ml_lineage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "label_lab_runs",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("label_version", sa.String, nullable=False),
        sa.Column("target_window_seconds", sa.Integer, nullable=False),
        sa.Column("source_filter", sa.String, nullable=True),
        sa.Column("status", sa.String, nullable=False),
        sa.Column("reasons", JSONB, nullable=False, server_default="[]"),
        sa.Column("thresholds", JSONB, nullable=False, server_default="{}"),
        sa.Column("metrics", JSONB, nullable=False, server_default="{}"),
        sa.Column("by_source", JSONB, nullable=False, server_default="{}"),
        sa.Column("triggered_by", sa.String, nullable=True),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index(
        "ix_label_lab_runs_label_version_evaluated_at",
        "label_lab_runs", ["label_version", "evaluated_at"],
    )
    op.execute(
        "COMMENT ON TABLE label_lab_runs IS "
        "'Audit trail of every Label Lab viability evaluation — run BEFORE "
        "training, not after. See backend/app/services/profile_intelligence_label_lab.py.'"
    )


def downgrade() -> None:
    op.drop_index("ix_label_lab_runs_label_version_evaluated_at", table_name="label_lab_runs")
    op.drop_table("label_lab_runs")
