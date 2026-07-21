"""Add fail-closed manual Profile Intelligence adjustments.

Revision ID: 137_pi_manual_adjustments
Revises: 136_regime_history
Create Date: 2026-07-21

This migration is additive.  It does not touch capture, training, model, or
historical trade tables.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "137_pi_manual_adjustments"
down_revision = "136_regime_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "profile_intelligence_manual_adjustments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("indicator_stat_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("base_profile_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("applied_profile_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("rollback_profile_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action_type", sa.String(50), nullable=False),
        sa.Column("target_path", sa.Text(), nullable=True),
        sa.Column("current_value", postgresql.JSONB(), nullable=True),
        sa.Column("proposed_value", postgresql.JSONB(), nullable=True),
        sa.Column("before_config", postgresql.JSONB(), nullable=True),
        sa.Column("after_config", postgresql.JSONB(), nullable=True),
        sa.Column("diff", postgresql.JSONB(), nullable=True),
        sa.Column("evidence_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("statistical_warnings", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("config_hash_before", sa.String(64), nullable=True),
        sa.Column("config_hash_after", sa.String(64), nullable=True),
        sa.Column("preview_hash", sa.String(64), nullable=True),
        sa.Column("state", sa.String(40), nullable=False, server_default=sa.text("'MANUAL_DRAFT'")),
        sa.Column("idempotency_key", sa.String(160), nullable=False),
        sa.Column("justification", sa.Text(), nullable=True),
        sa.Column("risk_confirmed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("approved_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("rollback_reason", sa.Text(), nullable=True),
        sa.Column("mutation_source", sa.String(60), nullable=False, server_default=sa.text("'MANUAL_PROFILE_INTELLIGENCE'")),
        sa.Column("autopilot_applied", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("ml_training_mutated", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("historical_dataset_mutated", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("previewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rolled_back_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["base_profile_version_id"], ["profile_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["applied_profile_version_id"], ["profile_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["rollback_profile_version_id"], ["profile_versions.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_pi_manual_adjustment_idempotency"),
        sa.CheckConstraint(
            "state IN ('MANUAL_DRAFT','PENDING_MANUAL_APPROVAL','MANUALLY_APPROVED','APPLIED','REJECTED','ROLLED_BACK','CONFLICTED')",
            name="ck_pi_manual_adjustment_state",
        ),
        sa.CheckConstraint("autopilot_applied = false", name="ck_pi_manual_no_autopilot"),
        sa.CheckConstraint("ml_training_mutated = false", name="ck_pi_manual_no_training"),
        sa.CheckConstraint("historical_dataset_mutated = false", name="ck_pi_manual_no_history"),
    )
    op.create_index("idx_pi_manual_user_state", "profile_intelligence_manual_adjustments", ["user_id", "state", "created_at"])
    op.create_index("idx_pi_manual_profile", "profile_intelligence_manual_adjustments", ["profile_id", "created_at"])

    op.create_table(
        "profile_intelligence_manual_adjustment_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("adjustment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(60), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("payload_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["adjustment_id"], ["profile_intelligence_manual_adjustments.id"], ondelete="RESTRICT"),
    )
    op.create_index("idx_pi_manual_events_adjustment", "profile_intelligence_manual_adjustment_events", ["adjustment_id", "created_at"])


def downgrade() -> None:
    op.drop_index("idx_pi_manual_events_adjustment", table_name="profile_intelligence_manual_adjustment_events")
    op.drop_table("profile_intelligence_manual_adjustment_events")
    op.drop_index("idx_pi_manual_profile", table_name="profile_intelligence_manual_adjustments")
    op.drop_index("idx_pi_manual_user_state", table_name="profile_intelligence_manual_adjustments")
    op.drop_table("profile_intelligence_manual_adjustments")
