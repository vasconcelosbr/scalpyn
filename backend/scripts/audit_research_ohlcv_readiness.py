"""Read-only readiness and 5m-equivalence audit for research 15m/1h OHLCV."""

from __future__ import annotations

import argparse
import json
import os
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor


TIMEFRAMES: dict[str, dict[str, Any]] = {
    "15m": {"seconds": 900, "target": 2_000, "five_minute_rows": 3},
    "1h": {"seconds": 3_600, "target": 1_000, "five_minute_rows": 12},
}


READINESS_SQL = """
WITH active AS (
    SELECT DISTINCT symbol
      FROM pool_coins
     WHERE market_type = 'spot' AND is_active IS TRUE
), ranked AS (
    SELECT o.symbol, o.time,
           row_number() OVER (PARTITION BY o.symbol ORDER BY o.time DESC) AS rn
      FROM ohlcv o
      JOIN active a ON a.symbol = o.symbol
     WHERE o.exchange = 'gate.io'
       AND o.market_type = 'spot'
       AND o.timeframe = %(timeframe)s
), recent AS (
    SELECT symbol, time FROM ranked WHERE rn <= %(target)s
), ordered AS (
    SELECT symbol, time,
           lead(time) OVER (PARTITION BY symbol ORDER BY time) AS next_time
      FROM recent
), per_symbol AS (
    SELECT a.symbol,
           count(o.time)::integer AS rows,
           min(o.time) AS first_open,
           max(o.time) AS last_open,
           coalesce(sum(greatest(
               floor(extract(epoch FROM (o.next_time - o.time))
                     / %(seconds)s)::bigint - 1,
               0
           )) FILTER (WHERE o.next_time IS NOT NULL), 0)::bigint AS gaps
      FROM active a
      LEFT JOIN ordered o ON o.symbol = a.symbol
     GROUP BY a.symbol
)
SELECT count(*)::integer AS target_symbols,
       count(*) FILTER (WHERE rows > 0)::integer AS present_symbols,
       count(*) FILTER (WHERE rows >= %(target)s)::integer AS target_ready_symbols,
       count(*) FILTER (WHERE rows >= 200)::integer AS ema200_ready_symbols,
       coalesce(sum(gaps), 0)::bigint AS total_gap_candles,
       min(rows)::integer AS minimum_rows,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY rows) AS median_rows,
       max(rows)::integer AS maximum_rows,
       min(first_open) AS first_open,
       max(last_open) AS last_open,
       percentile_cont(0.5) WITHIN GROUP (
           ORDER BY extract(epoch FROM (
               clock_timestamp()
               - (last_open + make_interval(secs => %(seconds)s))
           ))
       ) FILTER (WHERE last_open IS NOT NULL) AS median_close_lag_seconds,
       percentile_cont(0.95) WITHIN GROUP (
           ORDER BY extract(epoch FROM (
               clock_timestamp()
               - (last_open + make_interval(secs => %(seconds)s))
           ))
       ) FILTER (WHERE last_open IS NOT NULL) AS p95_close_lag_seconds
  FROM per_symbol
"""


