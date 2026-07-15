"""Native point-in-time feature capture contract.

Revision ID: 133_native_feature_capture
Revises: 132_calibration_orchestration_v2
"""

from alembic import op
from sqlalchemy import text


revision = "133_native_feature_capture"
down_revision = "132_calibration_orchestration_v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text("""
        ALTER TABLE shadow_trades
            ADD COLUMN IF NOT EXISTS feature_extractor_version VARCHAR(80),
            ADD COLUMN IF NOT EXISTS capture_contract_version VARCHAR(80)
    """))
    op.execute(text("""
        CREATE OR REPLACE FUNCTION prevent_shadow_native_capture_update()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF ROW(
              NEW.features_snapshot, NEW.features_captured_at, NEW.feature_hash,
              NEW.feature_extractor_version, NEW.feature_schema_version,
              NEW.capture_contract_version, NEW.symbol, NEW.exchange, NEW.timeframe,
              NEW.source, NEW.profile_id, NEW.ranking_id, NEW.decision_id
          ) IS DISTINCT FROM ROW(
              OLD.features_snapshot, OLD.features_captured_at, OLD.feature_hash,
              OLD.feature_extractor_version, OLD.feature_schema_version,
              OLD.capture_contract_version, OLD.symbol, OLD.exchange, OLD.timeframe,
              OLD.source, OLD.profile_id, OLD.ranking_id, OLD.decision_id
          ) THEN
            RAISE EXCEPTION 'shadow native capture contract is immutable after INSERT'
              USING ERRCODE = 'check_violation';
          END IF;
          RETURN NEW;
        END;
        $$
    """))
    op.execute(text("DROP TRIGGER IF EXISTS trg_shadow_native_capture_immutable ON shadow_trades"))
    op.execute(text("""
        CREATE TRIGGER trg_shadow_native_capture_immutable
        BEFORE UPDATE ON shadow_trades
        FOR EACH ROW EXECUTE FUNCTION prevent_shadow_native_capture_update()
    """))


def downgrade() -> None:
    op.execute(text("DROP TRIGGER IF EXISTS trg_shadow_native_capture_immutable ON shadow_trades"))
    op.execute(text("DROP FUNCTION IF EXISTS prevent_shadow_native_capture_update()"))
    op.execute(text("""
        ALTER TABLE shadow_trades
            DROP COLUMN IF EXISTS capture_contract_version,
            DROP COLUMN IF EXISTS feature_extractor_version
    """))
