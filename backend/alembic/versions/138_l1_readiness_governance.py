"""Separate L1 exploratory fitting from promotion-ready retraining.

Revision ID: 138_l1_readiness_governance
Revises: 137_profile_bayesian
Create Date: 2026-07-30

The change is config-only and audit-logged. It does not change the certified
L1 frontier, shadow trades, model activation, or execution authority.
"""

from alembic import op
import sqlalchemy as sa


revision = "138_l1_readiness_governance"
down_revision = "137_profile_bayesian"
branch_labels = None
depends_on = None


_NEW_KEYS_SQL = """
jsonb_build_object(
  'ml_l1_exploratory_fit_min_eligible_rows', 400,
  'ml_l1_retrain_min_eligible_rows', 1500,
  'ml_l1_readiness_contract_version', 'l1_trainer_mature_v1',
  'ml_l1_frontier_reset_requires_audit', true
)
"""


def upgrade() -> None:
    op.execute(sa.text(f"""
        WITH target AS (
          SELECT
            id,
            user_id,
            config_json AS previous_json,
            config_json || {_NEW_KEYS_SQL} AS new_json
          FROM config_profiles
          WHERE config_type = 'ml' AND is_active IS TRUE
        )
        INSERT INTO config_audit_log (
          id, config_id, changed_by, previous_json, new_json,
          change_description, changed_at
        )
        SELECT
          gen_random_uuid(), id, user_id, previous_json, new_json,
          'ML L1 readiness governance: exploratory=400; official=1500; '
          'frontier unchanged; promotion and execution remain gated',
          clock_timestamp()
        FROM target
        WHERE previous_json IS DISTINCT FROM new_json
    """))
    op.execute(sa.text(f"""
        UPDATE config_profiles
        SET config_json = config_json || {_NEW_KEYS_SQL},
            updated_at = clock_timestamp()
        WHERE config_type = 'ml' AND is_active IS TRUE
    """))


def downgrade() -> None:
    op.execute(sa.text("""
        WITH target AS (
          SELECT
            id,
            user_id,
            config_json AS previous_json,
            config_json
              - 'ml_l1_exploratory_fit_min_eligible_rows'
              - 'ml_l1_readiness_contract_version'
              - 'ml_l1_frontier_reset_requires_audit'
              || jsonb_build_object(
                   'ml_l1_retrain_min_eligible_rows', 400
                 ) AS new_json
          FROM config_profiles
          WHERE config_type = 'ml' AND is_active IS TRUE
        )
        INSERT INTO config_audit_log (
          id, config_id, changed_by, previous_json, new_json,
          change_description, changed_at
        )
        SELECT
          gen_random_uuid(), id, user_id, previous_json, new_json,
          'Rollback ML L1 readiness governance to legacy single gate',
          clock_timestamp()
        FROM target
        WHERE previous_json IS DISTINCT FROM new_json
    """))
    op.execute(sa.text("""
        UPDATE config_profiles
        SET config_json =
              config_json
                - 'ml_l1_exploratory_fit_min_eligible_rows'
                - 'ml_l1_readiness_contract_version'
                - 'ml_l1_frontier_reset_requires_audit'
              || jsonb_build_object(
                   'ml_l1_retrain_min_eligible_rows', 400
                 ),
            updated_at = clock_timestamp()
        WHERE config_type = 'ml' AND is_active IS TRUE
    """))
