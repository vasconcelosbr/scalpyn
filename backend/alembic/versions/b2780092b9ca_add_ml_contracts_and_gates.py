"""add_ml_contracts_and_gates

Revision ID: b2780092b9ca
Revises: 122_backfill_ranking_dec_id
Create Date: 2026-06-30 02:13:28.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b2780092b9ca'
down_revision: Union[str, None] = '122_backfill_ranking_dec_id'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add contract columns to ml_models
    op.add_column('ml_models', sa.Column('target_window_seconds', sa.Integer(), nullable=True))
    op.add_column('ml_models', sa.Column('label_contract_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column('ml_models', sa.Column('dataset_contract_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column('ml_models', sa.Column('feature_contract_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column('ml_models', sa.Column('tp_pct', sa.Numeric(), nullable=True))
    op.add_column('ml_models', sa.Column('sl_pct', sa.Numeric(), nullable=True))
    op.add_column('ml_models', sa.Column('fee_roundtrip_pct', sa.Numeric(), nullable=True))
    op.add_column('ml_models', sa.Column('label_net_of_fees', sa.Boolean(), nullable=True))
    op.add_column('ml_models', sa.Column('barrier_mode', sa.String(length=50), nullable=True))
    op.add_column('ml_models', sa.Column('intrabar_policy', sa.String(length=50), nullable=True))
    op.add_column('ml_models', sa.Column('ohlcv_timeframe', sa.String(length=10), nullable=True))
    op.add_column('ml_models', sa.Column('maturity_policy', sa.String(length=50), nullable=True))
    op.add_column('ml_models', sa.Column('macro_features_enabled', sa.Boolean(), nullable=True, server_default='false'))
    op.add_column('ml_models', sa.Column('test_metrics_json', postgresql.JSONB(astext_type=sa.Text()), nullable=True))

    # Create ml_readiness_gate_runs table
    op.create_table('ml_readiness_gate_runs',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text('gen_random_uuid()')),
        sa.Column('run_id', sa.String(length=255), nullable=False),
        sa.Column('model_lane', sa.String(length=50), nullable=True),
        sa.Column('readiness_status', sa.String(length=50), nullable=False),
        sa.Column('block_reason', sa.Text(), nullable=True),
        sa.Column('positive_rate_train', sa.Numeric(), nullable=True),
        sa.Column('positive_rate_val', sa.Numeric(), nullable=True),
        sa.Column('positive_rate_test', sa.Numeric(), nullable=True),
        sa.Column('dead_feature_ratio', sa.Numeric(), nullable=True),
        sa.Column('psi_max', sa.Numeric(), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ml_readiness_gate_runs_created_at'), 'ml_readiness_gate_runs', ['created_at'], unique=False)

    # Create ml_dataset_readiness_reports table
    op.create_table('ml_dataset_readiness_reports',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text('gen_random_uuid()')),
        sa.Column('dataset_id', sa.String(length=255), nullable=False),
        sa.Column('total_features', sa.Integer(), nullable=False),
        sa.Column('dead_features', sa.Integer(), nullable=False),
        sa.Column('dead_feature_ratio', sa.Numeric(), nullable=False),
        sa.Column('min_coverage', sa.Numeric(), nullable=False),
        sa.Column('readiness_status', sa.String(length=50), nullable=False),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ml_dataset_readiness_reports_dataset_id'), 'ml_dataset_readiness_reports', ['dataset_id'], unique=False)

    # Create ml_feature_observations table
    op.create_table('ml_feature_observations',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text('gen_random_uuid()')),
        sa.Column('dataset_id', sa.String(length=255), nullable=False),
        sa.Column('feature_name', sa.String(length=255), nullable=False),
        sa.Column('value', sa.Numeric(), nullable=True),
        sa.Column('source_timestamp', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('fetched_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('source_group', sa.String(length=50), nullable=True),
        sa.Column('stale', sa.Boolean(), nullable=True),
        sa.Column('formula_version', sa.String(length=50), nullable=True),
        sa.Column('coverage_status', sa.String(length=50), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ml_feature_observations_dataset_id'), 'ml_feature_observations', ['dataset_id'], unique=False)

    # Create ml_feature_drift_reports
    op.create_table('ml_feature_drift_reports',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text('gen_random_uuid()')),
        sa.Column('dataset_id', sa.String(length=255), nullable=False),
        sa.Column('feature_name', sa.String(length=255), nullable=False),
        sa.Column('psi_train_test', sa.Numeric(), nullable=True),
        sa.Column('importance', sa.Numeric(), nullable=True),
        sa.Column('source_timestamp_coverage', sa.Numeric(), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    op.drop_table('ml_feature_drift_reports')
    op.drop_index(op.f('ix_ml_feature_observations_dataset_id'), table_name='ml_feature_observations')
    op.drop_table('ml_feature_observations')
    op.drop_index(op.f('ix_ml_dataset_readiness_reports_dataset_id'), table_name='ml_dataset_readiness_reports')
    op.drop_table('ml_dataset_readiness_reports')
    op.drop_index(op.f('ix_ml_readiness_gate_runs_created_at'), table_name='ml_readiness_gate_runs')
    op.drop_table('ml_readiness_gate_runs')

    op.drop_column('ml_models', 'test_metrics_json')
    op.drop_column('ml_models', 'macro_features_enabled')
    op.drop_column('ml_models', 'maturity_policy')
    op.drop_column('ml_models', 'ohlcv_timeframe')
    op.drop_column('ml_models', 'intrabar_policy')
    op.drop_column('ml_models', 'barrier_mode')
    op.drop_column('ml_models', 'label_net_of_fees')
    op.drop_column('ml_models', 'fee_roundtrip_pct')
    op.drop_column('ml_models', 'sl_pct')
    op.drop_column('ml_models', 'tp_pct')
    op.drop_column('ml_models', 'feature_contract_id')
    op.drop_column('ml_models', 'dataset_contract_id')
    op.drop_column('ml_models', 'label_contract_id')
    op.drop_column('ml_models', 'target_window_seconds')
