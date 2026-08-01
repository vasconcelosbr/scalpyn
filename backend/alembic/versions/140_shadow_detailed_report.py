"""Shadow detailed report snapshots and AI analysis jobs.

Revision ID: 140_shadow_detailed_report
Revises: 139_shadow_monitor_fairness
Create Date: 2026-08-01
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "140_shadow_detailed_report"
down_revision: Union[str, Sequence[str], None] = "139_shadow_monitor_fairness"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))

    op.create_table(
        "shadow_trade_report_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("filters", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("filters_hash", sa.String(length=64), nullable=False),
        sa.Column("trade_ids_hash", sa.String(length=64), nullable=False),
        sa.Column("timezone", sa.String(length=80), nullable=False),
        sa.Column("total_trades", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("completeness", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_shadow_report_runs_user_created", "shadow_trade_report_runs", ["user_id", "created_at"])

    op.create_table(
        "shadow_trade_report_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("report_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("shadow_trade_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["report_run_id"], ["shadow_trade_report_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["shadow_trade_id"], ["shadow_trades.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("report_run_id", "position", name="uq_shadow_report_item_position"),
        sa.UniqueConstraint("report_run_id", "shadow_trade_id", name="uq_shadow_report_item_trade"),
    )
    op.create_index("idx_shadow_report_items_run_position", "shadow_trade_report_items", ["report_run_id", "position"])

    op.create_table(
        "shadow_trade_analysis_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scope", sa.String(length=30), nullable=False),
        sa.Column("shadow_trade_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("report_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("model", sa.String(length=160), nullable=False),
        sa.Column("prompt_version", sa.String(length=40), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("result_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("raw_response", sa.Text(), nullable=True),
        sa.Column("usage", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["shadow_trade_id"], ["shadow_trades.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["report_run_id"], ["shadow_trade_report_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_shadow_analysis_user_idempotency"),
    )
    op.create_index("idx_shadow_analysis_jobs_status", "shadow_trade_analysis_jobs", ["status", "created_at"])
    op.create_index("idx_shadow_analysis_jobs_user_created", "shadow_trade_analysis_jobs", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("idx_shadow_analysis_jobs_user_created", table_name="shadow_trade_analysis_jobs")
    op.drop_index("idx_shadow_analysis_jobs_status", table_name="shadow_trade_analysis_jobs")
    op.drop_table("shadow_trade_analysis_jobs")
    op.drop_index("idx_shadow_report_items_run_position", table_name="shadow_trade_report_items")
    op.drop_table("shadow_trade_report_items")
    op.drop_index("idx_shadow_report_runs_user_created", table_name="shadow_trade_report_runs")
    op.drop_table("shadow_trade_report_runs")
