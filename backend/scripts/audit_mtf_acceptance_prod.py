"""Bounded, read-only production evidence for the Spot MTF acceptance gate."""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from decimal import Decimal
import json
import os
from typing import Any
from uuid import UUID

import asyncpg


def _json(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (Decimal, UUID)):
        return str(value)
    if isinstance(value, asyncpg.Record):
        return {key: _json(item) for key, item in value.items()}
    if isinstance(value, dict):
        return {key: _json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(item) for item in value]
    return value


async def _rows(connection: asyncpg.Connection, sql: str) -> list[dict[str, Any]]:
    return [_json(row) for row in await connection.fetch(sql)]


async def main() -> None:
    dsn = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_PUBLIC_URL_REQUIRED")
    connection = await asyncpg.connect(dsn=dsn, command_timeout=30)
    evidence: dict[str, Any] = {
        "captured_at": datetime.now().astimezone().isoformat(),
        "read_only": True,
    }
    queries = {
        "schema": """
            SELECT version_num FROM alembic_version
        """,
        "database": """
            SELECT current_database() AS database_name,
                   pg_database_size(current_database()) AS database_bytes,
                   temp_files, temp_bytes, deadlocks, stats_reset
              FROM pg_stat_database
             WHERE datname = current_database()
        """,
        "largest_relations": """
            SELECT relname,
                   pg_total_relation_size(relid) AS total_bytes,
                   pg_relation_size(relid) AS table_bytes,
                   pg_indexes_size(relid) AS index_bytes,
                   n_live_tup, n_dead_tup,
                   seq_scan, idx_scan, last_autovacuum, last_autoanalyze
              FROM pg_stat_user_tables
             ORDER BY pg_total_relation_size(relid) DESC
             LIMIT 20
        """,
        "active_queries": """
            SELECT pid, state, wait_event_type, wait_event,
                   EXTRACT(EPOCH FROM (clock_timestamp() - query_start))::bigint AS age_seconds,
                   left(regexp_replace(query, '\\s+', ' ', 'g'), 300) AS query
              FROM pg_stat_activity
             WHERE datname = current_database()
               AND pid <> pg_backend_pid()
               AND (state = 'idle in transaction'
                    OR (state = 'active' AND clock_timestamp() - query_start > interval '5 seconds'))
             ORDER BY query_start
             LIMIT 50
        """,
        "relevant_indexes": """
            SELECT tablename, indexname, indexdef
              FROM pg_indexes
             WHERE schemaname = 'public'
               AND tablename IN (
                 'ohlcv', 'indicators', 'shadow_trades', 'pool_coins',
                 'mtf_calibration_runs', 'mtf_l2_setup_states'
               )
             ORDER BY tablename, indexname
        """,
        "shadow_population": """
            SELECT user_id, count(*) AS completed_rows,
                   min(entry_timestamp) AS first_entry_at,
                   max(entry_timestamp) AS last_entry_at,
                   count(*) FILTER (
                     WHERE exit_metrics_json IS NOT NULL
                   ) AS with_exit_metrics,
                   count(*) FILTER (
                     WHERE profile_version_id IS NOT NULL
                       AND profile_config_hash IS NOT NULL
                   ) AS with_profile_lineage
              FROM shadow_trades
             WHERE status = 'COMPLETED' AND pnl_pct IS NOT NULL
               AND entry_timestamp IS NOT NULL AND exit_timestamp IS NOT NULL
             GROUP BY user_id
             ORDER BY completed_rows DESC
        """,
        "governed_cost_population": """
            SELECT count(*) AS completed_rows,
                   count(*) FILTER (
                     WHERE net_return_pct IS NOT NULL
                       AND fee_roundtrip_pct_applied IS NOT NULL
                   ) AS with_governed_cost,
                   min(entry_timestamp) FILTER (
                     WHERE net_return_pct IS NOT NULL
                       AND fee_roundtrip_pct_applied IS NOT NULL
                   ) AS first_governed_cost_at,
                   max(entry_timestamp) FILTER (
                     WHERE net_return_pct IS NOT NULL
                       AND fee_roundtrip_pct_applied IS NOT NULL
                   ) AS last_governed_cost_at
              FROM shadow_trades
             WHERE status = 'COMPLETED' AND pnl_pct IS NOT NULL
               AND entry_timestamp IS NOT NULL AND exit_timestamp IS NOT NULL
        """,
        "exit_metric_keys_recent": """
            WITH recent AS (
              SELECT exit_metrics_json
                FROM shadow_trades
               WHERE status = 'COMPLETED' AND pnl_pct IS NOT NULL
                 AND exit_metrics_json IS NOT NULL
               ORDER BY entry_timestamp DESC
               LIMIT 50000
            )
            SELECT key, count(*) AS sampled_rows,
                   (SELECT count(*) FROM recent) AS sample_size
              FROM recent
              CROSS JOIN LATERAL jsonb_object_keys(exit_metrics_json) AS key
             GROUP BY key
             ORDER BY sampled_rows DESC, key
             LIMIT 80
        """,
        "mtf_indicators": """
            SELECT timeframe,
                   count(*) AS snapshots,
                   count(DISTINCT symbol) AS symbols,
                   min(time) AS first_computed_at,
                   max(time) AS last_computed_at,
                   count(*) FILTER (
                     WHERE indicators_json->'price'->>'capture_contract_version'
                           = 'spot_mtf_closed_ohlcv_v2'
                       AND indicators_json->'price'->>'candle_policy' = 'CLOSED_ONLY'
                       AND indicators_json->'price'->>'candle_closed' = 'true'
                   ) AS governed_snapshots,
                   percentile_cont(0.5) WITHIN GROUP (
                     ORDER BY EXTRACT(EPOCH FROM (
                       (indicators_json->'price'->>'available_at')::timestamptz
                       - (indicators_json->'price'->>'source_timestamp')::timestamptz
                     ))
                   ) FILTER (
                     WHERE indicators_json->'price'->>'available_at'
                           ~ '^\\d{4}-\\d{2}-\\d{2}T'
                       AND indicators_json->'price'->>'source_timestamp'
                           ~ '^\\d{4}-\\d{2}-\\d{2}T'
                   ) AS median_open_to_available_seconds,
                   percentile_cont(0.95) WITHIN GROUP (
                     ORDER BY EXTRACT(EPOCH FROM (
                       (indicators_json->'price'->>'available_at')::timestamptz
                       - (indicators_json->'price'->>'source_timestamp')::timestamptz
                     ))
                   ) FILTER (
                     WHERE indicators_json->'price'->>'available_at'
                           ~ '^\\d{4}-\\d{2}-\\d{2}T'
                       AND indicators_json->'price'->>'source_timestamp'
                           ~ '^\\d{4}-\\d{2}-\\d{2}T'
                   ) AS p95_open_to_available_seconds
              FROM indicators
             WHERE market_type = 'spot' AND scheduler_group = 'structural'
               AND timeframe IN ('1h', '15m')
               AND time > now() - interval '30 days'
             GROUP BY timeframe
             ORDER BY timeframe
        """,
        "ohlcv_72h_integrity": """
            SELECT timeframe,
                   count(*) AS rows,
                   count(DISTINCT symbol) AS symbols,
                   count(*) FILTER (WHERE is_closed IS NOT TRUE) AS open_rows,
                   count(*) FILTER (WHERE ingested_at IS NULL) AS missing_ingested_at,
                   count(*) FILTER (
                     WHERE capture_contract_version IS NULL
                   ) AS missing_capture_contract,
                   max(time) AS latest_candle_open
              FROM ohlcv
             WHERE market_type = 'spot'
               AND timeframe IN ('5m', '15m', '1h')
               AND time > now() - interval '72 hours'
             GROUP BY timeframe
             ORDER BY timeframe
        """,
        "mtf_runtime_config": """
            SELECT user_id, updated_at,
                   config_json->'scanner'->'multilayer_contract'->>'enabled' AS enabled,
                   config_json->'scanner'->'multilayer_contract'->>'activation_mode' AS activation_mode,
                   config_json->'scanner'->'multilayer_contract'->>'operational_effect' AS operational_effect,
                   config_json->'scanner'->'multilayer_contract'->>'decision_feature_contract_version' AS contract_version
              FROM config_profiles
             WHERE config_type = 'spot_engine' AND is_active IS TRUE
             ORDER BY user_id, updated_at DESC
        """,
        "mtf_policies": """
            SELECT user_id, id, updated_at,
                   config_json->>'policy_version' AS policy_version,
                   config_json->>'approval_status' AS approval_status
              FROM config_profiles
             WHERE config_type = 'mtf_calibration' AND is_active IS TRUE
             ORDER BY user_id, updated_at DESC
        """,
        "mtf_runs": """
            SELECT user_id, status, count(*) AS runs,
                   max(created_at) AS latest_created_at
              FROM mtf_calibration_runs
             GROUP BY user_id, status
             ORDER BY user_id, status
        """,
        "coverage_plan": """
            EXPLAIN (FORMAT JSON, COSTS TRUE)
            SELECT p.symbol, l1.time AS l1_time, l2.time AS l2_time
              FROM pool_coins p
              LEFT JOIN LATERAL (
                SELECT i.time FROM indicators i
                 WHERE i.symbol = p.symbol AND i.market_type = 'spot'
                   AND i.timeframe = '1h' AND i.scheduler_group = 'structural'
                 ORDER BY i.time DESC LIMIT 1
              ) l1 ON TRUE
              LEFT JOIN LATERAL (
                SELECT i.time FROM indicators i
                 WHERE i.symbol = p.symbol AND i.market_type = 'spot'
                   AND i.timeframe = '15m' AND i.scheduler_group = 'structural'
                 ORDER BY i.time DESC LIMIT 1
              ) l2 ON TRUE
             WHERE p.is_active IS TRUE AND p.market_type = 'spot'
             ORDER BY p.symbol
        """,
    }
    try:
        async with connection.transaction(readonly=True):
            for name, sql in queries.items():
                try:
                    async with connection.transaction():
                        evidence[name] = await _rows(connection, sql)
                except Exception as exc:  # each evidence source remains independently visible
                    evidence[name] = {"error": f"{type(exc).__name__}:{exc}"}
    finally:
        await connection.close()
    print(json.dumps(evidence, ensure_ascii=False, indent=2, default=_json))


if __name__ == "__main__":
    asyncio.run(main())
