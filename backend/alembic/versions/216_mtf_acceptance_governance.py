"""Persist MTF calibration runs and deterministic L2 setup state.

Revision ID: 216_mtf_acceptance_governance
Revises: 215_r6_multilayer_contracts
"""

from alembic import op
import sqlalchemy as sa


revision = "216_mtf_acceptance_governance"
down_revision = "215_r6_multilayer_contracts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
    op.execute(sa.text("""
        INSERT INTO ohlcv_capture_contracts (
          capture_contract_version, valid_from, mode, source, timeframes,
          closed_table, live_table, canonical_read_enabled,
          finalization_delay_seconds
        ) VALUES (
          'spot_mtf_closed_ohlcv_v2', clock_timestamp() + INTERVAL '5 minutes',
          'CANONICAL', 'gate.io', '["15m","1h"]'::jsonb,
          'ohlcv', 'ohlcv_live', TRUE, 60
        )
        ON CONFLICT (capture_contract_version) DO NOTHING
    """))
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS mtf_calibration_runs (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          config_profile_id UUID NOT NULL
            REFERENCES config_profiles(id) ON DELETE RESTRICT,
          config_hash VARCHAR(64) NOT NULL,
          policy_version VARCHAR(80) NOT NULL,
          approved_policy_at TIMESTAMPTZ NOT NULL,
          approved_policy_hash VARCHAR(64) NOT NULL,
          idempotency_key VARCHAR(160) NOT NULL,
          status VARCHAR(32) NOT NULL,
          dataset_manifest JSONB NOT NULL DEFAULT '{}'::jsonb,
          dataset_hash VARCHAR(64) NULL,
          results_json JSONB NOT NULL DEFAULT '{}'::jsonb,
          selected_profiles JSONB NOT NULL DEFAULT '{}'::jsonb,
          failure_reason TEXT NULL,
          started_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
          completed_at TIMESTAMPTZ NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
          CONSTRAINT uq_mtf_calibration_run_key UNIQUE (idempotency_key),
          CONSTRAINT ck_mtf_calibration_run_status CHECK (
            status IN ('RUNNING', 'PASSED', 'DRAFT_INSUFFICIENT_DATA',
                       'DRAFT_REJECTED', 'FAILED')
          ),
          CONSTRAINT ck_mtf_calibration_manifest_object CHECK (
            jsonb_typeof(dataset_manifest) = 'object'
          ),
          CONSTRAINT ck_mtf_calibration_results_object CHECK (
            jsonb_typeof(results_json) = 'object'
          ),
          CONSTRAINT ck_mtf_calibration_profiles_object CHECK (
            jsonb_typeof(selected_profiles) = 'object'
          )
        )
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_mtf_calibration_runs_user_created
          ON mtf_calibration_runs (user_id, created_at DESC)
    """))
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS mtf_l2_setup_states (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          symbol VARCHAR(32) NOT NULL,
          profile_version_id UUID NOT NULL
            REFERENCES profile_versions(id) ON DELETE RESTRICT,
          last_candle_open_at TIMESTAMPTZ NOT NULL,
          last_indicators_hash VARCHAR(64) NOT NULL,
          state VARCHAR(32) NOT NULL,
          state_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
          state_hash VARCHAR(64) NOT NULL,
          updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
          CONSTRAINT uq_mtf_l2_state_identity UNIQUE
            (user_id, symbol, profile_version_id),
          CONSTRAINT ck_mtf_l2_state CHECK (
            state IN ('NONE', 'PULLBACK_SEEN', 'BREAKOUT_SEEN',
                      'PULLBACK_RECLAIM', 'BREAKOUT_RETEST', 'INVALIDATED')
          ),
          CONSTRAINT ck_mtf_l2_state_payload_object CHECK (
            jsonb_typeof(state_payload) = 'object'
          )
        )
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_mtf_l2_states_user_updated
          ON mtf_l2_setup_states (user_id, updated_at DESC)
    """))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS mtf_l2_setup_states"))
    op.execute(sa.text("DROP TABLE IF EXISTS mtf_calibration_runs"))
    op.execute(sa.text("""
        DELETE FROM ohlcv_capture_contracts
         WHERE capture_contract_version = 'spot_mtf_closed_ohlcv_v2'
           AND NOT EXISTS (
             SELECT 1 FROM ohlcv
              WHERE capture_contract_version = 'spot_mtf_closed_ohlcv_v2'
           )
    """))
