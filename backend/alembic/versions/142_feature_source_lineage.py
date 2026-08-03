"""Add immutable causal feature-source timestamp to shadow captures.

Revision ID: 142_feature_source_lineage
Revises: 141_l3_profile_consolidation
"""

from alembic import op
from sqlalchemy import text


revision = "142_feature_source_lineage"
down_revision = "141_l3_profile_consolidation"
branch_labels = None
depends_on = None


_NATIVE_IMMUTABILITY_FUNCTION = """
CREATE OR REPLACE FUNCTION prevent_shadow_native_capture_update()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF ROW(
      NEW.features_snapshot, NEW.feature_source_at, NEW.feature_source_times,
      NEW.features_captured_at, NEW.feature_hash,
      NEW.feature_extractor_version, NEW.feature_schema_version,
      NEW.capture_contract_version, NEW.symbol, NEW.exchange, NEW.timeframe,
      NEW.source, NEW.profile_id, NEW.ranking_id, NEW.decision_id
  ) IS DISTINCT FROM ROW(
      OLD.features_snapshot, OLD.feature_source_at, OLD.feature_source_times,
      OLD.features_captured_at, OLD.feature_hash,
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
"""


def upgrade() -> None:
    op.execute(text("SET LOCAL lock_timeout = '10s'"))
    op.execute(text("""
        ALTER TABLE shadow_trades
        ADD COLUMN IF NOT EXISTS feature_source_at TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS feature_source_times JSONB
    """))
    op.execute(text(_NATIVE_IMMUTABILITY_FUNCTION))


def downgrade() -> None:
    op.execute(text("SET LOCAL lock_timeout = '10s'"))
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
    op.execute(text("""
        ALTER TABLE shadow_trades
        DROP COLUMN IF EXISTS feature_source_times,
        DROP COLUMN IF EXISTS feature_source_at
    """))
