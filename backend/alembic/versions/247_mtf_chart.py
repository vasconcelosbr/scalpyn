"""Isolated observational chart evaluations and shadow events.

Revision ID: 247_mtf_chart
Revises: 246_ohlcv_recovery
"""
import json
from alembic import op
import sqlalchemy as sa

revision = "247_mtf_chart"
down_revision = "246_ohlcv_recovery"
branch_labels = None
depends_on = None

# Frozen experimental seed. No modifications to operational trading configs.
PRESET = {
    "schema_version": "mtf_chart_v1", "enabled": True, "calibrated": False,
    "default_watchlist": "l3-consolidated", "monitored_watchlists": ["l3-consolidated"],
    "candle_limits": {"4h": 300, "1h": 300, "5m": 500},
    "poll_seconds": 15, "stale_tf_multiplier": 2, "max_assets_per_watchlist": 100,
    "pivot_left": 3, "pivot_right": 3, "doji_body_pct": 0.1,
    "pin_wick_ratio": 2, "pin_wick_pct": 0.6, "pin_close_fraction": 1/3,
    "engulf_include_wicks": False, "marubozu_wick_pct": 0.1, "min_range_atr": 0.25,
    "vol_mult": 1.5, "vol_lookback": 20, "zone_atr_mult": 0.5,
    "zone_min_touches": 2, "zone_proximity_atr": 0.75, "rejection_lookback": 3,
    "stop_buffer_atr": 0.2, "max_stop_atr": 3, "min_rr_net": 2.5,
    "rr_bonus_threshold": 3, "max_spread_pct": 0.2,
    "fee_per_side_pct": 0.1, "slippage_pct": 0.05,
    "capital_usdt": 10000, "risk_per_trade_pct": 1, "partial_tp1_pct": 50,
    "expires_after_candles": 3, "holding_candles": 36, "min_score": 7,
    "score_weights": {"trend": 2, "force": 1, "zone": 2, "rejection": 1,
                      "trigger": 2, "volume": 1, "rr": 1},
    "trigger_patterns": ["ENGULF_BULL", "PIN_BULL", "MARUBOZU_BULL"],
    "history_page_size": 30, "confidence_level": 0.95, "min_report_sample": 50,
}


def upgrade():
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
    op.execute(sa.text("""CREATE TABLE mtf_chart_evaluations (
        id uuid PRIMARY KEY, user_id uuid NOT NULL REFERENCES users(id),
        watchlist_id text NOT NULL, symbol text NOT NULL, identity text NOT NULL UNIQUE,
        observed_at timestamptz NOT NULL, payload jsonb NOT NULL, config_snapshot jsonb NOT NULL,
        CHECK (payload->>'mode' = 'SHADOW'),
        CHECK (payload->>'operational_effect' = 'false'))"""))
    op.execute(sa.text("CREATE INDEX ix_mtf_chart_user_history ON mtf_chart_evaluations(user_id,watchlist_id,symbol,observed_at DESC)"))
    op.execute(sa.text("""CREATE TABLE mtf_chart_shadow_events (
        id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        evaluation_id uuid NOT NULL REFERENCES mtf_chart_evaluations(id),
        candle_time bigint NOT NULL, observed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        state jsonb NOT NULL, UNIQUE(evaluation_id,candle_time))"""))
    op.execute(sa.text("""INSERT INTO config_profiles(id,user_id,config_type,config_json,is_active,created_at,updated_at)
        SELECT gen_random_uuid(),u.id,'mtf_analysis',CAST(:config AS jsonb),true,now(),now()
        FROM users u WHERE NOT EXISTS (SELECT 1 FROM config_profiles c
        WHERE c.user_id=u.id AND c.config_type='mtf_analysis' AND c.pool_id IS NULL)""").bindparams(config=json.dumps(PRESET)))


def downgrade():
    # Operational rollback preserves immutable observations; disable via config.
    pass
