"""L1 Spectrum capture infrastructure.

Revision ID: 073_l1_spectrum_capture
Revises: 072_trade_sim_schema_parity
Create Date: 2026-06-10

Changes (all idempotent):
1. Drop ux_shadow_running_user_symbol (user_id, symbol) WHERE status='RUNNING'
   and replace with ux_shadow_running_user_source (user_id, symbol, source)
   so each stream has its own per-symbol running slot:
     L3 shadow running → does not block L1_SPECTRUM shadow for same symbol
     L1_SPECTRUM shadow running → does not block L3 shadow for same symbol

2. Create shadow_capture_skips table for skip audit log (deterministic
   sampling + reentry policy + rate limit).  Not mirrored to
   trade_simulations (not a simulation record — structural metadata).

Downgrade:
- Drop shadow_capture_skips
- Drop ux_shadow_running_user_source
- Recreate ux_shadow_running_user_symbol (original)
"""

from alembic import op
import sqlalchemy as sa

revision = "073_l1_spectrum_capture"
down_revision = "072_trade_sim_schema_parity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Swap the partial unique index — one statement per op.execute()
    #    to avoid asyncpg multi-statement bug.
    op.execute(sa.text("""
        DO $$
        BEGIN
            -- Drop old per-(user_id, symbol) constraint
            DROP INDEX IF EXISTS ux_shadow_running_user_symbol;

            -- Create new per-(user_id, symbol, source) constraint
            IF NOT EXISTS (
                SELECT 1 FROM pg_indexes
                WHERE indexname = 'ux_shadow_running_user_source'
            ) THEN
                CREATE UNIQUE INDEX ux_shadow_running_user_source
                    ON shadow_trades (user_id, symbol, source)
                    WHERE status = 'RUNNING';
            END IF;
        END $$;
    """))

    # 2. Create shadow_capture_skips table
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS shadow_capture_skips (
            id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id         UUID        NOT NULL,
            symbol          VARCHAR     NOT NULL,
            promotion_at    TIMESTAMPTZ NOT NULL,
            skip_reason     VARCHAR     NOT NULL,
            source_path     VARCHAR     NOT NULL,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))

    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_shadow_capture_skips_user_id
            ON shadow_capture_skips (user_id)
    """))

    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_shadow_capture_skips_created_at
            ON shadow_capture_skips (created_at DESC)
    """))

    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_shadow_capture_skips_symbol
            ON shadow_capture_skips (symbol)
    """))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS shadow_capture_skips"))
    op.execute(sa.text("DROP INDEX IF EXISTS ux_shadow_running_user_source"))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_indexes
                WHERE indexname = 'ux_shadow_running_user_symbol'
            ) THEN
                CREATE UNIQUE INDEX ux_shadow_running_user_symbol
                    ON shadow_trades (user_id, symbol)
                    WHERE status = 'RUNNING';
            END IF;
        END $$;
    """))
