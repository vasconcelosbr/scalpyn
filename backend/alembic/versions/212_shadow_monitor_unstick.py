"""Bloco A.1 (C1) + A.6: widen barrier_touched, add ambiguity/closure audit
columns for the Shadow monitor backlog fix.

C1: shadow_trades.barrier_touched and trade_simulations.barrier_touched are
VARCHAR(20); the literal 'BARRIER_PATH_UNRESOLVED' is 23 characters, so
every attempt to persist it raises StringDataRightTruncationError, aborting
the row's whole savepoint (Q4 of the 2026-09-03 diagnosis). Widened to
VARCHAR(32) -- matches the width already used by sibling reason-code
columns on the same table (entry_risk_capture_status, lineage_status,
profile_status_at_entry). History is preserved; no existing value changes.

C2 support: entry_boundary_ambiguous_at records (once) the timestamp of an
entry-boundary-partial candle whose touch order could not be resolved --
audit trail for the fixed cursor-advance behaviour (barrier contract
shadow_closed_ohlcv_first_touch_v2), which no longer freezes the Shadow.

A.6: closure_path records which code path produced the terminal outcome
(fast_scan / regular_batch / canonical_walk), so latency can be broken down
by detection path going forward.

Revision ID: 212_shadow_monitor_unstick
Revises: 211_shadow_trailing_replay
"""

from alembic import op
import sqlalchemy as sa


revision = "212_shadow_monitor_unstick"
down_revision = "211_shadow_trailing_replay"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text(
        "ALTER TABLE shadow_trades ALTER COLUMN barrier_touched TYPE VARCHAR(32)"
    ))
    op.execute(sa.text(
        "ALTER TABLE trade_simulations ALTER COLUMN barrier_touched TYPE VARCHAR(32)"
    ))
    op.execute(sa.text(
        "ALTER TABLE shadow_trades ADD COLUMN IF NOT EXISTS "
        "entry_boundary_ambiguous_at TIMESTAMPTZ NULL"
    ))
    op.execute(sa.text(
        "ALTER TABLE shadow_trades ADD COLUMN IF NOT EXISTS "
        "closure_path VARCHAR(20) NULL"
    ))
    op.execute(sa.text(
        "ALTER TABLE shadow_trades ADD CONSTRAINT ck_shadow_trades_closure_path "
        "CHECK (closure_path IS NULL OR closure_path IN "
        "('fast_scan', 'regular_batch', 'canonical_walk'))"
    ))


def downgrade() -> None:
    op.execute(sa.text(
        "ALTER TABLE shadow_trades DROP CONSTRAINT IF EXISTS ck_shadow_trades_closure_path"
    ))
    op.execute(sa.text(
        "ALTER TABLE shadow_trades DROP COLUMN IF EXISTS closure_path"
    ))
    op.execute(sa.text(
        "ALTER TABLE shadow_trades DROP COLUMN IF EXISTS entry_boundary_ambiguous_at"
    ))
    op.execute(sa.text(
        "ALTER TABLE trade_simulations ALTER COLUMN barrier_touched TYPE VARCHAR(20)"
    ))
    op.execute(sa.text(
        "ALTER TABLE shadow_trades ALTER COLUMN barrier_touched TYPE VARCHAR(20)"
    ))
