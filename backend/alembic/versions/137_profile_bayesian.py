"""Profile Bayesian Intelligence isolated persistence.

Revision ID: 137_profile_bayesian
Revises: 136_l1_lane_contract_v2
Create Date: 2026-07-27

All objects are additive and outside the ML/trading schemas. UUIDs are
application-generated, so the migration does not require a database extension.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "137_profile_bayesian"
down_revision = "136_l1_lane_contract_v2"
branch_labels = None
depends_on = None


ANALYSIS_STATUSES = (
    "PENDING", "BUILDING_DATASET", "VALIDATING_DATA", "SAMPLING",
    "RUNNING_DIAGNOSTICS", "ANALYZING_POSTERIOR", "COMPLETED",
    "COMPLETED_WITH_WARNINGS", "FAILED", "CANCELLED",
)
CANDIDATE_STATUSES = (
    "DRAFT", "ANALYZED", "REPLAY_PENDING", "REPLAY_RUNNING",
    "REPLAY_FAILED", "REPLAY_REJECTED", "VALIDATED", "SHADOW_PENDING",
    "SHADOW_RUNNING", "SHADOW_REJECTED", "AWAITING_HUMAN_APPROVAL",
    "APPROVED", "REJECTED", "ACTIVATED", "ROLLED_BACK",
)


def _check_values(column: str, values: tuple[str, ...], name: str) -> sa.CheckConstraint:
    quoted = ", ".join(f"'{value}'" for value in values)
    return sa.CheckConstraint(f"{column} IN ({quoted})", name=name)


def upgrade() -> None:
    op.create_table(
        "profile_bayesian_dataset_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("profile_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("dataset_hash", sa.String(64), nullable=False),
        sa.Column("policy_hash", sa.String(64), nullable=False),
        sa.Column("window_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_to", sa.DateTime(timezone=True), nullable=False),
        sa.Column("row_count", sa.Integer, nullable=False),
        sa.Column("observation_ids", postgresql.JSONB, nullable=False),
        sa.Column("manifest", postgresql.JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["profile_version_id"], ["profile_versions.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("user_id", "dataset_hash", name="uq_profile_bayesian_dataset_hash"),
        sa.CheckConstraint("row_count >= 0", name="ck_profile_bayesian_dataset_row_count"),
        sa.CheckConstraint("window_to >= window_from", name="ck_profile_bayesian_dataset_window"),
    )
    op.create_index(
        "ix_profile_bayesian_dataset_profile_created",
        "profile_bayesian_dataset_snapshots",
        ["profile_id", "created_at"],
    )

    op.create_table(
        "profile_bayesian_analysis_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("profile_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("dataset_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("idempotency_key", sa.String(180), nullable=False, unique=True),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("diagnostic_status", sa.String(32), nullable=True),
        sa.Column("random_seed", sa.BigInteger, nullable=False),
        sa.Column("code_version", sa.String(80), nullable=False),
        sa.Column("git_commit", sa.String(64), nullable=True),
        sa.Column("model_config", postgresql.JSONB, nullable=False),
        sa.Column("sampler_config", postgresql.JSONB, nullable=False),
        sa.Column("dependency_versions", postgresql.JSONB, nullable=False),
        sa.Column("filters", postgresql.JSONB, nullable=False),
        sa.Column("warnings", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("requested_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", sa.String(80), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["profile_version_id"], ["profile_versions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["dataset_snapshot_id"], ["profile_bayesian_dataset_snapshots.id"], ondelete="SET NULL"
        ),
        _check_values("status", ANALYSIS_STATUSES, "ck_profile_bayesian_analysis_status"),
    )
    op.create_index(
        "ix_profile_bayesian_analysis_profile_created",
        "profile_bayesian_analysis_runs",
        ["profile_id", "created_at"],
    )

    op.create_table(
        "profile_bayesian_indicator_effects",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("analysis_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("indicator", sa.String(80), nullable=False),
        sa.Column("regime", sa.String(80), nullable=True),
        sa.Column("effect_direction", sa.String(16), nullable=False),
        sa.Column("estimated_tp_lift", sa.Numeric(16, 10), nullable=True),
        sa.Column("estimated_pnl_lift", sa.Numeric(16, 10), nullable=True),
        sa.Column("probability_positive_effect", sa.Numeric(8, 7), nullable=True),
        sa.Column("credible_interval_95", postgresql.JSONB, nullable=False),
        sa.Column("direct_sample_size", sa.Integer, nullable=False),
        sa.Column("shared_sample_size", sa.Integer, nullable=False),
        sa.Column("effective_sample_size", sa.Numeric(16, 4), nullable=True),
        sa.Column("evidence_grade", sa.String(24), nullable=False),
        sa.Column("diagnostic_status", sa.String(32), nullable=False),
        sa.Column("recommendation", sa.String(40), nullable=False),
        sa.Column("details", postgresql.JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["analysis_run_id"], ["profile_bayesian_analysis_runs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "analysis_run_id", "indicator", "regime", name="uq_profile_bayesian_effect_scope"
        ),
        sa.CheckConstraint("direct_sample_size >= 0", name="ck_profile_bayesian_effect_direct_n"),
        sa.CheckConstraint("shared_sample_size >= 0", name="ck_profile_bayesian_effect_shared_n"),
    )
    op.create_index(
        "ix_profile_bayesian_effect_profile_grade",
        "profile_bayesian_indicator_effects",
        ["profile_id", "evidence_grade", "created_at"],
    )

    op.create_table(
        "profile_bayesian_diagnostics",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("analysis_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_name", sa.String(80), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("rhat_max", sa.Numeric(12, 8), nullable=True),
        sa.Column("effective_sample_size_min", sa.Numeric(16, 4), nullable=True),
        sa.Column("divergences", sa.Integer, nullable=False, server_default="0"),
        sa.Column("posterior_predictive_check", postgresql.JSONB, nullable=False),
        sa.Column("credible_intervals", postgresql.JSONB, nullable=False),
        sa.Column("sampling_warnings", postgresql.JSONB, nullable=False),
        sa.Column("details", postgresql.JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["analysis_run_id"], ["profile_bayesian_analysis_runs.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("analysis_run_id", "model_name", name="uq_profile_bayesian_diagnostic_model"),
        sa.CheckConstraint("divergences >= 0", name="ck_profile_bayesian_diagnostic_divergences"),
    )

    op.create_table(
        "profile_optimization_studies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("analysis_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(180), nullable=False, unique=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("sampler", sa.String(40), nullable=False),
        sa.Column("directions", postgresql.JSONB, nullable=False),
        sa.Column("search_space", postgresql.JSONB, nullable=False),
        sa.Column("constraints", postgresql.JSONB, nullable=False),
        sa.Column("windows", postgresql.JSONB, nullable=False),
        sa.Column("random_seed", sa.BigInteger, nullable=False),
        sa.Column("total_trials", sa.Integer, nullable=False, server_default="0"),
        sa.Column("valid_trials", sa.Integer, nullable=False, server_default="0"),
        sa.Column("warnings", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("task_id", sa.String(80), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["analysis_run_id"], ["profile_bayesian_analysis_runs.id"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint("total_trials >= 0", name="ck_profile_optimization_total_trials"),
        sa.CheckConstraint("valid_trials >= 0", name="ck_profile_optimization_valid_trials"),
    )
    op.create_index(
        "ix_profile_optimization_profile_created",
        "profile_optimization_studies",
        ["profile_id", "created_at"],
    )

    op.create_table(
        "profile_optimization_trials",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("study_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trial_number", sa.Integer, nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("parameters", postgresql.JSONB, nullable=False),
        sa.Column("objective_values", postgresql.JSONB, nullable=False),
        sa.Column("metrics", postgresql.JSONB, nullable=False),
        sa.Column("constraint_violations", postgresql.JSONB, nullable=False),
        sa.Column("is_valid", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["study_id"], ["profile_optimization_studies.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("study_id", "trial_number", name="uq_profile_optimization_trial_number"),
        sa.CheckConstraint("trial_number >= 0", name="ck_profile_optimization_trial_number"),
    )

    op.create_table(
        "profile_optimization_trial_metrics",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("trial_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("metric_name", sa.String(80), nullable=False),
        sa.Column("metric_value", sa.Numeric(24, 10), nullable=True),
        sa.Column("metric_json", postgresql.JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["trial_id"], ["profile_optimization_trials.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("trial_id", "metric_name", name="uq_profile_optimization_trial_metric"),
    )

    op.create_table(
        "profile_bayesian_candidate_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("base_profile_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("analysis_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("optimization_study_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("autopilot_candidate_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("changes", postgresql.JSONB, nullable=False),
        sa.Column("evidence", postgresql.JSONB, nullable=False),
        sa.Column("validation_metrics", postgresql.JSONB, nullable=False),
        sa.Column("shadow_metrics", postgresql.JSONB, nullable=False),
        sa.Column("approval_status", sa.String(30), nullable=False),
        sa.Column("approved_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rollback_reference", postgresql.JSONB, nullable=True),
        sa.Column("idempotency_key", sa.String(180), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["base_profile_version_id"], ["profile_versions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["analysis_run_id"], ["profile_bayesian_analysis_runs.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["optimization_study_id"], ["profile_optimization_studies.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["autopilot_candidate_id"],
            ["profile_intelligence_autopilot_candidates.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["approved_by"], ["users.id"], ondelete="SET NULL"),
        _check_values("status", CANDIDATE_STATUSES, "ck_profile_bayesian_candidate_status"),
        sa.CheckConstraint(
            "source = 'PROFILE_BAYESIAN_INTELLIGENCE'",
            name="ck_profile_bayesian_candidate_source",
        ),
    )
    op.create_index(
        "ix_profile_bayesian_candidate_profile_status",
        "profile_bayesian_candidate_links",
        ["profile_id", "status", "created_at"],
    )

    op.create_table(
        "profile_bayesian_audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("analysis_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("study_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("candidate_link_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("previous_status", sa.String(40), nullable=True),
        sa.Column("new_status", sa.String(40), nullable=True),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["analysis_run_id"], ["profile_bayesian_analysis_runs.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["study_id"], ["profile_optimization_studies.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["candidate_link_id"], ["profile_bayesian_candidate_links.id"], ondelete="SET NULL"
        ),
    )
    op.create_index(
        "ix_profile_bayesian_audit_profile_created",
        "profile_bayesian_audit_events",
        ["profile_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_profile_bayesian_audit_profile_created", table_name="profile_bayesian_audit_events")
    op.drop_table("profile_bayesian_audit_events")
    op.drop_index(
        "ix_profile_bayesian_candidate_profile_status", table_name="profile_bayesian_candidate_links"
    )
    op.drop_table("profile_bayesian_candidate_links")
    op.drop_table("profile_optimization_trial_metrics")
    op.drop_table("profile_optimization_trials")
    op.drop_index("ix_profile_optimization_profile_created", table_name="profile_optimization_studies")
    op.drop_table("profile_optimization_studies")
    op.drop_table("profile_bayesian_diagnostics")
    op.drop_index(
        "ix_profile_bayesian_effect_profile_grade", table_name="profile_bayesian_indicator_effects"
    )
    op.drop_table("profile_bayesian_indicator_effects")
    op.drop_index(
        "ix_profile_bayesian_analysis_profile_created", table_name="profile_bayesian_analysis_runs"
    )
    op.drop_table("profile_bayesian_analysis_runs")
    op.drop_index(
        "ix_profile_bayesian_dataset_profile_created",
        table_name="profile_bayesian_dataset_snapshots",
    )
    op.drop_table("profile_bayesian_dataset_snapshots")
