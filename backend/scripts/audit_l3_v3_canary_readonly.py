"""Read-only evidence collector for the L3 v3 provenance canary.

The script never changes production state.  It emits literal JSON suitable for
the release evidence ledger and intentionally avoids printing environment
variables or connection strings.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections import Counter
from typing import Any, Mapping

from sqlalchemy import text

# ``railway run`` executes locally and injects both URLs.  Prefer the public
# endpoint there; inside Railway only DATABASE_URL exists and remains in use.
if os.environ.get("DATABASE_PUBLIC_URL"):
    os.environ["DATABASE_URL"] = os.environ["DATABASE_PUBLIC_URL"]

from app.database import CeleryAsyncSessionLocal
from app.services.profile_runtime_config import canonical_profile_config_hash


CANARY_PROFILE_ID = "4aa864f7-8c96-42f0-ab58-795bcbad1b9a"
KNOWN_DECISION_ID = 193639


def _conditions(config: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    source = dict(config or {})
    rows: list[dict[str, Any]] = []
    for section in ("filters", "signals", "entry_triggers"):
        for index, condition in enumerate(
            ((source.get(section) or {}).get("conditions") or [])
        ):
            if isinstance(condition, Mapping):
                rows.append({
                    "path": f"{section}.conditions[{index}]",
                    **dict(condition),
                })
    for block_index, block in enumerate(
        ((source.get("block_rules") or {}).get("blocks") or [])
    ):
        if not isinstance(block, Mapping):
            continue
        block_conditions = block.get("conditions")
        if block_conditions is None:
            block_conditions = [block]
        for index, condition in enumerate(block_conditions or []):
            if isinstance(condition, Mapping):
                rows.append({
                    "path": (
                        f"block_rules.blocks[{block_index}].conditions[{index}]"
                    ),
                    **dict(condition),
                })
    return rows


def _profile_row(row: Mapping[str, Any]) -> dict[str, Any]:
    config = row.get("config") or {}
    conditions = _conditions(config)
    return {
        "profile_id": str(row["profile_id"]),
        "profile_name": row["profile_name"],
        "profile_version": row.get("profile_version"),
        "profile_version_id": (
            str(row["profile_version_id"])
            if row.get("profile_version_id") else None
        ),
        "profile_config_hash": canonical_profile_config_hash(config),
        "version_config_hash": row.get("version_config_hash"),
        "profile_version_hash_match": (
            canonical_profile_config_hash(config) == row.get("version_config_hash")
        ),
        "condition_count": len(conditions),
        "comparison_count": sum(
            condition.get("type") == "comparison" for condition in conditions
        ),
        "missing_source_count": sum(
            not condition.get("source") for condition in conditions
        ),
        "conditions": conditions,
        "config": config if str(row["profile_id"]) == CANARY_PROFILE_ID else None,
    }


def _contract_summary(metrics: Mapping[str, Any] | None) -> dict[str, Any]:
    source = dict(metrics or {})
    gate = source.get("l3_gate_v2") or {}
    contract = source.get("l3_authorization_contract_v3") or {}
    registry = contract.get("feature_registry") or []
    providers = Counter(
        (
            str(candidate.get("source") or "NONE"),
            str(candidate.get("source_provider") or "NONE"),
        )
        for candidate in registry
        if isinstance(candidate, Mapping)
    )
    return {
        "gate_v2": gate,
        "v3": {
            "authorization_status": contract.get("authorization_status"),
            "valid": contract.get("valid"),
            "contract_technical_decision": contract.get(
                "contract_technical_decision"
            ),
            "reason_codes": contract.get("reason_codes") or [],
            "runtime_validation_errors": contract.get(
                "runtime_validation_errors"
            ) or [],
            "profile_lineage": contract.get("profile_lineage"),
            "provenance_resolution": contract.get("provenance_resolution"),
            "sections": contract.get("sections"),
        },
        "feature_registry": registry,
        "feature_provider_counts": [
            {"source": source, "provider": provider, "count": count}
            for (source, provider), count in sorted(providers.items())
        ],
    }


async def collect() -> dict[str, Any]:
    async with CeleryAsyncSessionLocal() as db:
        await db.execute(text("SET TRANSACTION READ ONLY"))
        profile_rows = (
            await db.execute(text("""
                SELECT DISTINCT ON (p.id)
                       p.id AS profile_id,
                       p.name AS profile_name,
                       p.profile_version,
                       p.config,
                       pv.id AS profile_version_id,
                       pv.config_hash AS version_config_hash
                  FROM profiles p
                  JOIN pipeline_watchlists w
                    ON w.profile_id = p.id
                   AND upper(w.level) = 'L3'
                   AND w.auto_refresh IS TRUE
                  LEFT JOIN LATERAL (
                      SELECT id, config_hash
                        FROM profile_versions
                       WHERE profile_id = p.id
                         AND is_active IS TRUE
                         AND status = 'CHAMPION'
                       ORDER BY version_number DESC, created_at DESC
                       LIMIT 1
                  ) pv ON TRUE
                 WHERE p.is_active IS TRUE
                 ORDER BY p.id
            """))
        ).mappings().all()
        profiles = [_profile_row(row) for row in profile_rows]

        decision = (
            await db.execute(
                text("""
                    SELECT id, symbol, decision, profile_id, profile_name,
                           profile_version, user_id, created_at, metrics
                      FROM decisions_log
                     WHERE id = :decision_id
                """),
                {"decision_id": KNOWN_DECISION_ID},
            )
        ).mappings().one_or_none()

        user_id = decision.get("user_id") if decision else None
        configs = []
        if user_id:
            configs = list((await db.execute(
                text("""
                    SELECT DISTINCT ON (config_type)
                           config_type, config_json, updated_at
                      FROM config_profiles
                     WHERE user_id = :user_id
                       AND pool_id IS NULL
                       AND is_active IS TRUE
                       AND config_type IN (
                           'spot_engine', 'pipeline', 'indicators', 'block'
                       )
                     ORDER BY config_type, updated_at DESC, created_at DESC
                """),
                {"user_id": user_id},
            )).mappings().all())

        historical = (
            await db.execute(text("""
                SELECT
                    COUNT(*) AS v3_rows,
                    COUNT(*) FILTER (
                        WHERE decision = 'BLOCK'
                          AND COALESCE(
                              (metrics->'l3_gate_v2'->'signals'->>'gate_passed')::boolean,
                              false
                          ) IS FALSE
                    ) AS signal_block_rows,
                    COUNT(*) FILTER (
                        WHERE metrics->'l3_authorization_contract_v3'
                              ->>'authorization_status' = 'CONTRACT_REJECT'
                    ) AS contract_reject_rows,
                    MIN(id) AS first_decision_id,
                    MAX(id) AS last_decision_id,
                    MIN(created_at) AS first_created_at,
                    MAX(created_at) AS last_created_at
                  FROM decisions_log
                 WHERE id <= :decision_id
                   AND metrics ? 'l3_authorization_contract_v3'
            """), {"decision_id": KNOWN_DECISION_ID})
        ).mappings().one()

        outbox = list((await db.execute(text("""
            SELECT id, event_type, status, attempt_count, last_error,
                   payload, created_at, processed_at
              FROM l3_authorization_outbox
             WHERE decision_id = :decision_id
             ORDER BY created_at
        """), {"decision_id": KNOWN_DECISION_ID})).mappings().all())

        active_zec = list((await db.execute(text("""
            SELECT id, decision_id, symbol, direction, status, source,
                   profile_id, profile_name, entry_timestamp
              FROM shadow_trades
             WHERE symbol = 'ZEC_USDT'
               AND source = 'L3'
               AND status IN ('PENDING', 'RUNNING')
             ORDER BY entry_timestamp DESC
        """))).mappings().all())

        latest_by_profile = list((await db.execute(text("""
            SELECT DISTINCT ON (d.profile_id)
                   d.profile_id, d.id AS decision_id, d.symbol, d.decision,
                   d.created_at,
                   d.metrics->'l3_gate_v2'->>'shadow_decision' AS gate_v2_decision,
                   d.metrics->'l3_authorization_contract_v3'
                       ->>'authorization_status' AS v3_status
              FROM decisions_log d
             WHERE d.profile_id IN (
                 SELECT DISTINCT p.id
                   FROM profiles p
                   JOIN pipeline_watchlists w ON w.profile_id = p.id
                  WHERE p.is_active IS TRUE
                    AND upper(w.level) = 'L3'
                    AND w.auto_refresh IS TRUE
             )
               AND d.metrics ? 'l3_gate_v2'
             ORDER BY d.profile_id, d.created_at DESC, d.id DESC
        """))).mappings().all())

        await db.rollback()

    return {
        "contract": "l3_v3_canary_readonly_audit_v1",
        "canary_profile_id": CANARY_PROFILE_ID,
        "known_decision_id": KNOWN_DECISION_ID,
        "active_profile_count": len(profiles),
        "profiles": profiles,
        "decision": (
            {
                **{key: value for key, value in decision.items() if key != "metrics"},
                **_contract_summary(decision.get("metrics")),
            }
            if decision else None
        ),
        "configs": [dict(row) for row in configs],
        "historical_through_known_decision": dict(historical),
        "known_decision_outbox": [dict(row) for row in outbox],
        "active_zec_l3_shadows": [dict(row) for row in active_zec],
        "latest_decision_by_active_profile": [
            dict(row) for row in latest_by_profile
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()
    print(json.dumps(
        asyncio.run(collect()),
        ensure_ascii=False,
        indent=None if args.compact else 2,
        sort_keys=True,
        default=str,
    ))


if __name__ == "__main__":
    main()
