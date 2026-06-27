"""Autopilot shadow calibration: run errors table + fix requires_human_approval.

Revision ID: 115_autopilot_shadow_calibration
Revises: 114_watchlist_priority
Create Date: 2026-06-27
"""

from alembic import op
import sqlalchemy as sa


revision = "115_autopilot_shadow_calibration"
down_revision = "114_watchlist_priority"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Fix existing shadow suggestions — wrongly set to requires_human_approval=true at insert time
    op.execute("""
        UPDATE profile_adjustment_suggestions
        SET requires_human_approval = false
        WHERE requires_human_approval = true
          AND status IN ('PENDING_SHADOW_VALIDATION', 'SHADOW_APPLIED', 'SHADOW_VALIDATING')
          AND mutation_applied = false
    """)

    # 2. Fix existing autopilot_pending_actions with SHADOW scope
    op.execute("""
        UPDATE autopilot_pending_actions
        SET requires_human_approval = false
        WHERE requires_human_approval = true
          AND target_scope = 'SHADOW'
          AND mutation_applied = false
    """)

    # 3. Create autopilot_run_errors table for detailed error tracking per cycle
    op.execute("""
        CREATE TABLE IF NOT EXISTS autopilot_run_errors (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            run_id uuid NULL,
            phase text NOT NULL DEFAULT 'shadow_calibration',
            error_code text NOT NULL DEFAULT 'UNKNOWN',
            severity text NOT NULL DEFAULT 'error',
            profile_id uuid NULL,
            suggestion_id uuid NULL,
            action_id uuid NULL,
            message text NOT NULL,
            stack_trace text NULL,
            payload jsonb NULL DEFAULT '{}',
            created_at timestamptz NOT NULL DEFAULT now()
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_autopilot_run_errors_run_id
        ON autopilot_run_errors(run_id)
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_autopilot_run_errors_profile_id
        ON autopilot_run_errors(profile_id)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS autopilot_run_errors")
    # Note: we do not revert requires_human_approval changes — they were bugs
