"""Require coherent per-row native feature coverage for L3 training.

Revision ID: 141_l3_native_feature_coverage
Revises: 140_xgboost_dual_lane
Create Date: 2026-07-24

This migration changes only non-secret ML JSON configuration. It does not
rewrite immutable shadow data, train, approve, promote, or delete models.
"""

from alembic import op
import sqlalchemy as sa


revision = "141_l3_native_feature_coverage"
down_revision = "140_xgboost_dual_lane"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE config_profiles
            SET config_json = jsonb_set(
                    config_json,
                    '{ml_feature_contract,L3_PROFILE,min_row_coverage}',
                    COALESCE(
                        config_json
                            #> '{ml_feature_contract,L3_PROFILE,min_row_coverage}',
                        '0.7'::jsonb
                    ),
                    true
                ),
                updated_at = NOW()
            WHERE config_type = 'ml'
              AND is_active = TRUE
              AND config_json #> '{ml_feature_contract,L3_PROFILE}'
                    IS NOT NULL
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE config_profiles
            SET config_json = jsonb_set(
                    config_json,
                    '{ml_feature_contract,L3_PROFILE}',
                    (config_json #> '{ml_feature_contract,L3_PROFILE}')
                        - 'min_row_coverage',
                    true
                ),
                updated_at = NOW()
            WHERE config_type = 'ml'
              AND is_active = TRUE
              AND config_json
                    #> '{ml_feature_contract,L3_PROFILE,min_row_coverage}'
                    IS NOT NULL
            """
        )
    )
