"""Add suggestion_hash to profile_suggestions for deduplication.

Revision ID: 102_suggestion_hash
Revises: 102_shadow_trades_orchestrator_payload
Create Date: 2026-06-22

Contexto:
  - suggestion_hash: SHA-256 (truncado em 64 chars) do conteúdo da sugestão (signals + block_rules
    + scoring + source_type). Permite detectar sugestões duplicadas dentro do mesmo user_id.
  - Constraint parcial UNIQUE(user_id, suggestion_hash) WHERE suggestion_hash IS NOT NULL
    evita inserção de sugestões idênticas sem afetar linhas legadas (hash NULL).
"""

from alembic import op
import sqlalchemy as sa

revision = "102_suggestion_hash"
down_revision = "102_shadow_trades_orchestrator_payload"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "profile_suggestions",
        sa.Column("suggestion_hash", sa.String(64), nullable=True),
    )
    op.create_index(
        "uq_suggestion_hash_per_user",
        "profile_suggestions",
        ["user_id", "suggestion_hash"],
        unique=True,
        postgresql_where=sa.text("suggestion_hash IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_suggestion_hash_per_user", table_name="profile_suggestions")
    op.drop_column("profile_suggestions", "suggestion_hash")
