"""Add observational entry-risk capture fields to shadow trades.

Revision ID: 196_entry_risk_observation
Revises: 195_text_tolerant_prompt
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "196_entry_risk_observation"
down_revision = "195_text_tolerant_prompt"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "shadow_trades",
        sa.Column("entry_risk_features_json", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "shadow_trades",
        sa.Column(
            "entry_risk_capture_status",
            sa.String(length=32),
            nullable=False,
            server_default="NOT_AVAILABLE",
        ),
    )
    op.add_column(
        "shadow_trades",
        sa.Column("entry_risk_captured_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_shadow_entry_risk_capture_status",
        "shadow_trades",
        "entry_risk_capture_status IN ('NOT_AVAILABLE','PENDING','VALID','PARTIAL','INVALID','ERROR')",
    )
    op.create_index(
        "ix_shadow_entry_risk_capture_pending",
        "shadow_trades",
        ["entry_risk_capture_status", "created_at"],
    )
    op.execute(sa.text("""
        INSERT INTO config_profiles (
            id, user_id, pool_id, config_type, config_json,
            is_active, created_at, updated_at
        )
        SELECT gen_random_uuid(), u.id, NULL, 'entry_risk_observation',
               CAST(:config AS jsonb), true, now(), now()
          FROM users u
         WHERE NOT EXISTS (
             SELECT 1 FROM config_profiles cp
              WHERE cp.user_id = u.id
                AND cp.pool_id IS NULL
                AND cp.config_type = 'entry_risk_observation'
         )
    """).bindparams(config='{"schema_version":"entry_risk_observation_v1","capture_enabled":true,"legacy_enabled":true,"momentum_enabled":true,"momentum_operational":false,"exhaustion_enabled":true,"exhaustion_operational":false,"source_timeframe":"5m","source_stale_seconds":300}'))


def downgrade() -> None:
    op.execute(sa.text("""
        DELETE FROM config_profiles
         WHERE config_type = 'entry_risk_observation'
           AND config_json = CAST(:config AS jsonb)
    """).bindparams(config='{"schema_version":"entry_risk_observation_v1","capture_enabled":true,"legacy_enabled":true,"momentum_enabled":true,"momentum_operational":false,"exhaustion_enabled":true,"exhaustion_operational":false,"source_timeframe":"5m","source_stale_seconds":300}'))
    op.drop_index("ix_shadow_entry_risk_capture_pending", table_name="shadow_trades")
    op.drop_constraint("ck_shadow_entry_risk_capture_status", "shadow_trades", type_="check")
    op.drop_column("shadow_trades", "entry_risk_captured_at")
    op.drop_column("shadow_trades", "entry_risk_capture_status")
    op.drop_column("shadow_trades", "entry_risk_features_json")
