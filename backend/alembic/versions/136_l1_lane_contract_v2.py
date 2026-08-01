"""L1-only lane eligibility contract and certified collection frontier.

Revision ID: 136_l1_lane_contract_v2
Revises: 135_l1_dedup_constraint
Create Date: 2026-07-27

This migration changes only the ``L1_SPECTRUM`` section of active ML config.
It does not update, delete or reinterpret any shadow trade and does not touch
the ``L3_PROFILE`` contract.
"""

from alembic import op
import sqlalchemy as sa


revision = "136_l1_lane_contract_v2"
down_revision = "135_l1_dedup_constraint"
branch_labels = None
depends_on = None


_L1_V2 = """
{
  "version": "l1_spectrum_entry_v2",
  "min_row_coverage": 0.7,
  "required": [
    "taker_ratio",
    "volume_delta",
    "rsi",
    "macd_histogram_pct",
    "macd_histogram_slope",
    "adx",
    "adx_acceleration",
    "spread_pct",
    "volume_spike",
    "bb_width",
    "atr_pct",
    "ema9_gt_ema21",
    "orderbook_depth_usdt",
    "vwap_distance_pct",
    "rsi_slope_3",
    "rsi_slope_5",
    "macd_hist_slope_3",
    "macd_hist_slope_5",
    "ema21_ema50_distance_pct",
    "di_plus_minus_diff",
    "adx_slope_3",
    "vwap_reclaim_bool",
    "higher_highs_5",
    "higher_lows_5"
  ],
  "optional": [
    "volume_24h_usdt",
    "flow_strength",
    "momentum_strength",
    "delta_normalized",
    "ema_distance_pct",
    "ema50_distance_pct",
    "ema200_distance_pct"
  ]
}
"""

_L1_EXCLUSIONS = """
[
  "liquidity_score",
  "market_structure_score",
  "momentum_score",
  "signal_score",
  "di_trend",
  "trend_alignment",
  "ema50_gt_ema200"
]
"""

_L1_V1 = """
{
  "required": [
    "taker_ratio",
    "volume_delta",
    "rsi",
    "macd_histogram_pct",
    "macd_histogram_slope",
    "adx",
    "adx_acceleration",
    "spread_pct",
    "volume_spike",
    "bb_width",
    "atr_pct",
    "ema9_gt_ema21",
    "ema50_gt_ema200",
    "orderbook_depth_usdt",
    "vwap_distance_pct",
    "rsi_slope_3",
    "rsi_slope_5",
    "macd_hist_slope_3",
    "macd_hist_slope_5",
    "ema21_ema50_distance_pct",
    "di_plus_minus_diff",
    "adx_slope_3",
    "vwap_reclaim_bool",
    "higher_highs_5",
    "higher_lows_5"
  ],
  "optional": [
    "volume_24h_usdt",
    "flow_strength",
    "trend_alignment",
    "momentum_strength",
    "delta_normalized",
    "ema_distance_pct",
    "ema50_distance_pct",
    "ema200_distance_pct"
  ]
}
"""


def upgrade() -> None:
    op.execute(
        sa.text(
            f"""
            UPDATE config_profiles
               SET config_json =
                   jsonb_set(
                     jsonb_set(
                       jsonb_set(
                         config_json,
                         '{{ml_feature_contract}}',
                         COALESCE(config_json->'ml_feature_contract', '{{}}'::jsonb)
                           || jsonb_build_object(
                                'L1_SPECTRUM',
                                CAST(:l1_contract AS jsonb)
                              ),
                         true
                       ),
                       '{{ml_l1_feature_contract_version}}',
                       to_jsonb('l1_spectrum_entry_v2'::text),
                       true
                     ),
                     '{{ml_l1_feature_exclusions}}',
                     CAST(:l1_exclusions AS jsonb),
                     true
                   )
                   || jsonb_build_object(
                        'ml_l1_dataset_valid_from',
                        to_char(
                          clock_timestamp() AT TIME ZONE 'UTC',
                          'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
                        )
                      ),
                   updated_at = clock_timestamp()
             WHERE config_type = 'ml'
               AND is_active IS TRUE
            """
        ).bindparams(
            l1_contract=_L1_V2,
            l1_exclusions=_L1_EXCLUSIONS,
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE config_profiles
               SET config_json =
                   jsonb_set(
                     config_json,
                     '{ml_feature_contract}',
                     COALESCE(config_json->'ml_feature_contract', '{}'::jsonb)
                       || jsonb_build_object(
                            'L1_SPECTRUM',
                            CAST(:l1_contract AS jsonb)
                          ),
                     true
                   )
                   - 'ml_l1_feature_contract_version'
                   - 'ml_l1_feature_exclusions'
                   - 'ml_l1_dataset_valid_from',
                   updated_at = clock_timestamp()
             WHERE config_type = 'ml'
               AND is_active IS TRUE
            """
        ).bindparams(l1_contract=_L1_V1)
    )
