"""Add Strategy Lab columns to shadow_trades (profile attribution).

Revision ID: 077_str_lab_profile
Revises: 076_ml_feature_registry
Create Date: 2026-06-16
"""

from alembic import op
import sqlalchemy as sa

revision = "077_str_lab_profile"
down_revision = "076_ml_feature_registry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add profile attribution columns to shadow_trades
    op.execute(sa.text("""
        ALTER TABLE shadow_trades
        ADD COLUMN IF NOT EXISTS profile_id UUID
    """))
    op.execute(sa.text("""
        ALTER TABLE shadow_trades
        ADD COLUMN IF NOT EXISTS profile_version TIMESTAMPTZ
    """))
    op.execute(sa.text("""
        ALTER TABLE shadow_trades
        ADD COLUMN IF NOT EXISTS profile_name VARCHAR(255)
    """))
    op.execute(sa.text("""
        ALTER TABLE shadow_trades
        ADD COLUMN IF NOT EXISTS strategy_type VARCHAR(64)
    """))
    op.execute(sa.text("""
        ALTER TABLE shadow_trades
        ADD COLUMN IF NOT EXISTS rules_snapshot JSONB
    """))
    op.execute(sa.text("""
        ALTER TABLE shadow_trades
        ADD COLUMN IF NOT EXISTS profile_status_at_entry VARCHAR(32)
    """))
    op.execute(sa.text("""
        ALTER TABLE shadow_trades
        ADD COLUMN IF NOT EXISTS final_priority_score DOUBLE PRECISION
    """))
    op.execute(sa.text("""
        ALTER TABLE shadow_trades
        ADD COLUMN IF NOT EXISTS ml_probability DOUBLE PRECISION
    """))
    op.execute(sa.text("""
        ALTER TABLE shadow_trades
        ADD COLUMN IF NOT EXISTS ml_model_id UUID
    """))

    # FK: soft reference — SET NULL on profile delete to preserve historical data
    op.execute(sa.text("""
        ALTER TABLE shadow_trades
        ADD CONSTRAINT fk_shadow_profile
        FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE SET NULL
    """))

    # Indexes for Strategy Lab queries
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_shadow_trades_profile
        ON shadow_trades(profile_id, profile_version)
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_shadow_trades_profile_source
        ON shadow_trades(source, profile_id, profile_version)
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_shadow_trades_profile_status
        ON shadow_trades(profile_id, status, outcome)
    """))

    # IMMUTABLE helper: epoch-based hour bucket (timezone-independent)
    # DATE_TRUNC on TIMESTAMPTZ is STABLE (not IMMUTABLE) in PostgreSQL,
    # so it can't be used directly in an index expression.
    op.execute(sa.text("""
        CREATE OR REPLACE FUNCTION shadow_lab_hour_bucket(ts timestamptz)
        RETURNS bigint LANGUAGE SQL IMMUTABLE PARALLEL SAFE AS $$
            SELECT EXTRACT(EPOCH FROM ts)::bigint / 3600
        $$
    """))

    # Unique index for Strategy Lab idempotency — only where profile_id is not null
    # Prevents duplicate shadows for same profile+symbol in the same UTC hour bucket
    op.execute(sa.text("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_shadow_lab_profile_symbol_bucket
        ON shadow_trades(profile_id, symbol, source, shadow_lab_hour_bucket(created_at))
        WHERE profile_id IS NOT NULL
    """))


def downgrade() -> None:
    op.execute(sa.text("""
        DROP INDEX IF EXISTS uq_shadow_lab_profile_symbol_bucket
    """))
    op.execute(sa.text("""
        DROP FUNCTION IF EXISTS shadow_lab_hour_bucket(timestamptz)
    """))
    op.execute(sa.text("""
        DROP INDEX IF EXISTS idx_shadow_trades_profile_status
    """))
    op.execute(sa.text("""
        DROP INDEX IF EXISTS idx_shadow_trades_profile_source
    """))
    op.execute(sa.text("""
        DROP INDEX IF EXISTS idx_shadow_trades_profile
    """))
    op.execute(sa.text("""
        ALTER TABLE shadow_trades DROP CONSTRAINT IF EXISTS fk_shadow_profile
    """))
    op.execute(sa.text("ALTER TABLE shadow_trades DROP COLUMN IF EXISTS ml_model_id"))
    op.execute(sa.text("ALTER TABLE shadow_trades DROP COLUMN IF EXISTS ml_probability"))
    op.execute(sa.text("ALTER TABLE shadow_trades DROP COLUMN IF EXISTS final_priority_score"))
    op.execute(sa.text("ALTER TABLE shadow_trades DROP COLUMN IF EXISTS profile_status_at_entry"))
    op.execute(sa.text("ALTER TABLE shadow_trades DROP COLUMN IF EXISTS rules_snapshot"))
    op.execute(sa.text("ALTER TABLE shadow_trades DROP COLUMN IF EXISTS strategy_type"))
    op.execute(sa.text("ALTER TABLE shadow_trades DROP COLUMN IF EXISTS profile_name"))
    op.execute(sa.text("ALTER TABLE shadow_trades DROP COLUMN IF EXISTS profile_version"))
    op.execute(sa.text("ALTER TABLE shadow_trades DROP COLUMN IF EXISTS profile_id"))
