"""Allow the governed L3 continuation worker to record its closure path.

Revision ID: 220_shadow_l3_closure_path
Revises: 219_shadow_l3_continuation

The continuation service introduced in revision 219 finalizes a Shadow with
``closure_path='l3_continuation'``. Revision 212's allowlist predates that
service, so PostgreSQL rejects the otherwise valid terminal update. Replace
the constraint only when the live definition does not already contain the new
path. That makes a pre-applied production repair a no-op during cold start.
"""

from alembic import op
import sqlalchemy as sa


revision = "220_shadow_l3_closure_path"
down_revision = "219_shadow_l3_continuation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                  FROM pg_constraint
                 WHERE conname = 'ck_shadow_trades_closure_path'
                   AND conrelid = 'shadow_trades'::regclass
                   AND pg_get_constraintdef(oid) LIKE '%l3_continuation%'
            ) THEN
                ALTER TABLE shadow_trades
                    DROP CONSTRAINT IF EXISTS ck_shadow_trades_closure_path;
                ALTER TABLE shadow_trades
                    ADD CONSTRAINT ck_shadow_trades_closure_path
                    CHECK (
                        closure_path IS NULL OR closure_path IN (
                            'fast_scan',
                            'regular_batch',
                            'canonical_walk',
                            'l3_continuation'
                        )
                    );
            END IF;
        END
        $$;
    """))


def downgrade() -> None:
    # Existing rows may legitimately carry the new value. Removing it would
    # make historical evidence invalid, so this repair is intentionally
    # forward-only; operational rollback disables the producer instead.
    raise RuntimeError(
        "Forward-only evidence migration; disable L3 continuation instead"
    )
