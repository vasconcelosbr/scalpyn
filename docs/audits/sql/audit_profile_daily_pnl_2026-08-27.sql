WITH active_ranking_config AS (
    SELECT DISTINCT ON (cp.user_id)
        cp.user_id,
        COALESCE(
            cp.config_json -> 'source_filter',
            '["L3", "L3_LAB", "L3_REJECTED"]'::jsonb
        ) AS source_filter
    FROM config_profiles AS cp
    WHERE cp.pool_id IS NULL
      AND cp.config_type = 'watchlist_performance_ranking'
      AND cp.is_active IS TRUE
    ORDER BY cp.user_id, cp.updated_at DESC
), eligible_trades AS (
    SELECT
        LEFT(md5(st.user_id::text), 12) AS user_key,
        st.id AS trade_id,
        st.source,
        st.outcome,
        st.pnl_pct,
        st.pnl_usdt,
        CASE
            WHEN st.exit_timestamp IS NOT NULL THEN 'exit_timestamp'
            WHEN st.completed_at IS NOT NULL THEN 'completed_at'
            WHEN st.updated_at IS NOT NULL THEN 'updated_at'
            ELSE 'created_at'
        END AS close_field,
        COALESCE(st.exit_timestamp, st.completed_at, st.updated_at, st.created_at) AS close_at
    FROM shadow_trades AS st
    JOIN pipeline_watchlists AS pw
      ON pw.id = st.watchlist_id
     AND pw.user_id = st.user_id
     AND pw.profile_id = st.profile_id
     AND UPPER(pw.level) = 'L3'
    JOIN active_ranking_config AS cfg
      ON cfg.user_id = st.user_id
    WHERE st.profile_id IS NOT NULL
      AND st.status = 'COMPLETED'
      AND st.source IN (
          SELECT jsonb_array_elements_text(cfg.source_filter)
      )
      AND COALESCE(st.exit_timestamp, st.completed_at, st.updated_at, st.created_at)
          >= TIMESTAMP '2026-08-13 00:00:00'
      AND COALESCE(st.exit_timestamp, st.completed_at, st.updated_at, st.created_at)
          < TIMESTAMP '2026-08-28 00:00:00'
), daily_totals AS (
    SELECT
        user_key,
        close_at::date AS metric_date,
        COUNT(*) AS completed_rows,
        COUNT(DISTINCT trade_id) AS distinct_trades,
        COUNT(*) FILTER (WHERE pnl_pct IS NOT NULL) AS displayed_finalized,
        COUNT(*) FILTER (WHERE pnl_usdt IS NOT NULL) AS pnl_rows,
        ROUND(COALESCE(SUM(pnl_usdt), 0)::numeric, 2) AS pnl_total_usdt
    FROM eligible_trades
    GROUP BY user_key, close_at::date
), close_breakdown AS (
    SELECT
        user_key,
        close_at::date AS metric_date,
        close_field,
        COUNT(*) AS trade_count,
        ROUND(COALESCE(SUM(pnl_usdt), 0)::numeric, 2) AS pnl_total_usdt
    FROM eligible_trades
    GROUP BY user_key, close_at::date, close_field
), outcome_breakdown AS (
    SELECT
        user_key,
        close_at::date AS metric_date,
        COALESCE(outcome, '<NULL>') AS outcome,
        COUNT(*) AS trade_count,
        ROUND(COALESCE(SUM(pnl_usdt), 0)::numeric, 2) AS pnl_total_usdt
    FROM eligible_trades
    GROUP BY user_key, close_at::date, COALESCE(outcome, '<NULL>')
)
SELECT
    current_setting('TimeZone') AS database_timezone,
    d.user_key,
    d.metric_date,
    d.completed_rows,
    d.distinct_trades,
    d.displayed_finalized,
    d.pnl_rows,
    d.pnl_total_usdt,
    (
        SELECT jsonb_object_agg(
            c.close_field,
            jsonb_build_object('trades', c.trade_count, 'pnl_usdt', c.pnl_total_usdt)
            ORDER BY c.close_field
        )
        FROM close_breakdown AS c
        WHERE c.user_key = d.user_key
          AND c.metric_date = d.metric_date
    ) AS close_field_breakdown,
    (
        SELECT jsonb_object_agg(
            o.outcome,
            jsonb_build_object('trades', o.trade_count, 'pnl_usdt', o.pnl_total_usdt)
            ORDER BY o.outcome
        )
        FROM outcome_breakdown AS o
        WHERE o.user_key = d.user_key
          AND o.metric_date = d.metric_date
    ) AS outcome_breakdown
FROM daily_totals AS d
ORDER BY d.user_key, d.metric_date;
