"""Bounded comparison state, separate from historical full snapshots.

Revision ID: 245_ohlcv_comparison_cursor
Revises: 244_storage_retention
"""
from alembic import op
import sqlalchemy as sa

revision = "245_ohlcv_comparison_cursor"
down_revision = "244_storage_retention"
branch_labels = None
depends_on = None


def upgrade():
    # No owner/config is assigned by deployment. Explicit operator provisioning
    # is required; collection does not depend on comparison being enabled.
    for statement in (
        """CREATE TABLE ohlcv_comparison_owner (
            singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
            user_id uuid NOT NULL UNIQUE REFERENCES users(id),
            authorization_reason text NOT NULL)""",
        """CREATE TABLE ohlcv_comparison_cursors (
            timeframe text NOT NULL, capture_contract_version text NOT NULL,
            next_time timestamptz NOT NULL, updated_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (timeframe,capture_contract_version))""",
        """CREATE TABLE ohlcv_comparison_windows (
            timeframe text NOT NULL, capture_contract_version text NOT NULL,
            window_start timestamptz NOT NULL, window_end timestamptz NOT NULL,
            observed_at timestamptz NOT NULL, config_json jsonb NOT NULL,
            summary jsonb NOT NULL,
            CHECK(window_end > window_start),
            PRIMARY KEY(timeframe,capture_contract_version,window_start))""",
    ):
        op.execute(sa.text(statement))


def downgrade():
    # Operational rollback must preserve the cursor and comparison evidence.
    pass
