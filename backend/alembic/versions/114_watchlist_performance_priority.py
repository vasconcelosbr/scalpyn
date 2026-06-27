"""Watchlist performance priority base view and DB-backed score config.

Revision ID: 114_watchlist_priority
Revises: 113_pi_live_engine
Create Date: 2026-06-27
"""

from alembic import op
import sqlalchemy as sa


revision = "114_watchlist_priority"
down_revision = "113_pi_live_engine"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        CREATE OR REPLACE VIEW watchlist_performance_priority_base_view AS
        WITH trade_metrics AS (
            SELECT
                st.user_id,
                st.profile_id,
                COALESCE(MAX(st.profile_name), MAX(p.name)) AS profile_name,
                st.watchlist_id,
                COALESCE(MAX(st.watchlist_name), MAX(pw.name)) AS watchlist_name,
                COALESCE(MAX(st.watchlist_level), MAX(pw.level), 'L3') AS level,
                st.source,
                COUNT(*)::bigint AS total_trades,
                COUNT(*) FILTER (WHERE st.status IN ('PENDING', 'RUNNING'))::bigint AS open_trades,
                COUNT(*) FILTER (WHERE st.status = 'COMPLETED' AND st.pnl_pct IS NOT NULL)::bigint AS completed_trades,
                COUNT(*) FILTER (WHERE st.status = 'COMPLETED' AND st.pnl_pct > 0)::bigint AS wins,
                COUNT(*) FILTER (
                    WHERE st.status = 'COMPLETED' AND st.pnl_pct > 0
                      AND st.holding_seconds IS NOT NULL
                      AND st.holding_seconds <= (ranking_config.config_json #>> '{thresholds,tp4h_seconds}')::integer
                )::bigint AS tp_4h_wins,
                COALESCE(SUM(st.pnl_pct) FILTER (
                    WHERE st.status = 'COMPLETED' AND st.pnl_pct IS NOT NULL
                ), 0)::double precision AS pnl_pct_sum,
                COUNT(st.pnl_pct) FILTER (WHERE st.status = 'COMPLETED')::bigint AS pnl_count,
                COALESCE(SUM(st.pnl_usdt) FILTER (
                    WHERE st.status = 'COMPLETED' AND st.pnl_usdt IS NOT NULL
                ), 0)::double precision AS pnl_total_usdt,
                COALESCE(SUM(st.holding_seconds) FILTER (
                    WHERE st.status = 'COMPLETED' AND st.pnl_pct > 0 AND st.holding_seconds IS NOT NULL
                ), 0)::double precision AS holding_win_sum,
                COUNT(st.holding_seconds) FILTER (
                    WHERE st.status = 'COMPLETED' AND st.pnl_pct > 0
                )::bigint AS holding_win_count,
                MIN(st.created_at) AS first_trade,
                MAX(st.created_at) AS last_trade
            FROM shadow_trades st
            LEFT JOIN profiles p ON p.id = st.profile_id
            LEFT JOIN pipeline_watchlists pw ON pw.id = st.watchlist_id
            JOIN config_profiles ranking_config
              ON ranking_config.user_id = st.user_id
             AND ranking_config.pool_id IS NULL
             AND ranking_config.config_type = 'watchlist_performance_ranking'
             AND ranking_config.is_active = true
            WHERE st.profile_id IS NOT NULL
            GROUP BY st.user_id, st.profile_id, st.watchlist_id, st.source
        ), entities AS (
            SELECT
                pw.user_id,
                pw.profile_id,
                p.name AS profile_name,
                pw.id AS watchlist_id,
                pw.name AS watchlist_name,
                pw.level,
                NULL::varchar AS source,
                0::bigint AS total_trades,
                0::bigint AS open_trades,
                0::bigint AS completed_trades,
                0::bigint AS wins,
                0::bigint AS tp_4h_wins,
                0::double precision AS pnl_pct_sum,
                0::bigint AS pnl_count,
                0::double precision AS pnl_total_usdt,
                0::double precision AS holding_win_sum,
                0::bigint AS holding_win_count,
                NULL::timestamptz AS first_trade,
                NULL::timestamptz AS last_trade
            FROM pipeline_watchlists pw
            JOIN profiles p ON p.id = pw.profile_id
            WHERE UPPER(pw.level) = 'L3'
            UNION
            SELECT
                p.user_id, p.id, p.name, NULL::uuid, NULL::varchar, 'L3', NULL::varchar,
                0::bigint, 0::bigint, 0::bigint, 0::bigint, 0::bigint,
                0::double precision, 0::bigint, 0::double precision,
                0::double precision, 0::bigint, NULL::timestamptz, NULL::timestamptz
            FROM profiles p
            WHERE p.is_shadow_only = true
              AND NOT EXISTS (
                  SELECT 1 FROM pipeline_watchlists pw
                  WHERE pw.profile_id = p.id AND UPPER(pw.level) = 'L3'
              )
        )
        SELECT * FROM trade_metrics
        UNION ALL
        SELECT * FROM entities
    """))

    op.execute(sa.text("""
        INSERT INTO config_profiles
            (id, user_id, pool_id, config_type, config_json, is_active, created_at, updated_at)
        SELECT
            gen_random_uuid(), u.id, NULL, 'watchlist_performance_ranking',
            jsonb_build_object(
                'version', 1,
                'source_filter', jsonb_build_array('L3', 'L3_LAB'),
                'weights', jsonb_build_object('pnl', 35, 'win_rate', 20, 'sample', 15, 'tp4h', 15, 'pnl_total', 10),
                'normalization', jsonb_build_object('avg_pnl_pct_target', 1.0, 'sample_target', 500, 'pnl_total_usdt_target', 1000),
                'limits', jsonb_build_object('score_min', 0, 'score_max', 100, 'pnl_component_min', -20),
                'penalties', jsonb_build_object(
                    'holding_over_4h', 5, 'holding_over_8h', 10,
                    'low_n_under_30', 30, 'low_n_under_50', 15, 'low_n_under_100', 5,
                    'negative_avg_pnl', 25, 'negative_total_pnl', 10
                ),
                'thresholds', jsonb_build_object(
                    'sample_low_n', 30, 'sample_low', 50, 'sample_medium', 100, 'sample_high', 300,
                    'priority_a_plus', 75, 'priority_a', 60, 'priority_b', 45, 'priority_c', 30,
                    'low_n_score_cap', 44.99,
                    'good_win_rate', 0.50, 'good_tp4h_rate', 0.40, 'shadow_tp4h_rate', 0.20,
                    'tp4h_seconds', 14400, 'holding_warning_seconds', 14400, 'holding_severe_seconds', 28800
                )
            ),
            true, now(), now()
        FROM users u
        WHERE NOT EXISTS (
            SELECT 1 FROM config_profiles cp
            WHERE cp.user_id = u.id
              AND cp.pool_id IS NULL
              AND cp.config_type = 'watchlist_performance_ranking'
              AND cp.is_active = true
        )
    """))


def downgrade() -> None:
    op.execute(sa.text("DROP VIEW IF EXISTS watchlist_performance_priority_base_view"))
    op.execute(sa.text("""
        DELETE FROM config_profiles
        WHERE config_type = 'watchlist_performance_ranking'
    """))