EQUIVALENCE_SQL = """
WITH active AS (
    SELECT DISTINCT symbol
      FROM pool_coins
     WHERE market_type = 'spot' AND is_active IS TRUE
), native_ranked AS (
    SELECT o.*,
           row_number() OVER (PARTITION BY o.symbol ORDER BY o.time DESC) AS rn
      FROM ohlcv o
      JOIN active a ON a.symbol = o.symbol
     WHERE o.exchange = 'gate.io'
       AND o.market_type = 'spot'
       AND o.timeframe = %(timeframe)s
       AND o.time + %(bucket)s::interval <= %(cutoff)s
), native AS (
    SELECT * FROM native_ranked WHERE rn <= %(sample_windows)s
), bounds AS (
    SELECT min(time) AS first_time, max(time) + %(bucket)s::interval AS last_time
      FROM native
), five AS (
    SELECT o.symbol,
           date_bin(%(bucket)s::interval, o.time,
                    timestamptz '1970-01-01 00:00:00+00') AS bucket,
           count(*)::integer AS rows_5m,
           (array_agg(o.open ORDER BY o.time))[1] AS open,
           max(o.high) AS high,
           min(o.low) AS low,
           (array_agg(o.close ORDER BY o.time DESC))[1] AS close
      FROM ohlcv o
      JOIN active a ON a.symbol = o.symbol
      CROSS JOIN bounds b
     WHERE o.exchange = 'gate.io'
       AND o.market_type = 'spot'
       AND o.timeframe = '5m'
       AND o.time >= b.first_time
       AND o.time < b.last_time
     GROUP BY o.symbol, bucket
), compared AS (
    SELECT n.symbol, n.time,
           f.rows_5m,
           n.open - f.open AS open_delta,
           n.high - f.high AS high_delta,
           n.low - f.low AS low_delta,
           n.close - f.close AS close_delta
      FROM native n
      LEFT JOIN five f ON f.symbol = n.symbol AND f.bucket = n.time
)
SELECT count(*)::integer AS native_windows,
       count(*) FILTER (WHERE rows_5m = %(expected_rows)s)::integer
           AS complete_5m_windows,
       count(*) FILTER (
           WHERE rows_5m = %(expected_rows)s
             AND open_delta = 0 AND high_delta = 0
             AND low_delta = 0 AND close_delta = 0
       )::integer AS exact_ohlc_matches,
       max(abs(open_delta)) FILTER (WHERE rows_5m = %(expected_rows)s)
           AS max_abs_open_delta,
       max(abs(high_delta)) FILTER (WHERE rows_5m = %(expected_rows)s)
           AS max_abs_high_delta,
       max(abs(low_delta)) FILTER (WHERE rows_5m = %(expected_rows)s)
           AS max_abs_low_delta,
       max(abs(close_delta)) FILTER (WHERE rows_5m = %(expected_rows)s)
           AS max_abs_close_delta
  FROM compared
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url-env", default="DATABASE_PUBLIC_URL")
    parser.add_argument("--sample-windows", type=int, default=20)
    args = parser.parse_args()
    if args.sample_windows <= 0:
        raise SystemExit("--sample-windows must be positive")
    database_url = os.getenv(args.database_url_env)
    if not database_url:
        raise SystemExit(f"missing environment variable {args.database_url_env}")

    output: dict[str, Any] = {"read_only": True, "timeframes": {}}
    with psycopg2.connect(database_url, connect_timeout=20) as conn:
        conn.set_session(readonly=True, autocommit=False)
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SET LOCAL statement_timeout = '120s'")
            cur.execute("SELECT clock_timestamp() AS cutoff")
            cutoff = cur.fetchone()["cutoff"]
            output["cutoff_utc"] = cutoff
            for timeframe, contract in TIMEFRAMES.items():
                readiness_params = {
                    "timeframe": timeframe,
                    "target": int(
                        os.getenv(
                            f"OHLCV_RESEARCH_TARGET_{timeframe.upper()}_CANDLES",
                            str(contract["target"]),
                        )
                    ),
                    "seconds": contract["seconds"],
                }
                cur.execute(READINESS_SQL, readiness_params)
                readiness = dict(cur.fetchone())
                cur.execute(
                    EQUIVALENCE_SQL,
                    {
                        "timeframe": timeframe,
                        "bucket": timeframe,
                        "cutoff": cutoff,
                        "sample_windows": args.sample_windows,
                        "expected_rows": contract["five_minute_rows"],
                    },
                )
                output["timeframes"][timeframe] = {
                    "target_candles": readiness_params["target"],
                    "readiness": readiness,
                    "five_minute_equivalence": dict(cur.fetchone()),
                }
        conn.rollback()

    print(json.dumps(output, default=str, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
