"""ML governance v2, immutable snapshot lineage, and evidence registry.

Revision ID: 131_ml_governance_v2
Revises: 130_pool_asset_exclusions
Create Date: 2026-07-11

All changes are additive. Historical lineage that cannot be reconstructed is
explicitly marked unresolved and remains ineligible for training.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "131_ml_governance_v2"
down_revision = "130_pool_asset_exclusions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        ALTER TABLE ml_models
            ADD COLUMN IF NOT EXISTS descriptive_status VARCHAR(48),
            ADD COLUMN IF NOT EXISTS predictive_status VARCHAR(48),
            ADD COLUMN IF NOT EXISTS calibration_authority BOOLEAN NOT NULL DEFAULT false,
            ADD COLUMN IF NOT EXISTS rule_generation_authority BOOLEAN NOT NULL DEFAULT false,
            ADD COLUMN IF NOT EXISTS autopilot_authority BOOLEAN NOT NULL DEFAULT false,
            ADD COLUMN IF NOT EXISTS execution_authority BOOLEAN NOT NULL DEFAULT false,
            ADD COLUMN IF NOT EXISTS governance_reason JSONB NOT NULL DEFAULT '{}'::jsonb
    """))
    op.execute(sa.text("""
        ALTER TABLE ml_threshold_curve
            ADD COLUMN IF NOT EXISTS coverage NUMERIC,
            ADD COLUMN IF NOT EXISTS specificity NUMERIC,
            ADD COLUMN IF NOT EXISTS mcc NUMERIC,
            ADD COLUMN IF NOT EXISTS net_ev NUMERIC,
            ADD COLUMN IF NOT EXISTS pnl NUMERIC,
            ADD COLUMN IF NOT EXISTS lift NUMERIC
    """))

    op.execute(sa.text("""
        ALTER TABLE shadow_trades
            ADD COLUMN IF NOT EXISTS event_id UUID,
            ADD COLUMN IF NOT EXISTS snapshot_id UUID,
            ADD COLUMN IF NOT EXISTS exchange VARCHAR(32),
            ADD COLUMN IF NOT EXISTS timeframe VARCHAR(16),
            ADD COLUMN IF NOT EXISTS profile_version_id UUID,
            ADD COLUMN IF NOT EXISTS score_engine_version_id UUID,
            ADD COLUMN IF NOT EXISTS feature_schema_version VARCHAR(80),
            ADD COLUMN IF NOT EXISTS label_contract_version VARCHAR(80),
            ADD COLUMN IF NOT EXISTS barrier_contract_version VARCHAR(80),
            ADD COLUMN IF NOT EXISTS features_captured_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS label_resolved_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS features_coverage NUMERIC(7,6),
            ADD COLUMN IF NOT EXISTS oldest_indicator_age_s INTEGER,
            ADD COLUMN IF NOT EXISTS market_data_confidence NUMERIC(7,6),
            ADD COLUMN IF NOT EXISTS feature_hash VARCHAR(64),
            ADD COLUMN IF NOT EXISTS profile_config_hash VARCHAR(64),
            ADD COLUMN IF NOT EXISTS score_engine_config_hash VARCHAR(64),
            ADD COLUMN IF NOT EXISTS lineage_status VARCHAR(32),
            ADD COLUMN IF NOT EXISTS eligible_for_training BOOLEAN NOT NULL DEFAULT false
    """))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_shadow_trades_snapshot_id ON shadow_trades (snapshot_id)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_shadow_trades_event_id ON shadow_trades (event_id)"))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_shadow_trades_training_eligible
            ON shadow_trades (created_at, source)
         WHERE eligible_for_training = true
    """))

    op.execute(sa.text("""
        ALTER TABLE profile_versions
            ADD COLUMN IF NOT EXISTS parent_version_id UUID,
            ADD COLUMN IF NOT EXISTS config_hash VARCHAR(64),
            ADD COLUMN IF NOT EXISTS score_engine_version_id UUID,
            ADD COLUMN IF NOT EXISTS source_cycle_id UUID,
            ADD COLUMN IF NOT EXISTS source_recommendation_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            ADD COLUMN IF NOT EXISTS status VARCHAR(24),
            ADD COLUMN IF NOT EXISTS activated_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS deactivated_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS rollback_to_version_id UUID,
            ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(160)
    """))
    op.execute(sa.text("CREATE UNIQUE INDEX IF NOT EXISTS uq_profile_versions_idempotency_key ON profile_versions (idempotency_key) WHERE idempotency_key IS NOT NULL"))
    op.execute(sa.text("CREATE UNIQUE INDEX IF NOT EXISTS uq_profile_versions_one_champion ON profile_versions (profile_id) WHERE status = 'CHAMPION'"))
    op.execute(sa.text("CREATE UNIQUE INDEX IF NOT EXISTS uq_profile_versions_one_shadow ON profile_versions (profile_id) WHERE status = 'SHADOW'"))

    op.create_table(
        "score_engine_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("parent_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("config_hash", sa.String(64), nullable=False),
        sa.Column("rules", postgresql.JSONB, nullable=False),
        sa.Column("weights", postgresql.JSONB, nullable=False),
        sa.Column("thresholds", postgresql.JSONB, nullable=False),
        sa.Column("selected_rule_ids", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("config_hash", name="uq_score_engine_versions_config_hash"),
    )

    op.create_table(
        "ml_evidence_registry",
        sa.Column("evidence_id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("cycle_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("profile_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("profile_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("model_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_type", sa.String(16), nullable=False),
        sa.Column("source_version", sa.String(80), nullable=False),
        sa.Column("dataset_hash", sa.String(64), nullable=False),
        sa.Column("window_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_to", sa.DateTime(timezone=True), nullable=False),
        sa.Column("target_path", sa.Text, nullable=False),
        sa.Column("indicator", sa.String(80), nullable=False),
        sa.Column("operator", sa.String(24), nullable=False),
        sa.Column("lower", sa.Numeric, nullable=True),
        sa.Column("upper", sa.Numeric, nullable=True),
        sa.Column("baseline_metric", sa.Numeric, nullable=True),
        sa.Column("candidate_metric", sa.Numeric, nullable=True),
        sa.Column("delta_metric", sa.Numeric, nullable=True),
        sa.Column("expected_ev", sa.Numeric, nullable=True),
        sa.Column("ci95_lower", sa.Numeric, nullable=False),
        sa.Column("ci95_upper", sa.Numeric, nullable=False),
        sa.Column("raw_n", sa.Integer, nullable=False),
        sa.Column("effective_n", sa.Numeric, nullable=False),
        sa.Column("independent_windows", sa.Integer, nullable=False),
        sa.Column("symbols", sa.Integer, nullable=False),
        sa.Column("confidence", sa.Numeric(7, 6), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("limitations", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("cycle_id", "source_type", "source_version", "target_path", name="uq_ml_evidence_cycle_source_target"),
    )
    op.create_index("ix_ml_evidence_profile_status", "ml_evidence_registry", ["profile_id", "status", "created_at"])

    op.execute(sa.text("""
        UPDATE ml_models
           SET descriptive_status = 'DESCRIPTIVE_VALIDATED',
               predictive_status = 'PREDICTIVE_REJECTED',
               calibration_authority = false,
               rule_generation_authority = false,
               autopilot_authority = false,
               execution_authority = false,
               governance_reason = jsonb_build_object(
                   'classification', 'v77_independent_audit',
                   'reasons', jsonb_build_array(
                       'holdout_auc_below_random',
                       'holdout_fpr_excessive',
                       'f1_below_always_positive_baseline',
                       'negative_ev_all_tested_thresholds'
                   )
               ),
               metrics_json = COALESCE(metrics_json, '{}'::jsonb) || jsonb_build_object(
                   'governance_v2', jsonb_build_object(
                       'descriptive_status', 'DESCRIPTIVE_VALIDATED',
                       'predictive_status', 'PREDICTIVE_REJECTED',
                       'calibration_authority', false,
                       'rule_generation_authority', false,
                       'autopilot_authority', false,
                       'execution_authority', false
                   )
               )
         WHERE id = 'e3dd7497-0747-4132-84b3-98571bd4b7f3'::uuid
           AND version = '77'
    """))

    op.execute(sa.text("""
        UPDATE config_profiles
           SET config_json = COALESCE(config_json, '{}'::jsonb) || jsonb_build_object(
               'ml_predictive_gate_v2', COALESCE((config_json->>'ml_predictive_gate_v2')::boolean, false),
               'profile_versioning_v2', COALESCE((config_json->>'profile_versioning_v2')::boolean, false),
               'calibration_evidence_registry_v1', COALESCE((config_json->>'calibration_evidence_registry_v1')::boolean, false),
               'calibration_orchestrator_v1', COALESCE((config_json->>'calibration_orchestrator_v1')::boolean, false),
               'autopilot_calibration_v1', COALESCE((config_json->>'autopilot_calibration_v1')::boolean, false),
               'counterfactual_outcomes_v1', COALESCE((config_json->>'counterfactual_outcomes_v1')::boolean, false),
               'ev_score_v2', COALESCE((config_json->>'ev_score_v2')::boolean, false),
               'ml_frontend_status_v2', COALESCE((config_json->>'ml_frontend_status_v2')::boolean, false)
           )
         WHERE config_type = 'ml'
    """))
    op.execute(sa.text("""
        INSERT INTO ml_label_contracts (
            id, name, version, description, sql_expression, target_window_seconds
        ) VALUES
            ('net_return_pct_v2', 'target_net_return_pct', '2.0',
             'Economic regression target after fees, spread, slippage and funding',
             'gross_return_pct - fees_pct - spread_pct - slippage_pct - funding_pct', NULL),
            ('tp_before_sl_v2', 'target_tp_before_sl', '2.0',
             'Barrier classification under a versioned barrier contract',
             'resolution = ''TP''', NULL),
            ('mfe_pct_v1', 'target_mfe_pct', '1.0',
             'Maximum favorable excursion regression target', 'mfe_pct', NULL),
            ('mae_pct_v1', 'target_mae_pct', '1.0',
             'Maximum adverse excursion regression target', 'mae_pct', NULL),
            ('time_to_tp_v1', 'target_time_to_tp', '1.0',
             'Time to take-profit survival/regression target', 'time_to_tp_s', NULL)
        ON CONFLICT DO NOTHING
    """))


def downgrade() -> None:
    op.drop_index("ix_ml_evidence_profile_status", table_name="ml_evidence_registry")
    op.drop_table("ml_evidence_registry")
    op.drop_table("score_engine_versions")
    op.execute(sa.text("DROP INDEX IF EXISTS uq_profile_versions_one_shadow"))
    op.execute(sa.text("DROP INDEX IF EXISTS uq_profile_versions_one_champion"))
    op.execute(sa.text("DROP INDEX IF EXISTS uq_profile_versions_idempotency_key"))
    op.execute(sa.text("""
        ALTER TABLE profile_versions
            DROP COLUMN IF EXISTS idempotency_key,
            DROP COLUMN IF EXISTS rollback_to_version_id,
            DROP COLUMN IF EXISTS deactivated_at,
            DROP COLUMN IF EXISTS activated_at,
            DROP COLUMN IF EXISTS status,
            DROP COLUMN IF EXISTS source_recommendation_ids,
            DROP COLUMN IF EXISTS source_cycle_id,
            DROP COLUMN IF EXISTS score_engine_version_id,
            DROP COLUMN IF EXISTS config_hash,
            DROP COLUMN IF EXISTS parent_version_id
    """))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_shadow_trades_training_eligible"))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_shadow_trades_event_id"))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_shadow_trades_snapshot_id"))
    op.execute(sa.text("""
        ALTER TABLE shadow_trades
            DROP COLUMN IF EXISTS eligible_for_training,
            DROP COLUMN IF EXISTS lineage_status,
            DROP COLUMN IF EXISTS score_engine_config_hash,
            DROP COLUMN IF EXISTS profile_config_hash,
            DROP COLUMN IF EXISTS feature_hash,
            DROP COLUMN IF EXISTS market_data_confidence,
            DROP COLUMN IF EXISTS oldest_indicator_age_s,
            DROP COLUMN IF EXISTS features_coverage,
            DROP COLUMN IF EXISTS label_resolved_at,
            DROP COLUMN IF EXISTS features_captured_at,
            DROP COLUMN IF EXISTS barrier_contract_version,
            DROP COLUMN IF EXISTS label_contract_version,
            DROP COLUMN IF EXISTS feature_schema_version,
            DROP COLUMN IF EXISTS score_engine_version_id,
            DROP COLUMN IF EXISTS profile_version_id,
            DROP COLUMN IF EXISTS timeframe,
            DROP COLUMN IF EXISTS exchange,
            DROP COLUMN IF EXISTS snapshot_id,
            DROP COLUMN IF EXISTS event_id
    """))
    op.execute(sa.text("""
        ALTER TABLE ml_models
            DROP COLUMN IF EXISTS governance_reason,
            DROP COLUMN IF EXISTS execution_authority,
            DROP COLUMN IF EXISTS autopilot_authority,
            DROP COLUMN IF EXISTS rule_generation_authority,
            DROP COLUMN IF EXISTS calibration_authority,
            DROP COLUMN IF EXISTS predictive_status,
            DROP COLUMN IF EXISTS descriptive_status
    """))
    op.execute(sa.text("""
        ALTER TABLE ml_threshold_curve
            DROP COLUMN IF EXISTS lift,
            DROP COLUMN IF EXISTS pnl,
            DROP COLUMN IF EXISTS net_ev,
            DROP COLUMN IF EXISTS mcc,
            DROP COLUMN IF EXISTS specificity,
            DROP COLUMN IF EXISTS coverage
    """))
