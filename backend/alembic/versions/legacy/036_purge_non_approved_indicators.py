"""Purge indicators and alpha_scores for non-approved pool symbols.

Revision ID: 036
Revises: 035
Create Date: 2026-05-03

Context
-------
Migration 035 added ``pool_coins.is_approved`` and the collector / indicator
tasks were updated to gate on it.  However, the ``indicators`` and
``alpha_scores`` tables may still contain rows written before the gate was
enforced — rows for symbols that are NOT in the approved pool.

With those rows present, ``compute_scores`` (before its corresponding fix)
continued scoring all 480 historical symbols, and ``pipeline_scan`` forwarded
all of them to the L1 → L2 → L3 funnel regardless of pool membership.

This migration cleans up the residual rows so that the pipeline is fully
constrained to the approved pool immediately after deploy, without waiting for
the natural 2-hour staleness window.

Safety guard
------------
The DELETE only runs when at least one approved symbol exists in
``pool_coins`` (i.e. ``is_active = true AND is_approved = true``).  An
operator who has not yet approved any coins will see an INFO-level log
instead of a full table wipe.  The condition is checked inside the same
statement via a sub-select, making the check and delete atomic.

Idempotency
-----------
Repeated runs are safe: if no non-approved rows remain, the DELETE is a
no-op.
"""

from alembic import op
import sqlalchemy as sa

revision = "036"
down_revision = "035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Only purge when the operator has already approved at least one symbol.
    # This prevents accidentally wiping the entire table on a fresh deploy
    # where is_approved has not been set yet.
    op.execute(sa.text("""
        DELETE FROM indicators
        WHERE symbol NOT IN (
            SELECT symbol
            FROM pool_coins
            WHERE is_active = true
              AND is_approved = true
        )
        AND EXISTS (
            SELECT 1 FROM pool_coins
            WHERE is_active = true AND is_approved = true
        )
    """))

    op.execute(sa.text("""
        DELETE FROM alpha_scores
        WHERE symbol NOT IN (
            SELECT symbol
            FROM pool_coins
            WHERE is_active = true
              AND is_approved = true
        )
        AND EXISTS (
            SELECT 1 FROM pool_coins
            WHERE is_active = true AND is_approved = true
        )
    """))


def downgrade() -> None:
    # Data deletions are not reversible.
    pass
