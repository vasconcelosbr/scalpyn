"""Add ai_provider_keys table for storing encrypted AI API keys per user

Revision ID: 007_ai_provider_keys
Revises: 006_refactor_filters_blocks
Create Date: 2026-03-22

Stores AI provider API keys (Anthropic, OpenAI, Gemini) encrypted with
AES-256 (Fernet). The plain-text key is NEVER stored or returned by the API.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "007_ai_provider_keys"
down_revision = "006_refactor_filters_blocks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_provider_keys",
        sa.Column("id",                   UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id",              UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider",             sa.String(50),  nullable=False),
        sa.Column("api_key_encrypted",    sa.LargeBinary, nullable=False),
        sa.Column("api_secret_encrypted", sa.LargeBinary, nullable=True),
        sa.Column("key_hint",             sa.String(20),  nullable=True),
        sa.Column("label",                sa.String(100), nullable=True),
        sa.Column("is_active",            sa.Boolean,     server_default="true",  nullable=False),
        sa.Column("is_validated",         sa.Boolean,     server_default="false", nullable=False),
        sa.Column("last_used_at",         sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_tested_at",       sa.DateTime(timezone=True), nullable=True),
        sa.Column("test_status",          sa.String(20),  nullable=True),
        sa.Column("test_error",           sa.Text,        nullable=True),
        sa.Column("monthly_token_limit",  sa.BigInteger,  nullable=True),
        sa.Column("tokens_used_month",    sa.BigInteger,  server_default="0", nullable=False),
        sa.Column("created_at",  sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at",  sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_ai_keys_user_provider", "ai_provider_keys", ["user_id", "provider"])
    # Unique: one active key per (user, provider)
    op.execute(sa.text("""
        CREATE UNIQUE INDEX uq_ai_keys_user_provider_active
        ON ai_provider_keys (user_id, provider)
        WHERE is_active = true;
    """))


def downgrade() -> None:
    op.drop_index("uq_ai_keys_user_provider_active", table_name="ai_provider_keys")
    op.drop_index("ix_ai_keys_user_provider", table_name="ai_provider_keys")
    op.drop_table("ai_provider_keys")
