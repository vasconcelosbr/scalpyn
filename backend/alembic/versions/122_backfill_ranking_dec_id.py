"""Backfill decision_id in ml_opportunity_rankings via heuristic JOIN.

Matches NULL-decision_id rankings to decisions_log rows by symbol + ranked_at
proximity (±5 seconds). This covers future gap scenarios where the linkage
UPDATE in pipeline_scan.py fails transiently.

For the 505 canary-2 rankings (2026-06-25): the corresponding decisions_log rows
were never persisted (ORM mapper bug — FK to table without ORM class caused
configure_mappers() to fail, rolling back the transaction). Those 505 rows will
remain NULL after this migration — expected and acceptable.

Revision ID: 122_backfill_ranking_dec_id
Revises: 121_shadow_validation_cycle
Create Date: 2026-06-29
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "122_backfill_ranking_dec_id"
down_revision = "121_shadow_validation_cycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text("""
        UPDATE ml_opportunity_rankings r
        SET decision_id = (
            SELECT d.id
            FROM decisions_log d
            WHERE d.symbol = r.symbol
              AND d.created_at BETWEEN r.ranked_at - interval '5 seconds'
                                   AND r.ranked_at + interval '5 seconds'
            ORDER BY ABS(EXTRACT(EPOCH FROM (d.created_at - r.ranked_at)))
            LIMIT 1
        )
        WHERE r.decision_id IS NULL
    """))


def downgrade() -> None:
    pass
