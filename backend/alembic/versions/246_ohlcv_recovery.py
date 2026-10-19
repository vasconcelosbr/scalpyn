"""Reviewed historical recovery manifests and atomic attempt receipts.

Revision ID: 246_ohlcv_recovery
Revises: 245_ohlcv_comparison_cursor
"""
from alembic import op
import sqlalchemy as sa

revision = "246_ohlcv_recovery"
down_revision = "245_ohlcv_comparison_cursor"
branch_labels = None
depends_on = None


def upgrade():
    # No production owner, config, manifest, task, or hot-table DDL is seeded.
    for statement in (
        """CREATE TABLE ohlcv_recovery_owner (
            singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
            user_id uuid NOT NULL UNIQUE REFERENCES users(id),
            authorization_reason text NOT NULL,
            next_request_at timestamptz NOT NULL DEFAULT 'epoch')""",
        """CREATE TABLE ohlcv_recovery_jobs (
            id uuid PRIMARY KEY, manifest_hash text NOT NULL UNIQUE,
            manifest jsonb NOT NULL, submitted_by uuid NOT NULL REFERENCES users(id),
            submitted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            next_time timestamptz NOT NULL,
            status text NOT NULL CHECK(status IN ('READY','PARTIAL','COMPLETE','EXHAUSTED')),
            gap_attempts integer NOT NULL DEFAULT 0,
            next_attempt_at timestamptz NOT NULL DEFAULT 'epoch')""",
        """CREATE TABLE ohlcv_recovery_attempts (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            job_id uuid NOT NULL REFERENCES ohlcv_recovery_jobs(id),
            observed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            request_start timestamptz NOT NULL, request_end timestamptz NOT NULL,
            config_json jsonb NOT NULL, receipt jsonb NOT NULL)""",
    ):
        op.execute(sa.text(statement))


def downgrade():
    # Preserve provenance on operational rollback; disabling config stops runs.
    pass
