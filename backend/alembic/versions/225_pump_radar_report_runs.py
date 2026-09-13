"""Pump Radar report runs -- immutable selection materialization for AI analysis.

Revision ID: 225_pump_radar_report_runs
Revises: 224_pump_radar_v1
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "225_pump_radar_report_runs"
down_revision = "224_pump_radar_v1"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "pump_radar_report_runs",
        sa.Column("id", UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("run_id", UUID, nullable=False),
        sa.Column("selection_mode", sa.String(16), nullable=False),
        sa.Column("filters", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("selection_hash", sa.String(64), nullable=False),
        sa.Column("total_events", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="READY"),
        sa.Column("completeness", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["pump_radar_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("selection_mode IN ('ASSET','ALL')", name="ck_pump_radar_report_selection_mode"),
    )
    op.create_index("idx_pump_radar_report_runs_user_created", "pump_radar_report_runs", ["user_id", "created_at"])

    op.create_table(
        "pump_radar_report_items",
        sa.Column("id", UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("report_run_id", UUID, nullable=False),
        sa.Column("event_id", UUID, nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["report_run_id"], ["pump_radar_report_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["event_id"], ["pump_radar_events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("report_run_id", "position", name="uq_pump_radar_report_item_position"),
        sa.UniqueConstraint("report_run_id", "event_id", name="uq_pump_radar_report_item_event"),
    )
    op.create_index("idx_pump_radar_report_items_run_position", "pump_radar_report_items", ["report_run_id", "position"])


def downgrade() -> None:
    op.drop_index("idx_pump_radar_report_items_run_position", table_name="pump_radar_report_items")
    op.drop_table("pump_radar_report_items")
    op.drop_index("idx_pump_radar_report_runs_user_created", table_name="pump_radar_report_runs")
    op.drop_table("pump_radar_report_runs")
