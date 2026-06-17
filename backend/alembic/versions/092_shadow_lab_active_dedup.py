"""Prevent duplicate active L3_LAB shadows for the same profile+symbol.

The existing uq_shadow_lab_profile_symbol_bucket only deduplicates within
the same hour bucket. When a symbol keeps passing the filter across hour
boundaries, each scan creates a new RUNNING shadow for the same profile+symbol.

This index adds a guard at the status level: at most one RUNNING or PENDING
shadow per (profile_id, symbol, source). When the monitor closes a trade
(TP_HIT / SL_HIT / TIMEOUT) the row leaves the partial index and the next
scan can open a fresh shadow.

No code change needed — the bare ON CONFLICT DO NOTHING in
_INSERT_STRATEGY_LAB_SQL already catches all constraint violations.

Revision ID: 092_shadow_lab_active_dedup
Revises: 091_profiles_suggestion_unique
Create Date: 2026-06-17
"""

from alembic import op
import sqlalchemy as sa

revision = "092_shadow_lab_active_dedup"
down_revision = "091_profiles_suggestion_unique"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_shadow_lab_active_profile_symbol
        ON shadow_trades(profile_id, symbol, source)
        WHERE profile_id IS NOT NULL AND status IN ('RUNNING', 'PENDING')
    """))


def downgrade() -> None:
    op.execute(sa.text("""
        DROP INDEX IF EXISTS uq_shadow_lab_active_profile_symbol
    """))
