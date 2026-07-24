"""Configure governed XGBoost training for the L1 and L3 lanes.

Revision ID: 140_xgboost_dual_lane
Revises: 139_pi_ai_v2
Create Date: 2026-07-24

This migration changes only non-secret JSON configuration. It does not train,
approve, promote, delete, or rewrite any model or trading dataset.
"""

from alembic import op
import sqlalchemy as sa


revision = "140_xgboost_dual_lane"
down_revision = "139_pi_ai_v2"
branch_labels = None
depends_on = None


_XGBOOST_SEARCH_SPACE = """
{
  "n_estimators": {"type": "int", "low": 150, "high": 600, "step": 50},
  "max_depth": {"type": "int", "low": 3, "high": 8},
  "learning_rate": {"type": "float", "low": 0.01, "high": 0.2, "log": true},
  "subsample": {"type": "float", "low": 0.6, "high": 1.0},
  "colsample_bytree": {"type": "float", "low": 0.5, "high": 1.0},
  "min_child_weight": {"type": "float", "low": 1.0, "high": 20.0, "log": true},
  "gamma": {"type": "float", "low": 0.0, "high": 5.0},
  "reg_alpha": {"type": "float", "low": 0.001, "high": 10.0, "log": true},
  "reg_lambda": {"type": "float", "low": 0.1, "high": 20.0, "log": true}
}
"""


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE config_profiles
            SET config_json =
                config_json
                || jsonb_build_object(
                    'ml_xgboost_l1_retrain_min_eligible_rows',
                    COALESCE(
                        config_json->'ml_xgboost_l1_retrain_min_eligible_rows',
                        config_json->'ml_retrain_min_eligible_rows',
                        '3000'::jsonb
                    ),
                    'ml_xgboost_l3_retrain_min_eligible_rows',
                    COALESCE(
                        config_json->'ml_xgboost_l3_retrain_min_eligible_rows',
                        config_json->'ml_catboost_retrain_min_eligible_rows',
                        '200'::jsonb
                    ),
                    'ml_optuna_search_space',
                    COALESCE(config_json->'ml_optuna_search_space', '{}'::jsonb)
                    || jsonb_build_object(
                        'xgboost',
                        COALESCE(
                            config_json#>'{ml_optuna_search_space,xgboost}',
                            CAST(:search_space AS jsonb)
                        )
                    )
                ),
                updated_at = NOW()
            WHERE config_type = 'ml'
              AND is_active = TRUE
            """
        ).bindparams(search_space=_XGBOOST_SEARCH_SPACE)
    )
    op.execute(
        sa.text(
            """
            UPDATE config_profiles
            SET config_json =
                (
                    config_json
                    - 'enable_lightgbm'
                    - 'enable_catboost'
                    - 'catboost_source_filter'
                )
                || jsonb_build_object(
                    'enable_xgboost_l1',
                    COALESCE(config_json->'enable_xgboost_l1', 'false'::jsonb),
                    'enable_xgboost_l3',
                    COALESCE(config_json->'enable_xgboost_l3', 'false'::jsonb),
                    'xgboost_l3_source_filter',
                    COALESCE(
                        config_json->'xgboost_l3_source_filter',
                        config_json->'catboost_source_filter',
                        '["L3"]'::jsonb
                    )
                ),
                updated_at = NOW()
            WHERE config_type = 'profile_intelligence'
              AND is_active = TRUE
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE config_profiles
            SET config_json =
                config_json
                - 'enable_xgboost_l1'
                - 'enable_xgboost_l3'
                - 'xgboost_l3_source_filter',
                updated_at = NOW()
            WHERE config_type = 'profile_intelligence'
              AND is_active = TRUE
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE config_profiles
            SET config_json =
                config_json
                - 'ml_xgboost_l1_retrain_min_eligible_rows'
                - 'ml_xgboost_l3_retrain_min_eligible_rows',
                updated_at = NOW()
            WHERE config_type = 'ml'
              AND is_active = TRUE
            """
        )
    )
