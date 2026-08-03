"""Configure causal 30-day L3_PROFILE candidate training gates.

Revision ID: 143_l3_training_governance
Revises: 142_feature_source_lineage
"""

from alembic import op
import sqlalchemy as sa


revision = "143_l3_training_governance"
down_revision = "142_feature_source_lineage"
branch_labels = None
depends_on = None

_DESCRIPTION = "ML L3_PROFILE 30d causal candidate training governance v1"

_CATBOOST_SPACE = """
jsonb_build_object(
  'iterations', jsonb_build_object('type', 'int', 'low', 200, 'high', 600),
  'learning_rate', jsonb_build_object(
      'type', 'float', 'low', 0.01, 'high', 0.15, 'log', true
  ),
  'depth', jsonb_build_object('type', 'int', 'low', 3, 'high', 6),
  'l2_leaf_reg', jsonb_build_object('type', 'float', 'low', 3.0, 'high', 10.0),
  'min_data_in_leaf', jsonb_build_object('type', 'int', 'low', 20, 'high', 100),
  'random_strength', jsonb_build_object('type', 'float', 'low', 1.0, 'high', 10.0)
)
"""

_NEW_CONFIG = f"""
config_json
|| jsonb_build_object(
  'ml_l3_training_contract_version', 'l3_profile_30d_causal_v1',
  'ml_catboost_retrain_min_eligible_rows', 2000,
  'ml_catboost_train_size_ratio', 0.60,
  'ml_catboost_validation_size_ratio', 0.20,
  'ml_catboost_test_size_ratio', 0.20,
  'ml_catboost_min_train_samples', 1000,
  'ml_catboost_min_validation_samples', 200,
  'ml_catboost_min_test_samples', 200,
  'ml_catboost_early_stopping_rounds', 30,
  'ml_catboost_max_boundary_candidates', 200,
  'ml_catboost_base_params', jsonb_build_object(
      'task_type', 'CPU',
      'loss_function', 'Logloss',
      'eval_metric', 'AUC',
      'nan_mode', 'Min',
      'od_type', 'Iter',
      'use_best_model', true,
      'bootstrap_type', 'MVS',
      'subsample', 0.8
  ),
  'ml_optuna_max_trials', 100,
  'ml_optuna_timeout_seconds', 600,
  'ml_training_seed', 42
)
|| jsonb_build_object(
  'ml_optuna_search_space',
  jsonb_set(
    COALESCE(config_json->'ml_optuna_search_space', '{{}}'::jsonb),
    '{{catboost}}',
    {_CATBOOST_SPACE},
    true
  )
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
