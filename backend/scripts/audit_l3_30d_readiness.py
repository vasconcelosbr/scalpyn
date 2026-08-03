"""Read-only production audit for the causal L3_PROFILE 30-day dataset."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import json
import os


REQUIRED_COLUMNS = ("feature_source_at", "feature_source_times")


def _parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cutoff", help="Frozen ISO-8601 cutoff; default DB clock")
    return parser.parse_args()


async def _audit(cutoff_text: str | None) -> dict:
    import asyncpg

    url = os.getenv("DATABASE_PUBLIC_URL") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("missing_database_url")
    conn = await asyncpg.connect(url, timeout=10)
    try:
        async with conn.transaction(readonly=True):
            await conn.execute("SET LOCAL statement_timeout = '20s'")
            cutoff = (
                datetime.fromisoformat(cutoff_text.replace("Z", "+00:00"))
                if cutoff_text
                else await conn.fetchval("SELECT clock_timestamp()")
            )
            if cutoff.tzinfo is None:
                cutoff = cutoff.replace(tzinfo=timezone.utc)
            cutoff = cutoff.astimezone(timezone.utc)

            version = await conn.fetchval("SELECT version_num FROM alembic_version")
            columns = {
                row["column_name"]
                for row in await conn.fetch(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'public' AND table_name = 'shadow_trades'
                    """
                )
            }
            missing = sorted(set(REQUIRED_COLUMNS) - columns)
            if missing:
                return {
                    "alembic_version": version,
                    "cutoff": cutoff.isoformat(),
                    "schema_ready": False,
                    "missing_columns": missing,
                }

            config = await conn.fetchval(
                """
                SELECT config_json
                FROM config_profiles
                WHERE config_type = 'ml' AND is_active IS TRUE
                ORDER BY updated_at DESC
                LIMIT 1
                """
            )
            if isinstance(config, str):
                config = json.loads(config)
            valid_from = datetime.fromisoformat(
                str(config["ml_l3_dataset_valid_from"]).replace("Z", "+00:00")
            )
            window_start = max(valid_from, cutoff - timedelta(days=30))
            row = await conn.fetchrow(
                """
                SELECT
                  COUNT(*)::int AS eligible_rows,
                  COUNT(DISTINCT symbol)::int AS assets,
                  COUNT(DISTINCT profile_id)::int AS profiles,
                  COUNT(*) FILTER (WHERE pnl_pct > 0)::int AS positive_rows,
                  COUNT(*) FILTER (WHERE pnl_pct <= 0)::int AS nonpositive_rows,
                  MIN(created_at) AS first_created_at,
                  MAX(created_at) AS last_created_at
                FROM shadow_trades
                WHERE source = 'L3'
                  AND created_at >= $1 AND created_at <= $2
                  AND COALESCE(label_resolved_at, completed_at) <= $2
                  AND outcome IN ('TP_HIT', 'SL_HIT', 'TIMEOUT')
                  AND pnl_pct IS NOT NULL
                  AND profile_id IS NOT NULL
                  AND lineage_status = 'EXACT'
                  AND eligible_for_training IS TRUE
                  AND capture_contract_version = 'point-in-time-v2'
                  AND feature_source_at IS NOT NULL
                  AND feature_source_times IS NOT NULL
                  AND feature_source_times <> '{}'::jsonb
                  AND features_captured_at IS NOT NULL
                  AND feature_source_at <= entry_timestamp
                  AND feature_source_at <= features_captured_at
                """,
                window_start,
                cutoff,
            )
            config_keys = (
                "ml_l3_training_contract_version",
                "ml_catboost_retrain_min_eligible_rows",
                "ml_catboost_train_size_ratio",
                "ml_catboost_validation_size_ratio",
                "ml_catboost_test_size_ratio",
                "ml_catboost_min_train_samples",
                "ml_catboost_min_validation_samples",
                "ml_catboost_min_test_samples",
                "ml_threshold_min_positives",
                "ml_optuna_max_trials",
                "ml_optuna_timeout_seconds",
                "ml_catboost_early_stopping_rounds",
                "ml_training_seed",
                "ml_catboost_base_params",
            )
            return {
                "alembic_version": version,
                "cutoff": cutoff.isoformat(),
                "window_start": window_start.isoformat(),
                "schema_ready": True,
                "config": {key: config.get(key) for key in config_keys},
                "dataset": {
                    key: value.isoformat() if isinstance(value, datetime) else value
                    for key, value in dict(row).items()
                },
            }
    finally:
        await conn.close()


def main() -> None:
    args = _parse_args()
    print(json.dumps(asyncio.run(_audit(args.cutoff)), indent=2, default=str))


if __name__ == "__main__":
    main()
