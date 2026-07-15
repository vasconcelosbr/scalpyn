"""Calibration orchestration, versioned EV, and state timeline.

Revision ID: 132_calibration_orchestration_v2
Revises: 131_ml_governance_v2
Create Date: 2026-07-11

All tables are additive. IDs are application-generated so this migration does
not require extension installation during a production cold start.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "132_calibration_orchestration_v2"
down_revision = "131_ml_governance_v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "calibration_recommendations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cycle_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("base_profile_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("recommendation_type", sa.String(48), nullable=False),
        sa.Column("target_path", sa.Text, nullable=False),
        sa.Column("current_value", postgresql.JSONB, nullable=False),
        sa.Column("proposed_value", postgresql.JSONB, nullable=False),
        sa.Column("bounded_change", postgresql.JSONB, nullable=False),
        sa.Column("evidence_refs", postgresql.JSONB, nullable=False),
        sa.Column("expected_impact", postgresql.JSONB, nullable=False),
        sa.Column("risk", sa.String(16), nullable=False),
        sa.Column("confidence", sa.Numeric(7, 6), nullable=False),
        sa.Column("validation_required", sa.String(32), nullable=False),
        sa.Column("rollback_condition", sa.Text, nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("idempotency_key", sa.String(160), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["base_profile_version_id"], ["profile_versions.id"], ondelete="RESTRICT"),
    )
    op.create_index(
        "ix_calibration_recommendations_profile_status",
        "calibration_recommendations",
        ["profile_id", "status", "created_at"],
    )

    op.create_table(
        "calibration_proposals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("recommendation_id", postgresql.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("base_profile_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("challenger_profile_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("challenger_profile_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("autopilot_candidate_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("before_config", postgresql.JSONB, nullable=False),
        sa.Column("after_config", postgresql.JSONB, nullable=False),
        sa.Column("diff", postgresql.JSONB, nullable=False),
        sa.Column("expected_impact", postgresql.JSONB, nullable=False),
        sa.Column("idempotency_key", sa.String(160), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["recommendation_id"], ["calibration_recommendations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["base_profile_version_id"], ["profile_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["challenger_profile_id"], ["profiles.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["challenger_profile_version_id"], ["profile_versions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["autopilot_candidate_id"], ["profile_intelligence_autopilot_candidates.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "ix_calibration_proposals_profile_state",
        "calibration_proposals",
        ["profile_id", "state", "created_at"],
    )

    op.create_table(
        "calibration_state_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cycle_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("recommendation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("proposal_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("previous_state", sa.String(32), nullable=True),
        sa.Column("new_state", sa.String(32), nullable=False),
        sa.Column("actor", sa.String(80), nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("metrics", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("artifact_refs", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["recommendation_id"], ["calibration_recommendations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["proposal_id"], ["calibration_proposals.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "ix_calibration_state_events_profile_created",
        "calibration_state_events",
        ["profile_id", "created_at"],
    )

    op.create_table(
        "calibration_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("proposal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("champion_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("challenger_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("window_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_to", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metrics_before", postgresql.JSONB, nullable=False),
        sa.Column("metrics_after", postgresql.JSONB, nullable=False),
        sa.Column("expected_delta", postgresql.JSONB, nullable=False),
        sa.Column("realized_delta", postgresql.JSONB, nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("decision_reasons", postgresql.JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["proposal_id"], ["calibration_proposals.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["champion_version_id"], ["profile_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["challenger_version_id"], ["profile_versions.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("proposal_id", "window_from", "window_to", name="uq_calibration_result_window"),
    )

    op.create_table(
        "profile_version_ev_scores",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("profile_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("timeframe", sa.String(16), nullable=False),
        sa.Column("window_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_to", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_n", sa.Integer, nullable=False),
        sa.Column("effective_n", sa.Numeric, nullable=False),
        sa.Column("net_ev", sa.Numeric, nullable=True),
        sa.Column("ci95_lower", sa.Numeric, nullable=True),
        sa.Column("ci95_upper", sa.Numeric, nullable=True),
        sa.Column("win_rate", sa.Numeric, nullable=True),
        sa.Column("drawdown", sa.Numeric, nullable=True),
        sa.Column("stability", sa.Numeric, nullable=True),
        sa.Column("score", sa.Numeric, nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("audit_json", postgresql.JSONB, nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["profile_version_id"], ["profile_versions.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("profile_version_id", "timeframe", "window_from", "window_to", name="uq_profile_version_ev_window"),
    )
    op.create_index(
        "ix_profile_version_ev_current",
        "profile_version_ev_scores",
        ["profile_id", "computed_at"],
    )

    op.create_table(
        "crypto_profile_ev_scores",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("profile_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("timeframe", sa.String(16), nullable=False),
        sa.Column("window_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_to", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_n", sa.Integer, nullable=False),
        sa.Column("effective_n", sa.Numeric, nullable=False),
        sa.Column("expected_ev", sa.Numeric, nullable=True),
        sa.Column("realized_ev", sa.Numeric, nullable=True),
        sa.Column("confidence", sa.Numeric, nullable=True),
        sa.Column("score", sa.Numeric, nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("audit_json", postgresql.JSONB, nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["profile_version_id"], ["profile_versions.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("profile_version_id", "symbol", "timeframe", "window_from", "window_to", name="uq_crypto_profile_ev_window"),
    )
    op.create_index(
        "ix_crypto_profile_ev_current",
        "crypto_profile_ev_scores",
        ["symbol", "computed_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_crypto_profile_ev_current", table_name="crypto_profile_ev_scores")
    op.drop_table("crypto_profile_ev_scores")
    op.drop_index("ix_profile_version_ev_current", table_name="profile_version_ev_scores")
    op.drop_table("profile_version_ev_scores")
    op.drop_table("calibration_results")
    op.drop_index("ix_calibration_state_events_profile_created", table_name="calibration_state_events")
    op.drop_table("calibration_state_events")
    op.drop_index("ix_calibration_proposals_profile_state", table_name="calibration_proposals")
    op.drop_table("calibration_proposals")
    op.drop_index("ix_calibration_recommendations_profile_status", table_name="calibration_recommendations")
    op.drop_table("calibration_recommendations")
