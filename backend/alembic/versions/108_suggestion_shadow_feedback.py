"""Add shadow_feedback_status/shadow_feedback_json to profile_suggestions.

Revision ID: 108_suggestion_feedback
Revises: 107_label_lab_runs
Create Date: 2026-06-24

Contexto (Profile Intelligence Adaptive Loop reformulation, audit 2026-06-24,
Fase 11 da especificacao de implementacao):

  profile_suggestions.status='exploratory_only' (99 linhas) e 'applied' (2
  linhas) nunca tinham qualquer ligacao automatica com o desempenho real em
  Shadow do profile a que se referem — promocao exploratory_only -> applied
  e inteiramente manual e cega a evidencia (POST /suggestions/{id}/create-profile,
  backend/app/api/profile_intelligence.py:825, sem checar shadow_trades).

  Esta migration adiciona as duas colunas que o Profile Intelligence Feedback
  Engine (backend/app/services/profile_suggestion_feedback_engine.py) usa
  para anotar cada suggestion com evidencia real, SEM jamais promove-la
  automaticamente (a promocao continua 100% humana via o mesmo endpoint).

    shadow_feedback_status VARCHAR NULL
      'PROMOTE_CANDIDATE' | 'INSUFFICIENT_EVIDENCE' | 'POOR_PERFORMANCE' |
      'NO_PROFILE_LINKED' (quando profile_id e created_profile_id sao ambos
      NULL — caso confirmado para as 99 suggestions exploratory_only criadas
      antes da migration 096, que introduziu profile_id/source_profile_ids;
      o join com profile_rule_combinations tambem nao resolve essas 99
      porque as combinations de origem TAMBEM tem source_profile_ids vazio —
      relacao ausente estrutural, nao um bug do codigo atual).

    shadow_feedback_json JSONB NULL
      Estrutura completa de evaluate_suggestion_feedback(): metrics,
      reasons, thresholds, evaluated_at.

  asyncpg: cada op.execute() contem exatamente um statement.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "108_suggestion_feedback"
down_revision = "107_label_lab_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "profile_suggestions",
        sa.Column("shadow_feedback_status", sa.String, nullable=True),
    )
    op.add_column(
        "profile_suggestions",
        sa.Column("shadow_feedback_json", JSONB, nullable=True),
    )
    op.execute(
        "COMMENT ON COLUMN profile_suggestions.shadow_feedback_status IS "
        "'PROMOTE_CANDIDATE | INSUFFICIENT_EVIDENCE | POOR_PERFORMANCE | NO_PROFILE_LINKED. "
        "Evidence only — never auto-promotes; promotion stays human via create-profile endpoint.'"
    )
    op.execute(
        "COMMENT ON COLUMN profile_suggestions.shadow_feedback_json IS "
        "'Full evaluate_suggestion_feedback() output: metrics/reasons/thresholds/evaluated_at.'"
    )


def downgrade() -> None:
    op.drop_column("profile_suggestions", "shadow_feedback_json")
    op.drop_column("profile_suggestions", "shadow_feedback_status")
