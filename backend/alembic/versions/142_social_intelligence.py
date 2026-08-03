"""Add immutable Social Intelligence runs and per-asset observations.

Revision ID: 142_social_intelligence
Revises: 141_l3_profile_consolidation
Create Date: 2026-08-03
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "142_social_intelligence"
down_revision = "141_l3_profile_consolidation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))

    op.create_table(
        "social_intelligence_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("contract_version", sa.String(64), nullable=False),
        sa.Column("external_run_id", sa.String(128), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("prompt_version", sa.String(128), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("accepted_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rejected_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("validation_errors", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("window_start < window_end", name="ck_social_runs_window_order"),
        sa.CheckConstraint("window_end <= collected_at", name="ck_social_runs_collected_after_window"),
        sa.UniqueConstraint("source", "external_run_id", name="uq_social_runs_source_external"),
    )
    op.create_index("ix_social_runs_window_end", "social_intelligence_runs", ["window_end"])

    op.create_table(
        "social_asset_observations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("social_intelligence_runs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("attention_score", sa.Float(), nullable=False),
        sa.Column("sentiment_score", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("sentiment_label", sa.String(32), nullable=False),
        sa.Column("recommendation", sa.String(32), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("narratives", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("anomalies", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("sources", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("contract_version", sa.String(64), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("attention_score >= 0 AND attention_score <= 100", name="ck_social_attention_range"),
        sa.CheckConstraint("sentiment_score >= 0 AND sentiment_score <= 100", name="ck_social_sentiment_range"),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_social_confidence_range"),
        sa.UniqueConstraint("run_id", "symbol", name="uq_social_observations_run_symbol"),
    )
    op.create_index(
        "ix_social_observations_symbol_window",
        "social_asset_observations",
        ["symbol", "window_end"],
    )

    # Seed every existing tenant with the user-approved values, but keep the
    # modifier dark until one real import is reconciled in production.
    op.execute(sa.text("""
        INSERT INTO config_profiles
            (id, user_id, pool_id, config_type, config_json, is_active, created_at, updated_at)
        SELECT
            gen_random_uuid(),
            u.id,
            NULL,
            'social_score',
            jsonb_build_object(
                'enabled', false,
                'spot_weight', 0.20,
                'futures_weight', 0.20,
                'max_age_seconds', 86400,
                'mode', 'symmetric',
                'formula_version', 'confidence_adjusted_v1'
            ),
            true,
            now(),
            now()
        FROM users u
        WHERE NOT EXISTS (
            SELECT 1
            FROM config_profiles cp
            WHERE cp.user_id = u.id
              AND cp.pool_id IS NULL
              AND cp.config_type = 'social_score'
              AND cp.is_active = true
        )
    """))


def downgrade() -> None:
    # Config rows may already contain audited user changes by the time a
    # rollback is requested. Keep them rather than deleting tenant data.
    op.drop_index("ix_social_observations_symbol_window", table_name="social_asset_observations")
    op.drop_table("social_asset_observations")
    op.drop_index("ix_social_runs_window_end", table_name="social_intelligence_runs")
    op.drop_table("social_intelligence_runs")
