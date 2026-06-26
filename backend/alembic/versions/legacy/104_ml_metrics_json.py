"""Add metrics_json and target_window_seconds to ml_models.

Revision ID: 104_ml_metrics_json
Revises: 103_shadow_watchlist_lineage
Create Date: 2026-06-23

Contexto:
  MLChallengerService salva métricas de validação (val set) nas colunas
  escalares de ml_models (precision_score, recall_score, etc.) mas as
  métricas do test set ficam apenas em `notes` TEXT e `hyperparams` JSONB —
  sem estrutura consultável e sem separação explícita entre val/test.

  Colunas adicionadas:
    metrics_json JSONB NULL
      Estrutura: {
        "label_version": "is_tp_4h_v1",
        "target_window_seconds": 14400,
        "validation": {"precision": ..., "recall": ..., "roc_auc": ...,
                       "fpr": ..., "f1": ..., "samples": ...},
        "test":       {"precision": ..., "recall": ..., "roc_auc": ...,
                       "fpr": ..., "f1": ..., "samples": ...}
      }
      NULL em modelos antigos (pre-104) — frontend trata como ausente.

    target_window_seconds INTEGER NULL
      Janela de tempo em segundos usada para o label de treino.
      1800 = is_win_fast_v1 (30 min), 14400 = is_tp_4h_v1 (4 h).
      NULL em modelos antigos (backward compat).

  asyncpg: cada op.execute() contém exatamente um statement.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "104_ml_metrics_json"
down_revision = "103_shadow_watchlist_lineage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ml_models",
        sa.Column("metrics_json", JSONB, nullable=True),
    )
    op.add_column(
        "ml_models",
        sa.Column("target_window_seconds", sa.Integer, nullable=True),
    )
    op.execute(
        "COMMENT ON COLUMN ml_models.metrics_json IS "
        "'Structured val+test metrics with label metadata. NULL for pre-104 models.'"
    )
    op.execute(
        "COMMENT ON COLUMN ml_models.target_window_seconds IS "
        "'Win-time threshold used for label (seconds). 1800=is_win_fast_v1, 14400=is_tp_4h_v1.'"
    )


def downgrade() -> None:
    op.drop_column("ml_models", "target_window_seconds")
    op.drop_column("ml_models", "metrics_json")
