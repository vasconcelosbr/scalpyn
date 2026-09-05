"""Read-only diagnostics for Spot MTF point-in-time calibration coverage.

This script intentionally does not call ``run_calibration`` and never writes to
the database.  It reproduces the bounded population window used by the active
policy, then inventories whether governed 1h/15m indicator envelopes could
have been available at each historical decision timestamp.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
from datetime import date, datetime
from decimal import Decimal
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping
from uuid import UUID

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.mtf_walk_forward import require_calibration_config
from app.services.profile_runtime_config import canonical_hash


_REQUIRED_BY_TIMEFRAME = {
    "1h": {
        "adx", "atr_pct", "di_plus", "di_minus", "ema21", "ema50",
        "ema21_slope_pct", "ema50_slope_pct", "higher_highs_5",
        "higher_lows_5",
    },
    "15m": {
        "price", "atr", "ema21", "ema50", "vwap", "vwap_reclaim_bool",
        "bb_upper", "bb_lower", "di_plus", "di_minus", "higher_highs_5",
        "higher_lows_5", "adx", "volume_spike", "bb_width",
    },
}


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", required=True, type=UUID)
    parser.add_argument("--run-id", required=True, type=UUID)
    return parser.parse_args()


def _json(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (Decimal, UUID)):
        return str(value)
    if isinstance(value, asyncpg.Record):
        return {key: _json(item) for key, item in value.items()}
    if isinstance(value, Mapping):
        return {str(key): _json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json(item) for item in value]
    return value


def _envelope_state(payload: Any, required: set[str]) -> tuple[str, dict[str, Any]]:
    if not isinstance(payload, Mapping):
        return "SNAPSHOT_UNAVAILABLE", {}
    for feature in sorted(required):
        envelope = payload.get(feature)
        if not isinstance(envelope, Mapping):
            return "FEATURE_UNAVAILABLE", {"feature": feature}
        material = dict(envelope)
        expected_hash = material.pop("envelope_hash", None)
        if not expected_hash:
            return "FEATURE_HASH_MISSING", {
                "feature": feature,
                "capture_contract_version": material.get("capture_contract_version"),
                "producer_version": material.get("producer_version"),
                "source_timestamp": material.get("source_timestamp"),
            }
        if expected_hash != canonical_hash(material):
            return "FEATURE_HASH_MISMATCH", {
                "feature": feature,
                "capture_contract_version": material.get("capture_contract_version"),
                "producer_version": material.get("producer_version"),
                "source_timestamp": material.get("source_timestamp"),
            }
    first = dict(payload[sorted(required)[0]])
    return "HASHES_VALID", {
        "capture_contract_version": first.get("capture_contract_version"),
        "producer_version": first.get("producer_version"),
        "source_timestamp": first.get("source_timestamp"),
        "config_profile_id": first.get("config_profile_id"),
        "config_hash": first.get("config_hash"),
    }


async def main() -> None:
    args = _args()
    dsn = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_PUBLIC_URL_REQUIRED")
    connection = await asyncpg.connect(dsn=dsn, command_timeout=30)
    try:
        async with connection.transaction(readonly=True):
            policy_row = await connection.fetchrow(
                """
                SELECT id, config_json, updated_at
                  FROM config_profiles
                 WHERE user_id = $1 AND pool_id IS NULL
                   AND config_type = 'mtf_calibration' AND is_active IS TRUE
                 ORDER BY updated_at DESC, id
                """,
                args.user_id,
            )
            if policy_row is None:
                raise SystemExit("MTF_CALIBRATION_POLICY_NOT_FOUND")
            raw_policy = policy_row["config_json"] or {}
            if isinstance(raw_policy, str):
                raw_policy = json.loads(raw_policy)
            policy = require_calibration_config(dict(raw_policy))
            requested = (
                int(policy["train_window_rows"])
                + int(policy["test_window_rows"]) * int(policy["fold_count"])
                + int(policy["embargo_rows"]) * int(policy["fold_count"])
                + int(policy["validation_holdout_rows"])
            )
            run = await connection.fetchrow(
                """
                SELECT id, status, failure_reason, dataset_manifest, dataset_hash,
                       started_at, completed_at
                  FROM mtf_calibration_runs
                 WHERE id = $1 AND user_id = $2
                """,
                args.run_id,
                args.user_id,
            )
            if run is None:
                raise SystemExit("MTF_CALIBRATION_RUN_NOT_FOUND")

            population = await connection.fetch(
                """
                WITH population AS (
                  SELECT st.id, st.symbol, st.entry_timestamp AS decision_at
                    FROM shadow_trades st
                   WHERE st.user_id = $1
                     AND st.status = 'COMPLETED'
                     AND st.pnl_pct IS NOT NULL
                     AND st.entry_timestamp IS NOT NULL
                     AND st.exit_timestamp IS NOT NULL
                   ORDER BY st.entry_timestamp DESC, st.id DESC
                   LIMIT $2
                )
                SELECT p.*,
                       l1.time AS l1_computed_at,
                       l1.indicators_json AS l1_indicators,
                       l2.time AS l2_computed_at,
                       l2.indicators_json AS l2_indicators
                  FROM population p
                  LEFT JOIN LATERAL (
                    SELECT i.time, i.indicators_json
                      FROM indicators i
                     WHERE i.symbol = p.symbol AND i.market_type = 'spot'
                       AND i.timeframe = '1h' AND i.scheduler_group = 'structural'
                       AND i.time <= p.decision_at
                     ORDER BY i.time DESC LIMIT 1
                  ) l1 ON TRUE
                  LEFT JOIN LATERAL (
                    SELECT i.time, i.indicators_json
                      FROM indicators i
                     WHERE i.symbol = p.symbol AND i.market_type = 'spot'
                       AND i.timeframe = '15m' AND i.scheduler_group = 'structural'
                       AND i.time <= p.decision_at
                     ORDER BY i.time DESC LIMIT 1
                  ) l2 ON TRUE
                 ORDER BY p.decision_at, p.id
                """,
                args.user_id,
                requested,
            )

            inventory = await connection.fetch(
                """
                SELECT timeframe,
                       indicators_json->'adx'->>'capture_contract_version'
                           AS capture_contract_version,
                       indicators_json->'adx'->>'producer_version' AS producer_version,
                       count(*) AS snapshots,
                       count(*) FILTER (
                         WHERE indicators_json->'adx' ? 'envelope_hash'
                       ) AS snapshots_with_adx_hash,
                       min(time) AS first_computed_at,
                       max(time) AS last_computed_at
                  FROM indicators
                 WHERE market_type = 'spot' AND scheduler_group = 'structural'
                   AND timeframe IN ('1h', '15m')
                 GROUP BY timeframe, capture_contract_version, producer_version
                 ORDER BY timeframe, first_computed_at
                """
            )
            governed_starts = {
                row["timeframe"]: row["first_computed_at"]
                for row in inventory
                if row["capture_contract_version"] == "spot_mtf_closed_ohlcv_v2"
                and row["producer_version"] == "mtf_indicator_producer_v1"
            }
            common_governed_start = (
                max(governed_starts.values())
                if set(governed_starts) == {"1h", "15m"}
                else None
            )
            completed_after_governed_start = None
            if common_governed_start is not None:
                completed_after_governed_start = await connection.fetchrow(
                    """
                    SELECT count(*) AS completed_rows,
                           min(entry_timestamp) AS first_entry_at,
                           max(entry_timestamp) AS last_entry_at
                      FROM shadow_trades
                     WHERE user_id = $1 AND status = 'COMPLETED'
                       AND pnl_pct IS NOT NULL
                       AND entry_timestamp >= $2
                       AND exit_timestamp IS NOT NULL
                    """,
                    args.user_id,
                    common_governed_start,
                )

            safety = await connection.fetchrow(
                """
                SELECT
                  (SELECT count(*) FROM profiles WHERE user_id = $1) AS profile_count,
                  (SELECT count(*) FROM mtf_profile_activation_audits
                    WHERE user_id = $1) AS activation_audits,
                  (SELECT count(*) FROM config_profiles
                    WHERE user_id = $1 AND config_type = 'mtf_calibration'
                      AND is_active IS TRUE) AS active_mtf_policies,
                  (SELECT count(*) FROM mtf_calibration_runs
                    WHERE user_id = $1) AS mtf_runs
                """,
                args.user_id,
            )
            contract = await connection.fetchrow(
                """
                SELECT config_json->'scanner'->'multilayer_contract'->>'enabled'
                           AS enabled,
                       config_json->'scanner'->'multilayer_contract'->>'activation_mode'
                           AS activation_mode,
                       config_json->'scanner'->'multilayer_contract'->>'operational_effect'
                           AS operational_effect
                  FROM config_profiles
                 WHERE user_id = $1 AND config_type = 'spot_engine'
                   AND is_active IS TRUE
                 ORDER BY updated_at DESC, id
                 LIMIT 1
                """,
                args.user_id,
            )
            watchlists = await connection.fetch(
                """
                SELECT id, name, level, profile_id
                  FROM pipeline_watchlists
                 WHERE user_id = $1
                   AND id IN (
                     'fa46602b-3a1c-411e-a0dc-447ffc0ed302'::uuid,
                     'b8a370d5-7e98-4d33-807a-5dc5f10e1573'::uuid
                   )
                 ORDER BY level, id
                """,
                args.user_id,
            )

            state_counts: dict[str, Counter[str]] = {
                "1h": Counter(),
                "15m": Counter(),
            }
            state_ranges: dict[str, dict[str, list[datetime]]] = {
                "1h": defaultdict(list),
                "15m": defaultdict(list),
            }
            identity_counts: dict[str, Counter[str]] = {
                "1h": Counter(),
                "15m": Counter(),
            }
            for row in population:
                for timeframe in ("1h", "15m"):
                    raw_indicators = row[
                        f"l{1 if timeframe == '1h' else 2}_indicators"
                    ]
                    if isinstance(raw_indicators, str):
                        raw_indicators = json.loads(raw_indicators)
                    state, identity = _envelope_state(
                        raw_indicators,
                        _REQUIRED_BY_TIMEFRAME[timeframe],
                    )
                    state_counts[timeframe][state] += 1
                    state_ranges[timeframe][state].append(row["decision_at"])
                    identity_key = json.dumps(
                        {
                            key: identity.get(key)
                            for key in (
                                "feature", "capture_contract_version",
                                "producer_version", "config_profile_id", "config_hash",
                            )
                        },
                        sort_keys=True,
                        default=str,
                    )
                    identity_counts[timeframe][identity_key] += 1

            state_summary = {}
            for timeframe in ("1h", "15m"):
                state_summary[timeframe] = [
                    {
                        "state": state,
                        "rows": count,
                        "share": count / len(population) if population else None,
                        "first_decision_at": min(state_ranges[timeframe][state]),
                        "last_decision_at": max(state_ranges[timeframe][state]),
                    }
                    for state, count in state_counts[timeframe].most_common()
                ]

            output = {
                "captured_at": datetime.now().astimezone(),
                "read_only": True,
                "user_id": args.user_id,
                "policy": {
                    "config_profile_id": policy_row["id"],
                    "policy_version": policy["policy_version"],
                    "policy_hash": canonical_hash(policy),
                    "approval_status": policy.get("approval_status"),
                    "approved_at": policy.get("approved_at"),
                    "approved_by": policy.get("approved_by"),
                    "requested_rows": requested,
                    "source_identity": {
                        layer: policy["profile_templates"][layer]["source_identity"]
                        for layer in ("L1", "L2")
                    },
                },
                "run": {
                    **dict(run),
                    "dataset_manifest": (
                        json.loads(run["dataset_manifest"])
                        if isinstance(run["dataset_manifest"], str)
                        else run["dataset_manifest"]
                    ),
                },
                "population": {
                    "rows": len(population),
                    "first_decision_at": population[0]["decision_at"] if population else None,
                    "last_decision_at": population[-1]["decision_at"] if population else None,
                },
                "point_in_time_state": state_summary,
                "identity_breakdown": {
                    timeframe: [
                        {"identity": json.loads(identity), "rows": count}
                        for identity, count in identity_counts[timeframe].most_common()
                    ]
                    for timeframe in ("1h", "15m")
                },
                "indicator_inventory": [dict(row) for row in inventory],
                "fully_governed_decision_window": {
                    "starts_by_timeframe": governed_starts,
                    "common_start": common_governed_start,
                    "completed_shadow_rows": (
                        dict(completed_after_governed_start)
                        if completed_after_governed_start
                        else None
                    ),
                },
                "post_run_safety": {
                    **dict(safety),
                    "contract": dict(contract) if contract else None,
                    "watchlists": [dict(row) for row in watchlists],
                },
            }
            print(json.dumps(_json(output), ensure_ascii=False, indent=2))
    finally:
        await connection.close()


if __name__ == "__main__":
    asyncio.run(main())
