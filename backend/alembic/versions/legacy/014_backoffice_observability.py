"""Create backoffice observability tables

Revision ID: 014_backoffice
Revises: 013_pipeline_staleness_tracking
Create Date: 2026-04-18

Creates:
  - decision_logs: audit trail for every scoring/trade decision
  - asset_traces: full market data + indicator snapshots per asset
  - backoffice_alerts: system alerts with acknowledgement workflow
  - pipeline_metrics: pipeline execution metrics per cycle
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "014_backoffice"
down_revision = "013_pipeline_staleness_tracking"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # decision_logs
    op.create_table(
        'decision_logs',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('symbol', sa.String(50), nullable=False, index=True),
        sa.Column('strategy', sa.String(20), nullable=False),
        sa.Column('score', sa.Float, nullable=True),
        sa.Column('signal', sa.String(50), nullable=True),
        sa.Column('confidence', sa.Float, nullable=True),
        sa.Column('decision', sa.String(20), nullable=True),
        sa.Column('payload_json', JSONB, nullable=True),
        sa.Column('trace_id', sa.String(64), nullable=True, index=True),
        sa.Column('user_id', UUID(as_uuid=True), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # asset_traces
    op.create_table(
        'asset_traces',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('symbol', sa.String(50), nullable=False, index=True),
        sa.Column('market_data_json', JSONB, nullable=True),
        sa.Column('indicators_json', JSONB, nullable=True),
        sa.Column('conditions_json', JSONB, nullable=True),
        sa.Column('decision', sa.String(20), nullable=True),
        sa.Column('score', sa.Float, nullable=True),
        sa.Column('strategy', sa.String(20), nullable=True),
        sa.Column('trace_id', sa.String(64), nullable=True, index=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # backoffice_alerts
    op.create_table(
        'backoffice_alerts',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('alert_type', sa.String(20), nullable=False),
        sa.Column('category', sa.String(50), nullable=True),
        sa.Column('message', sa.Text, nullable=False),
        sa.Column('details_json', JSONB, nullable=True),
        sa.Column('status', sa.String(20), server_default='active'),
        sa.Column('acknowledged_by', UUID(as_uuid=True), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('acknowledged_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # pipeline_metrics
    op.create_table(
        'pipeline_metrics',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('discovered', sa.Integer, server_default='0'),
        sa.Column('filtered', sa.Integer, server_default='0'),
        sa.Column('scored', sa.Integer, server_default='0'),
        sa.Column('signals_count', sa.Integer, server_default='0'),
        sa.Column('executed', sa.Integer, server_default='0'),
        sa.Column('approved', sa.Integer, server_default='0'),
        sa.Column('rejected', sa.Integer, server_default='0'),
        sa.Column('latency_ms', sa.Float, nullable=True),
        sa.Column('error_count', sa.Integer, server_default='0'),
        sa.Column('strategy', sa.String(20), nullable=True),
        sa.Column('trace_id', sa.String(64), nullable=True, index=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table('pipeline_metrics')
    op.drop_table('backoffice_alerts')
    op.drop_table('asset_traces')
    op.drop_table('decision_logs')
