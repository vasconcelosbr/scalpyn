"""Seven-day radar receipt audit.

Revision ID: 228_radar_feed_audit
Revises: 227_radar_last_seen_at
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "228_radar_feed_audit"
down_revision = "227_radar_last_seen_at"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
    op.create_table(
        "radar_feed_receipts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("pool_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("pools.id", ondelete="CASCADE"), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("source_count", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(80)),
    )
    op.create_index("ix_radar_feed_receipts_expires_at", "radar_feed_receipts", ["expires_at"])
    op.create_index("ix_radar_receipt_pool_time", "radar_feed_receipts", ["pool_id", "received_at"])
    op.create_table(
        "radar_feed_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("receipt_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("radar_feed_receipts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("symbol", sa.String(120), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True)),
        sa.Column("pool_result", sa.String(32), nullable=False),
    )
    op.create_index("ix_radar_feed_items_receipt_id", "radar_feed_items", ["receipt_id"])


def downgrade():
    op.drop_table("radar_feed_items")
    op.drop_table("radar_feed_receipts")
