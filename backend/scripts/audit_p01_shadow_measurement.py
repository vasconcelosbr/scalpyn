"""Read-only evidence audit for the P0.1 Shadow measurement contract.

The script intentionally fails closed: it starts a read-only transaction and
prints literal JSON results without changing configuration or trade history.
"""

from __future__ import annotations

import json
import os
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor


KNOWN_SUI_TRADE_ID = "6f2c71fd-65db-4112-b274-35ed49aceb1b"


def _json(value: Any) -> str:
    return json.dumps(value, default=str, ensure_ascii=False, sort_keys=True)


def _query(cursor: RealDictCursor, name: str, sql: str, params: tuple[Any, ...] = ()) -> None:
    cursor.execute(sql, params)
    print(_json({"evidence": name, "rows": cursor.fetchall()}))


def main() -> None:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required")

    with psycopg2.connect(database_url, connect_timeout=15) as connection:
        connection.set_session(readonly=True, autocommit=False)
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("SET LOCAL statement_timeout = '45000ms'")
            cursor.execute("SET LOCAL lock_timeout = '5000ms'")

            _query(
                cursor,
                "schema",
                """
                SELECT current_database() AS database_name,
                       current_setting('transaction_read_only') AS transaction_read_only,
                       (SELECT version_num FROM alembic_version) AS alembic_version
                """,
            )
            _query(
                cursor,
                "ml_config",
                """
                SELECT id, config_type, is_active, updated_at,
                       config_json->'shadow_measurement_timeframe_priority' AS timeframe_priority,
                       config_json->'shadow_entry_max_lag_seconds' AS max_lag_seconds
                  FROM config_profiles
                 WHERE config_type = 'ml' AND is_active IS TRUE
                 ORDER BY updated_at DESC, id
                """,
            )
            _query(
                cursor,
                "measurement_columns",
                """
                SELECT column_name, data_type, is_nullable
                  FROM information_schema.columns
                 WHERE table_schema = 'public'
                   AND table_name = 'shadow_trade_measurement_revisions'
                 ORDER BY ordinal_position
                """,
            )
            _query(
                cursor,
                "shadow_exit_columns",
                """
                SELECT column_name, data_type, is_nullable
                  FROM information_schema.columns
                 WHERE table_schema = 'public'
                   AND table_name = 'shadow_trades'
                   AND column_name IN (
                       'exit_price', 'exit_price_nominal', 'barrier_overshoot_pct',
                       'mfe_mae_source', 'mfe_mae_recomputed_at', 'mfe_mae_method_version'
                   )
                 ORDER BY ordinal_position
                """,
            )
            _query(
                cursor,
                "measurement_status",
                """
                SELECT status, unavailable_reason, source, timeframe, COUNT(*) AS n
                  FROM shadow_trade_measurement_revisions
                 GROUP BY status, unavailable_reason, source, timeframe
                 ORDER BY n DESC, status, unavailable_reason
                """,
            )
            _query(
                cursor,
                "entry_lag_distribution",
                """
                WITH observed AS (
                    SELECT NULLIF(config_snapshot->>'entry_price_lag_seconds', '')::double precision AS lag_seconds
                      FROM shadow_trades
                     WHERE config_snapshot->>'entry_price_mode' = 'DECISION_ENVELOPE'
                       AND entry_timestamp >= TIMESTAMPTZ '2026-08-27 16:54:00+00'
                )
                SELECT COUNT(lag_seconds) AS n,
                       MIN(lag_seconds) AS min_seconds,
                       percentile_cont(0.50) WITHIN GROUP (ORDER BY lag_seconds) AS p50_seconds,
                       percentile_cont(0.90) WITHIN GROUP (ORDER BY lag_seconds) AS p90_seconds,
                       percentile_cont(0.95) WITHIN GROUP (ORDER BY lag_seconds) AS p95_seconds,
                       percentile_cont(0.99) WITHIN GROUP (ORDER BY lag_seconds) AS p99_seconds,
                       MAX(lag_seconds) AS max_seconds
                  FROM observed
                """,
            )
            _query(
                cursor,
                "ohlcv_retention",
                """
                SELECT timeframe, MIN(time) AS first_at, MAX(time) AS last_at,
                       COUNT(*) AS rows, COUNT(DISTINCT symbol) AS symbols
                  FROM ohlcv
                 WHERE timeframe IN ('1m', '5m', '15m', '1h')
                 GROUP BY timeframe
                 ORDER BY timeframe
                """,
            )
            _query(
                cursor,
                "recent_measurement_coverage",
                """
                WITH completed AS (
                    SELECT id, lineage_status, eligible_for_training
                      FROM shadow_trades
                     WHERE status = 'COMPLETED'
                       AND strategy_type = 'PROFILE_L3'
                       AND source = 'L3'
                       AND exit_timestamp >= TIMESTAMPTZ '2026-08-24 00:00:00+00'
                ), latest AS (
                    SELECT DISTINCT ON (shadow_trade_id)
                           shadow_trade_id, status, entry_quality, source, timeframe
                      FROM shadow_trade_measurement_revisions
                     ORDER BY shadow_trade_id, created_at DESC, id DESC
                )
                SELECT COALESCE(latest.status, 'MISSING') AS measurement_status,
                       COALESCE(latest.entry_quality, 'MISSING') AS entry_quality,
                       completed.lineage_status,
                       completed.eligible_for_training,
                       COUNT(*) AS n
                  FROM completed
                  LEFT JOIN latest ON latest.shadow_trade_id = completed.id
                 GROUP BY 1, 2, 3, 4
                 ORDER BY n DESC
                """,
            )
            _query(
                cursor,
                "known_sui_trade",
                """
                SELECT id, symbol, status, outcome, entry_price, entry_timestamp,
                       exit_price, exit_timestamp, tp_price, sl_price,
                       mae_pct, mfe_pct, mae_at, mfe_at,
                       config_snapshot->>'entry_price_mode' AS entry_price_mode,
                       config_snapshot->>'entry_price_lag_seconds' AS entry_price_lag_seconds
                  FROM shadow_trades
                 WHERE id = %s::uuid
                """,
                (KNOWN_SUI_TRADE_ID,),
            )
            _query(
                cursor,
                "known_sui_measurements",
                """
                SELECT id, measurement_contract_version, status, source, timeframe,
                       legacy_mae_pct, legacy_mfe_pct, mae_pct, mfe_pct,
                       mae_at, mfe_at, gross_return_pct, entry_quality,
                       unavailable_reason, created_at
                  FROM shadow_trade_measurement_revisions
                 WHERE shadow_trade_id = %s::uuid
                 ORDER BY created_at, id
                """,
                (KNOWN_SUI_TRADE_ID,),
            )

        connection.rollback()


if __name__ == "__main__":
    main()
