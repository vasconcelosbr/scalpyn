"""Configure the governed L3_PROFILE 1,200-row candidate validation gate.

Revision ID: 146_l3_1200_validation
Revises: 145_l3_historical_lineage
"""

from alembic import op
import sqlalchemy as sa


revision = "146_l3_1200_validation"
down_revision = "145_l3_historical_lineage"
branch_labels = None
depends_on = None

_DESCRIPTION = "ML L3_PROFILE 1200-row candidate validation gate"

_NEW_CONFIG = """
config_json || jsonb_build_object(
  'ml_l3_training_contract_version',
      'l3_profile_30d_causal_1200_validation_v1',
  'ml_catboost_retrain_min_eligible_rows', 1200,
  'ml_catboost_min_train_samples', 600,
  'ml_catboost_min_validation_samples', 200,
  'ml_catboost_min_test_samples', 200
)
"""


def upgrade() -> None:
    op.execute(sa.text(f"""
        WITH target AS (
          SELECT id, user_id, config_json AS previous_json,
                 {_NEW_CONFIG} AS new_json
          FROM config_profiles
          WHERE config_type = 'ml' AND is_active IS TRUE
        )
        INSERT INTO config_audit_log (
          id, config_id, changed_by, previous_json, new_json,
          change_description, changed_at
        )
        SELECT gen_random_uuid(), id, user_id, previous_json, new_json,
               '{_DESCRIPTION}', clock_timestamp()
        FROM target
        WHERE previous_json IS DISTINCT FROM new_json
    """))
    op.execute(sa.text(f"""
        UPDATE config_profiles
        SET config_json = {_NEW_CONFIG},
            updated_at = clock_timestamp()
        WHERE config_type = 'ml' AND is_active IS TRUE
    """))


def downgrade() -> None:
    op.execute(sa.text(f"""
        WITH restore AS (
          SELECT DISTINCT ON (cal.config_id)
                 cal.config_id, cp.user_id, cp.config_json AS current_json,
                 cal.previous_json AS restored_json
          FROM config_audit_log cal
          JOIN config_profiles cp ON cp.id = cal.config_id
          WHERE cal.change_description = '{_DESCRIPTION}'
          ORDER BY cal.config_id, cal.changed_at DESC
        ), audit AS (
          INSERT INTO config_audit_log (
            id, config_id, changed_by, previous_json, new_json,
            change_description, changed_at
          )
          SELECT gen_random_uuid(), config_id, user_id, current_json, restored_json,
                 'Rollback {_DESCRIPTION}', clock_timestamp()
          FROM restore
          WHERE current_json IS DISTINCT FROM restored_json
        )
        UPDATE config_profiles cp
        SET config_json = restore.restored_json,
            updated_at = clock_timestamp()
        FROM restore
        WHERE cp.id = restore.config_id
    """))
