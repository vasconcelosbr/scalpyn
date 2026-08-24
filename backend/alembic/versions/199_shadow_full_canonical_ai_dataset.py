"""Persist complete Shadow Portfolio AI datasets and provider shards.

Revision ID: 199_shadow_full_canonical
Revises: 198_l3_gate_v2_operational
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "199_shadow_full_canonical"
down_revision = "198_l3_gate_v2_operational"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_dataset_snapshot_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("dataset_snapshot_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("ai_dataset_snapshots.id", ondelete="CASCADE"), nullable=False),
        sa.Column("report_run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("shadow_trade_report_runs.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("shadow_trade_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("shadow_trades.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("report_position", sa.Integer(), nullable=False),
        sa.Column("canonical_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("item_hash", sa.String(64), nullable=False),
        sa.Column("payload_bytes", sa.Integer(), nullable=False),
        sa.Column("estimated_tokens", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("report_position >= 0", name="ck_ai_dataset_item_position_nonnegative"),
        sa.CheckConstraint("payload_bytes > 0", name="ck_ai_dataset_item_payload_bytes_positive"),
        sa.CheckConstraint("estimated_tokens > 0", name="ck_ai_dataset_item_tokens_positive"),
        sa.UniqueConstraint("dataset_snapshot_id", "report_position", name="uq_ai_dataset_item_position"),
        sa.UniqueConstraint("dataset_snapshot_id", "shadow_trade_id", name="uq_ai_dataset_item_trade"),
    )
    op.create_index("ix_ai_dataset_item_snapshot_position", "ai_dataset_snapshot_items", ["dataset_snapshot_id", "report_position"])
    op.create_index("ix_ai_dataset_item_tenant_report", "ai_dataset_snapshot_items", ["tenant_id", "report_run_id"])

    op.create_table(
        "ai_analysis_shards",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ai_request_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("ai_requests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("dataset_snapshot_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("ai_dataset_snapshots.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("shard_index", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="PLANNED"),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("item_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("item_hashes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("payload_bytes", sa.Integer(), nullable=False),
        sa.Column("estimated_input_tokens", sa.Integer(), nullable=False),
        sa.Column("reserved_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_input", sa.Integer()),
        sa.Column("tokens_output", sa.Integer()),
        sa.Column("provider_request_ref", sa.String(255)),
        sa.Column("provider_response_ref", sa.String(255)),
        sa.Column("result_json", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("error_code", sa.String(80)),
        sa.Column("error_safe_message", sa.Text()),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("shard_index >= 0", name="ck_ai_analysis_shard_index_nonnegative"),
        sa.CheckConstraint("item_count > 0", name="ck_ai_analysis_shard_item_count_positive"),
        sa.CheckConstraint("payload_bytes > 0", name="ck_ai_analysis_shard_payload_bytes_positive"),
        sa.CheckConstraint("estimated_input_tokens > 0", name="ck_ai_analysis_shard_tokens_positive"),
        sa.CheckConstraint("status IN ('PLANNED','RUNNING','COMPLETED','FAILED','RECONCILED')", name="ck_ai_analysis_shard_status"),
        sa.UniqueConstraint("ai_request_id", "shard_index", name="uq_ai_analysis_shard_request_index"),
    )
    op.create_index("ix_ai_analysis_shard_request_index", "ai_analysis_shards", ["ai_request_id", "shard_index"])
    op.create_index("ix_ai_analysis_shard_tenant_status", "ai_analysis_shards", ["tenant_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_ai_analysis_shard_tenant_status", table_name="ai_analysis_shards", if_exists=True)
    op.drop_index("ix_ai_analysis_shard_request_index", table_name="ai_analysis_shards", if_exists=True)
    op.drop_table("ai_analysis_shards", if_exists=True)
    op.drop_index("ix_ai_dataset_item_tenant_report", table_name="ai_dataset_snapshot_items", if_exists=True)
    op.drop_index("ix_ai_dataset_item_snapshot_position", table_name="ai_dataset_snapshot_items", if_exists=True)
    op.drop_table("ai_dataset_snapshot_items", if_exists=True)
