"""Add default_model column to ai_provider_keys (DeepSeek: deepseek-v4-flash / deepseek-v4-pro).

Revision ID: 183_ai_key_default_model
Revises: 182_profile_daily_budget
Create Date: 2026-08-18

Providers with a single fixed model (Anthropic, OpenAI, Gemini) leave this
column NULL. Providers offering multiple selectable models (DeepSeek) store
the user's chosen model here so callers know which model to use without a
second round-trip.

NOTE: alembic_version.version_num is VARCHAR(32) — revision ids must stay
at or under 32 chars or the final UPDATE alembic_version fails with
StringDataRightTruncationError (bit this once already).
"""

from alembic import op
import sqlalchemy as sa

revision = "183_ai_key_default_model"
down_revision = "182_profile_daily_budget"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ai_provider_keys",
        sa.Column("default_model", sa.String(60), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ai_provider_keys", "default_model")
