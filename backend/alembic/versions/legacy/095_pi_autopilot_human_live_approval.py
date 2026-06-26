"""Require explicit human approval before PI Auto-Pilot live activation.

Revision ID: 095_pi_human_live_approval
Revises: 094_autopilot_scope_audit
Create Date: 2026-06-19
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision = "095_pi_human_live_approval"
down_revision = "094_autopilot_scope_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "profile_intelligence_autopilot_candidates",
        sa.Column(
            "approval_status",
            sa.String(length=30),
            nullable=False,
            server_default="pending",
        ),
    )
    op.add_column(
        "profile_intelligence_autopilot_candidates",
        sa.Column(
            "approval_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "profile_intelligence_autopilot_candidates",
        sa.Column("approved_by", UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "profile_intelligence_autopilot_candidates",
        sa.Column("approved_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column(
        "profile_intelligence_autopilot_candidates",
        sa.Column("approval_reason", sa.Text(), nullable=True),
    )
    op.add_column(
        "profile_intelligence_autopilot_candidates",
        sa.Column("approval_source", sa.String(length=80), nullable=True),
    )
    op.add_column(
        "profile_intelligence_autopilot_candidates",
        sa.Column("approval_snapshot_json", JSONB(), nullable=True),
    )
    op.add_column(
        "profile_intelligence_autopilot_candidates",
        sa.Column("promotion_blocked_reason", sa.Text(), nullable=True),
    )
    op.add_column(
        "profile_intelligence_autopilot_candidates",
        sa.Column("rollback_payload", JSONB(), nullable=True),
    )
    op.add_column(
        "profile_intelligence_autopilot_candidates",
        sa.Column("live_activation_attempted_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column(
        "profile_intelligence_autopilot_candidates",
        sa.Column("live_activated_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_pi_autopilot_candidate_approved_by",
        "profile_intelligence_autopilot_candidates",
        "users",
        ["approved_by"],
        ["id"],
        ondelete="SET NULL",
    )

    op.execute(sa.text("""
        UPDATE profile_intelligence_autopilot_candidates
        SET state = 'PENDING_HUMAN_APPROVAL',
            approval_status = 'pending',
            approval_required = true,
            promotion_blocked_reason = 'migration_blocked_legacy_auto_promotion'
        WHERE state = 'APPROVED_WAITING_LIVE'
    """))
    op.execute(sa.text("""
        UPDATE profile_intelligence_autopilot_candidates
        SET state = 'SHADOW_READY'
        WHERE state = 'SHADOW_READY_FOR_REVIEW'
    """))
    op.create_check_constraint(
        "ck_pi_candidate_live_requires_human_approval",
        "profile_intelligence_autopilot_candidates",
        """
        state NOT IN ('APPROVED_FOR_LIVE', 'LIVE_ACTIVATED')
        OR (
            approval_status = 'approved'
            AND approved_by IS NOT NULL
            AND approved_at IS NOT NULL
            AND approval_reason IS NOT NULL
            AND rollback_payload IS NOT NULL
        )
        """,
    )


def downgrade() -> None:
    op.execute(sa.text("""
        UPDATE profile_intelligence_autopilot_candidates
        SET state = 'SHADOW_READY_FOR_REVIEW'
        WHERE state = 'SHADOW_READY'
    """))
    op.execute(sa.text("""
        UPDATE profile_intelligence_autopilot_candidates
        SET state = 'APPROVED_WAITING_LIVE'
        WHERE state IN ('PENDING_HUMAN_APPROVAL', 'APPROVED_FOR_LIVE')
    """))
    op.drop_constraint(
        "ck_pi_candidate_live_requires_human_approval",
        "profile_intelligence_autopilot_candidates",
        type_="check",
    )
    op.drop_constraint(
        "fk_pi_autopilot_candidate_approved_by",
        "profile_intelligence_autopilot_candidates",
        type_="foreignkey",
    )
    for column in (
        "live_activated_at",
        "live_activation_attempted_at",
        "rollback_payload",
        "promotion_blocked_reason",
        "approval_snapshot_json",
        "approval_source",
        "approval_reason",
        "approved_at",
        "approved_by",
        "approval_required",
        "approval_status",
    ):
        op.drop_column("profile_intelligence_autopilot_candidates", column)
