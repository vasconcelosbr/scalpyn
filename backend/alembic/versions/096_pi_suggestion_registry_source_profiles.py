"""Complete PI suggestion registry and source-profile attribution.

Revision ID: 096_pi_suggestion_registry
Revises: 095_pi_human_live_approval
Create Date: 2026-06-20
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision = "096_pi_suggestion_registry"
down_revision = "095_pi_human_live_approval"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "profile_indicator_stats",
        sa.Column("source_profile_ids", JSONB(), nullable=True),
    )
    op.add_column(
        "profile_indicator_stats",
        sa.Column(
            "validation_status",
            sa.String(length=40),
            nullable=False,
            server_default="exploratory_only",
        ),
    )
    op.add_column(
        "profile_indicator_stats",
        sa.Column(
            "actionability_status",
            sa.String(length=40),
            nullable=False,
            server_default="exploratory_only",
        ),
    )
    op.add_column(
        "profile_indicator_stats",
        sa.Column("target_section", sa.String(length=80), nullable=True),
    )

    op.add_column(
        "profile_rule_combinations",
        sa.Column("source_profile_ids", JSONB(), nullable=True),
    )

    suggestion_columns = (
        sa.Column("source_type", sa.String(length=50), nullable=True),
        sa.Column("source_model_type", sa.String(length=30), nullable=True),
        sa.Column("source_model_id", UUID(as_uuid=True), nullable=True),
        sa.Column("source_run_id", UUID(as_uuid=True), nullable=True),
        sa.Column("profile_id", UUID(as_uuid=True), nullable=True),
        sa.Column("profile_name", sa.String(length=255), nullable=True),
        sa.Column("source_profile_ids", JSONB(), nullable=True),
        sa.Column("target_section", sa.String(length=80), nullable=True),
        sa.Column("target_field", sa.String(length=120), nullable=True),
        sa.Column("current_value", JSONB(), nullable=True),
        sa.Column("proposed_value", JSONB(), nullable=True),
        sa.Column("diff_json", JSONB(), nullable=True),
        sa.Column("confidence", sa.Numeric(), nullable=True),
        sa.Column("lift", sa.Numeric(), nullable=True),
        sa.Column("evidence_count", sa.Integer(), nullable=True),
        sa.Column("expected_impact", JSONB(), nullable=True),
        sa.Column("risk_level", sa.String(length=20), nullable=True),
        sa.Column("validation_status", sa.String(length=40), nullable=True),
        sa.Column("actionability_status", sa.String(length=40), nullable=True),
        sa.Column("blocked_reason", sa.Text(), nullable=True),
        sa.Column("applied_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("reverted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("rollback_payload", JSONB(), nullable=True),
        sa.Column("dataset_version", sa.String(length=80), nullable=True),
        sa.Column("feature_schema_version", sa.String(length=80), nullable=True),
        sa.Column("label_version", sa.String(length=80), nullable=True),
    )
    for column in suggestion_columns:
        op.add_column("profile_suggestions", column)
    op.alter_column(
        "profile_suggestions",
        "status",
        server_default="pending",
        existing_type=sa.String(length=30),
    )

    op.create_foreign_key(
        "fk_profile_suggestions_profile_id",
        "profile_suggestions",
        "profiles",
        ["profile_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "idx_pi_suggestion_source",
        "profile_suggestions",
        ["source_type", "source_run_id", "profile_id"],
    )
    op.create_index(
        "idx_pi_suggestion_validation",
        "profile_suggestions",
        ["validation_status", "actionability_status", "status"],
    )

    op.execute(sa.text("""
        UPDATE profile_suggestions
        SET source_run_id = run_id,
            source_type = COALESCE(
                source_type,
                (
                    SELECT c.combination_type
                    FROM profile_rule_combinations c
                    WHERE c.id = profile_suggestions.source_combination_id
                ),
                'legacy_profile_intelligence'
            ),
            validation_status = COALESCE(
                validation_status,
                (
                    SELECT c.validation_metrics_json->>'validation_status'
                    FROM profile_rule_combinations c
                    WHERE c.id = profile_suggestions.source_combination_id
                ),
                'blocked_no_validation'
            ),
            actionability_status = COALESCE(
                actionability_status,
                (
                    SELECT c.validation_metrics_json->>'actionability_status'
                    FROM profile_rule_combinations c
                    WHERE c.id = profile_suggestions.source_combination_id
                ),
                'exploratory_only'
            ),
            blocked_reason = COALESCE(
                blocked_reason,
                (
                    SELECT c.validation_metrics_json->>'blocked_reason'
                    FROM profile_rule_combinations c
                    WHERE c.id = profile_suggestions.source_combination_id
                ),
                'migration_requires_registry_review'
            ),
            status = CASE
                WHEN status IN ('created', 'applied') THEN 'applied'
                WHEN status IN ('rejected', 'reverted', 'expired') THEN status
                ELSE 'exploratory_only'
            END
    """))

    op.create_check_constraint(
        "ck_pi_suggestion_actionable_registry",
        "profile_suggestions",
        """
        status NOT IN ('validated', 'approved')
        OR (
            source_type IS NOT NULL
            AND source_run_id IS NOT NULL
            AND profile_id IS NOT NULL
            AND validation_status = 'validated'
            AND diff_json IS NOT NULL
            AND rollback_payload IS NOT NULL
        )
        """,
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_pi_suggestion_actionable_registry",
        "profile_suggestions",
        type_="check",
    )
    op.drop_index("idx_pi_suggestion_validation", table_name="profile_suggestions")
    op.drop_index("idx_pi_suggestion_source", table_name="profile_suggestions")
    op.drop_constraint(
        "fk_profile_suggestions_profile_id",
        "profile_suggestions",
        type_="foreignkey",
    )
    op.alter_column(
        "profile_suggestions",
        "status",
        server_default="pending_user_approval",
        existing_type=sa.String(length=30),
    )
    for column in (
        "label_version",
        "feature_schema_version",
        "dataset_version",
        "rollback_payload",
        "reason",
        "reverted_at",
        "applied_at",
        "blocked_reason",
        "actionability_status",
        "validation_status",
        "risk_level",
        "expected_impact",
        "evidence_count",
        "lift",
        "confidence",
        "diff_json",
        "proposed_value",
        "current_value",
        "target_field",
        "target_section",
        "source_profile_ids",
        "profile_name",
        "profile_id",
        "source_run_id",
        "source_model_id",
        "source_model_type",
        "source_type",
    ):
        op.drop_column("profile_suggestions", column)
    op.drop_column("profile_rule_combinations", "source_profile_ids")
    for column in (
        "target_section",
        "actionability_status",
        "validation_status",
        "source_profile_ids",
    ):
        op.drop_column("profile_indicator_stats", column)
