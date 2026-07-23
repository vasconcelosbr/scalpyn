"""Add Profile Score Intelligence global optimization and challenger tracking.

Revision ID: 138_profile_score_optimization
Revises: 137_pi_manual_adjustments
Create Date: 2026-07-23

The migration is additive.  It does not alter ML datasets, shadow trade rows,
profile incumbents, or model registry state.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "138_profile_score_optimization"
down_revision = "137_pi_manual_adjustments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "profile_score_optimization_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("lookback_days", sa.Integer(), nullable=False),
        sa.Column("cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dataset_contract", sa.String(64), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(160), nullable=False),
        sa.Column("evidence_json", postgresql.JSONB, nullable=False),
        sa.Column("executive_report", postgresql.JSONB, nullable=True),
        sa.Column("adjustment_envelope", postgresql.JSONB, nullable=True),
        sa.Column("provider", sa.String(32), nullable=True),
        sa.Column("model", sa.String(120), nullable=True),
        sa.Column("skill_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("error_code", sa.String(120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["skill_id"], ["ai_skills.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_pi_score_run_user_idempotency"),
    )
    op.create_index(
        "ix_pi_score_runs_user_created",
        "profile_score_optimization_runs",
        ["user_id", "created_at"],
    )

    op.create_table(
        "profile_score_replay_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("champion_profile_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("champion_score_engine_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_config_hash", sa.String(64), nullable=False),
        sa.Column("candidate_config", postgresql.JSONB, nullable=False),
        sa.Column("window_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_to", sa.DateTime(timezone=True), nullable=False),
        sa.Column("champion_metrics", postgresql.JSONB, nullable=False),
        sa.Column("challenger_metrics", postgresql.JSONB, nullable=False),
        sa.Column("delta_metrics", postgresql.JSONB, nullable=False),
        sa.Column("gates", postgresql.JSONB, nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("evidence_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["run_id"], ["profile_score_optimization_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["champion_profile_version_id"], ["profile_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["champion_score_engine_version_id"], ["score_engine_versions.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("run_id", "profile_id", name="uq_pi_score_replay_run_profile"),
    )
    op.create_index(
        "ix_pi_score_replay_profile_status",
        "profile_score_replay_results",
        ["profile_id", "status", "created_at"],
    )

    op.create_table(
        "profile_score_optimization_challengers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("replay_result_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("champion_profile_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("challenger_profile_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("validation_gate", postgresql.JSONB, nullable=False),
        sa.Column("collection_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["run_id"], ["profile_score_optimization_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["replay_result_id"], ["profile_score_replay_results.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["champion_profile_version_id"], ["profile_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["challenger_profile_version_id"], ["profile_versions.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("replay_result_id", name="uq_pi_score_challenger_replay"),
    )
    op.create_index(
        "uq_pi_score_one_collecting_profile",
        "profile_score_optimization_challengers",
        ["profile_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('CREATED','COLLECTING','VALIDATED')"),
    )

    op.create_table(
        "profile_score_performance_daily",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("challenger_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("profile_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("score_engine_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("variant", sa.String(24), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("metric_date", sa.Date(), nullable=False),
        sa.Column("closed_trades", sa.Integer(), nullable=False),
        sa.Column("tp", sa.Integer(), nullable=False),
        sa.Column("sl", sa.Integer(), nullable=False),
        sa.Column("timeout", sa.Integer(), nullable=False),
        sa.Column("rapid_sl", sa.Integer(), nullable=False),
        sa.Column("pnl_sum_pct", sa.Numeric(18, 8), nullable=True),
        sa.Column("avg_pnl_pct", sa.Numeric(18, 8), nullable=True),
        sa.Column("avg_mae_pct", sa.Numeric(18, 8), nullable=True),
        sa.Column("avg_mfe_pct", sa.Numeric(18, 8), nullable=True),
        sa.Column("distinct_symbols", sa.Integer(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["challenger_id"], ["profile_score_optimization_challengers.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["profile_version_id"], ["profile_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["score_engine_version_id"], ["score_engine_versions.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "challenger_id", "variant", "source", "metric_date",
            name="uq_pi_score_daily_challenger_variant_date",
        ),
    )
    op.create_index(
        "ix_pi_score_daily_profile_date",
        "profile_score_performance_daily",
        ["profile_id", "metric_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_pi_score_daily_profile_date", table_name="profile_score_performance_daily")
    op.drop_table("profile_score_performance_daily")
    op.drop_index(
        "uq_pi_score_one_collecting_profile",
        table_name="profile_score_optimization_challengers",
    )
    op.drop_table("profile_score_optimization_challengers")
    op.drop_index("ix_pi_score_replay_profile_status", table_name="profile_score_replay_results")
    op.drop_table("profile_score_replay_results")
    op.drop_index("ix_pi_score_runs_user_created", table_name="profile_score_optimization_runs")
    op.drop_table("profile_score_optimization_runs")
