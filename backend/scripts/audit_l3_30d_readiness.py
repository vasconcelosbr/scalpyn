"""Read-only production audit for the causal L3_PROFILE 30-day dataset."""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
import os


REQUIRED_COLUMNS = ("feature_source_at", "feature_source_times")


def _decode_jsonb(value):
    """Normalize asyncpg's text JSONB codec without weakening fail-closed checks."""
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


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
            maturity_margin = int(config["ml_maturity_embargo_margin_minutes"])
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
                  AND created_at <= $2 - make_interval(
                        mins => COALESCE(ttt_timeout_minutes, 0) + $3
                      )
                """,
                window_start,
                cutoff,
                maturity_margin,
            )
            historical_dataset = {
                "enabled": False,
                "queried_rows": 0,
                "included_rows": 0,
                "excluded_rows": 0,
                "exclusion_reasons": {},
                "neutralized_feature_rows": {},
                "shadow_mutations": 0,
            }
            if config.get("ml_l3_historical_lineage_enabled") is True:
                from app.ml.feature_extractor import FEATURE_COLUMNS
                from app.ml.historical_l3_lineage import (
                    resolve_historical_l3_record,
                )

                historical_rows = await conn.fetch(
                    """
                    SELECT
                      st.id::text AS shadow_id,
                      st.symbol, st.source, st.pnl_pct, st.net_return_pct,
                      st.holding_seconds, st.outcome, st.features_snapshot,
                      st.config_snapshot, st.barrier_mode,
                      st.barrier_contract_version, st.tp_pct_applied,
                      st.sl_pct_applied, st.entry_timestamp, st.created_at,
                      st.feature_source_at, st.feature_source_times,
                      st.features_captured_at, st.capture_contract_version,
                      st.timeframe, st.exchange, st.profile_id::text AS profile_id,
                      st.event_id::text AS event_id,
                      st.snapshot_id::text AS snapshot_id,
                      st.profile_version_id::text AS profile_version_id,
                      st.score_engine_version_id::text AS score_engine_version_id,
                      st.label_resolved_at, st.completed_at,
                      st.ttt_timeout_minutes, st.exit_timestamp,
                      st.barrier_touched_at,
                      dl.created_at AS decision_created_at,
                      dl.metrics->'indicators_snapshot' AS decision_indicator_snapshot,
                      CASE WHEN st.outcome IN ('TP_HIT', 'SL_HIT')
                           THEN st.barrier_touched_at
                           WHEN st.outcome = 'TIMEOUT' THEN st.exit_timestamp
                           ELSE NULL END AS historical_label_event_at
                    FROM shadow_trades st
                    JOIN decisions_log dl ON dl.id = st.decision_id
                    WHERE st.source = 'L3'
                      AND st.capture_contract_version = ANY($3::text[])
                      AND st.outcome IN ('TP_HIT', 'SL_HIT', 'TIMEOUT')
                      AND st.pnl_pct IS NOT NULL
                      AND st.features_snapshot IS NOT NULL
                      AND st.features_snapshot <> '{}'::jsonb
                      AND st.feature_extractor_version IS NOT NULL
                      AND st.feature_schema_version IS NOT NULL
                      AND st.feature_hash IS NOT NULL
                      AND st.lineage_status = 'EXACT'
                      AND st.eligible_for_training IS TRUE
                      AND dl.created_at >= $1 AND dl.created_at <= $2
                      AND COALESCE(st.label_resolved_at, st.completed_at) <= $2
                      AND dl.created_at <= $2 - make_interval(
                            mins => COALESCE(st.ttt_timeout_minutes, 0) + $4
                          )
                      AND CASE WHEN st.outcome IN ('TP_HIT', 'SL_HIT')
                               THEN st.barrier_touched_at
                               WHEN st.outcome = 'TIMEOUT' THEN st.exit_timestamp
                               ELSE NULL END > dl.created_at
                    ORDER BY dl.created_at
                    """,
                    window_start,
                    cutoff,
                    config["ml_l3_historical_capture_contracts"],
                    maturity_margin,
                )
                exclusions: Counter[str] = Counter()
                neutralized: Counter[str] = Counter()
                included = 0
                for historical_row in historical_rows:
                    raw_record = dict(historical_row)
                    for jsonb_field in (
                        "features_snapshot",
                        "config_snapshot",
                        "feature_source_times",
                        "decision_indicator_snapshot",
                    ):
                        raw_record[jsonb_field] = _decode_jsonb(
                            raw_record.get(jsonb_field)
                        )
                    resolved = resolve_historical_l3_record(
                        raw_record,
                        model_feature_columns=FEATURE_COLUMNS,
                        contract_version=config[
                            "ml_l3_historical_lineage_contract_version"
                        ],
                        configured_neutralized_features=config[
                            "ml_l3_historical_neutralized_features"
                        ],
                        untrusted_source_groups=config[
                            "ml_l3_historical_untrusted_source_groups"
                        ],
                    )
                    if resolved.record is None:
                        exclusions[resolved.exclusion_reason or "unknown"] += 1
                    else:
                        included += 1
                        neutralized.update(resolved.neutralized_features)
                historical_dataset = {
                    "enabled": True,
                    "contract_version": config[
                        "ml_l3_historical_lineage_contract_version"
                    ],
                    "queried_rows": len(historical_rows),
                    "included_rows": included,
                    "excluded_rows": len(historical_rows) - included,
                    "exclusion_reasons": dict(sorted(exclusions.items())),
                    "neutralized_feature_rows": dict(sorted(neutralized.items())),
                    "shadow_mutations": 0,
                }
            model_row = await conn.fetchrow(
                """
                SELECT
                  COUNT(*)::int AS catboost_models_created_since_cutoff,
                  COUNT(*) FILTER (WHERE execution_authority IS TRUE)::int
                    AS execution_authority_true_since_cutoff,
                  MAX(version)::int AS latest_catboost_version,
                  MAX(created_at) AS latest_catboost_created_at
                FROM ml_models
                WHERE model_lane = 'L3_PROFILE'
                  AND created_at >= $1
                """,
                cutoff,
            )
            comparison_rows = await conn.fetch(
                """
                SELECT version, status, model_lane,
                       train_samples, val_samples, test_samples,
                       NULLIF(metrics_json->'test'->>'roc_auc', '')::double precision
                         AS test_roc_auc,
                       NULLIF(metrics_json->'test'->>'net_ev', '')::double precision
                         AS test_net_ev,
                       NULLIF(metrics_json->'test'->>'fpr', '')::double precision
                         AS test_fpr
                FROM ml_models
                WHERE version::text = ANY($1::text[])
                ORDER BY version DESC
                """,
                ["95", "89", "88"],
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
                "ml_l3_historical_lineage_enabled",
                "ml_l3_historical_lineage_contract_version",
                "ml_l3_historical_capture_contracts",
                "ml_l3_historical_timestamp_aliases",
                "ml_l3_historical_untrusted_source_groups",
                "ml_l3_historical_neutralized_features",
                "ml_l3_historical_unresolved_feature_policy",
                "ml_l3_historical_label_anchor",
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
                "historical_dataset": historical_dataset,
                "total_resolved_causal_rows": (
                    int(row["eligible_rows"])
                    + int(historical_dataset["included_rows"])
                ),
                "model_writes_after_cutoff": {
                    key: value.isoformat() if isinstance(value, datetime) else value
                    for key, value in dict(model_row).items()
                },
                "comparison_models": [dict(item) for item in comparison_rows],
            }
    finally:
        await conn.close()


def main() -> None:
    args = _parse_args()
    print(json.dumps(asyncio.run(_audit(args.cutoff)), indent=2, default=str))


if __name__ == "__main__":
    main()
