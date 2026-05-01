"""indicator_snapshots table

Revision ID: 027_indicator_snapshots
Revises: 026_decisions_log_direction_event_type
Create Date: 2026-05-01 18:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '027_indicator_snapshots'
down_revision = '026_decisions_log_direction_event_type'
branch_labels = None
depends_on = None


def upgrade():
    # Create indicator_snapshots table
    op.create_table(
        'indicator_snapshots',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('symbol', sa.String(length=20), nullable=False),
        sa.Column('timestamp', sa.DateTime(timezone=True), nullable=False),
        sa.Column('indicators_json', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('global_confidence', sa.Numeric(precision=5, scale=4), nullable=False),
        sa.Column('valid_indicators', sa.Integer(), nullable=False),
        sa.Column('total_indicators', sa.Integer(), nullable=False),
        sa.Column('validation_passed', sa.Boolean(), nullable=False),
        sa.Column('validation_errors', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('score', sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column('score_confidence', sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column('can_trade', sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )

    # Create indexes
    op.create_index(
        'idx_indicator_snapshots_symbol_timestamp',
        'indicator_snapshots',
        ['symbol', 'timestamp'],
        unique=False
    )
    op.create_index(
        'idx_indicator_snapshots_can_trade',
        'indicator_snapshots',
        ['can_trade', 'timestamp'],
        unique=False
    )
    op.create_index(
        'idx_indicator_snapshots_validation',
        'indicator_snapshots',
        ['validation_passed', 'timestamp'],
        unique=False
    )
    op.create_index(
        'ix_indicator_snapshots_symbol',
        'indicator_snapshots',
        ['symbol'],
        unique=False
    )
    op.create_index(
        'ix_indicator_snapshots_timestamp',
        'indicator_snapshots',
        ['timestamp'],
        unique=False
    )


def downgrade():
    op.drop_index('ix_indicator_snapshots_timestamp', table_name='indicator_snapshots')
    op.drop_index('ix_indicator_snapshots_symbol', table_name='indicator_snapshots')
    op.drop_index('idx_indicator_snapshots_validation', table_name='indicator_snapshots')
    op.drop_index('idx_indicator_snapshots_can_trade', table_name='indicator_snapshots')
    op.drop_index('idx_indicator_snapshots_symbol_timestamp', table_name='indicator_snapshots')
    op.drop_table('indicator_snapshots')
