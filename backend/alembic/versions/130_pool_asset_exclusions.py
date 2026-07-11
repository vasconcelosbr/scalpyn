"""Persist operator removals so automatic pool discovery cannot restore them.

Revision ID: 130_pool_asset_exclusions
Revises: 129_crypto_ev_score
Create Date: 2026-07-11
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "130_pool_asset_exclusions"
down_revision = "129_crypto_ev_score"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
    op.create_table(
        "pool_asset_exclusions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("pool_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("pools.id", ondelete="CASCADE"), nullable=False),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False, server_default="manual_removal"),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("pool_id", "symbol", name="uq_pool_asset_exclusions_pool_symbol"),
    )
    op.create_index(
        "ix_pool_asset_exclusions_pool_id",
        "pool_asset_exclusions",
        ["pool_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_pool_asset_exclusions_pool_id", table_name="pool_asset_exclusions")
    op.drop_table("pool_asset_exclusions")
