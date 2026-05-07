"""Backfill score fields in indicators_json.

Revision ID: 037
Revises: 036
Create Date: 2026-05-03

Context
-------
``compute_indicators`` now computes and persists ``score``, ``score_raw``,
``score_max``, and ``score_normalized`` inside ``indicators_json`` for every
new indicators row.

Existing rows were written before this change and therefore lack these fields.
Without a backfill, the UI receives ``null`` for ``score`` on all historical
rows, which breaks score bar rendering.

This migration sets all four fields to 0 for rows that are missing the
``score`` key.  Zero is a safe sentinel — it keeps the UI working (score bar
stays empty/grey) and makes it clear the value predates the computation.  New
rows written by the updated task will carry the real computed score.

Idempotency
-----------
The ``WHERE indicators_json->'score' IS NULL`` guard makes repeated runs a
no-op once all rows have been backfilled.
"""

from alembic import op
import sqlalchemy as sa

revision = "037"
down_revision = "036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        UPDATE indicators
        SET indicators_json = indicators_json || jsonb_build_object(
            'score',            0,
            'score_raw',        0,
            'score_max',        0,
            'score_normalized', 0
        )
        WHERE indicators_json->'score' IS NULL
    """))


def downgrade() -> None:
    # Removing computed score fields is non-trivial and not safely reversible;
    # a downgrade is intentionally a no-op.
    pass
