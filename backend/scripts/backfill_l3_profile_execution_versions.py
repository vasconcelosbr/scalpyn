"""Idempotently repair immutable execution versions for active L3 profiles.

The default mode is read-only. Pass ``--apply`` only after reviewing the JSON
report and while ``L3_PROFILE_CONTRACT_OPERATIONAL`` remains disabled.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any
from uuid import UUID

from sqlalchemy import bindparam, select, text

from app.database import AsyncSessionLocal, engine
from app.models.profile import Profile
from app.services.profile_execution_contract import (
    load_profile_execution_snapshots,
)
from app.services.profile_config_validation import validate_profile_config
from app.services.profile_versioning_v2 import ensure_current_profile_version


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Create/reuse exact immutable versions. Omit for read-only dry-run.",
    )
    parser.add_argument(
        "--profile-id",
        action="append",
        default=[],
        help="Optional profile UUID. Repeat to restrict the run.",
    )
    return parser.parse_args()


def summarize_contracts(
    snapshots: dict[UUID, dict[str, Any]],
    *,
    mode: str,
    validation_errors: dict[UUID, str] | None = None,
) -> dict[str, Any]:
    validation_errors = validation_errors or {}
    items = []
    for profile_id in sorted(snapshots, key=str):
        contract = snapshots[profile_id]["contract"]
        reason_codes = list(contract["reason_codes"])
        if profile_id in validation_errors:
            reason_codes.append("PROFILE_CONFIG_VALIDATION_FAILED")
        status = "MISMATCH" if reason_codes else contract["status"]
        items.append(
            {
                "profile_id": str(profile_id),
                "profile_name": snapshots[profile_id]["name"],
                "profile_version_id": contract["profile_version_id"],
                "profile_projection_hash": contract["profile_projection_hash"],
                "version_config_hash": contract["version_config_hash"],
                "status": status,
                "reason_codes": reason_codes,
                "validation_error": validation_errors.get(profile_id),
            }
        )
    mismatches = sum(item["status"] != "MATCH" for item in items)
    return {
        "mode": mode,
        "status": "MATCH" if mismatches == 0 else "MISMATCH",
        "profiles_checked": len(items),
        "mismatches": mismatches,
        "items": items,
    }


async def _active_l3_profile_ids(
    db: Any, requested_ids: list[UUID]
) -> list[UUID]:
    sql = """
        SELECT DISTINCT p.id
          FROM profiles p
          JOIN pipeline_watchlists pw ON pw.profile_id = p.id
         WHERE p.is_active IS TRUE
           AND pw.auto_refresh IS TRUE
           AND UPPER(pw.level) = 'L3'
    """
    params: dict[str, Any] = {}
    if requested_ids:
        sql += " AND p.id IN :requested_ids"
        params["requested_ids"] = requested_ids
    sql += " ORDER BY p.id"
    statement = text(sql)
    if requested_ids:
        statement = statement.bindparams(bindparam("requested_ids", expanding=True))
    return list((await db.execute(statement, params)).scalars().all())


async def run(*, apply: bool, requested_ids: list[UUID]) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        profile_ids = await _active_l3_profile_ids(db, requested_ids)
        before = await load_profile_execution_snapshots(db, profile_ids)
        validation_errors: dict[UUID, str] = {}
        validated_configs: dict[UUID, dict[str, Any]] = {}
        for profile_id, metadata in before.items():
            try:
                validate_profile_config(
                    metadata["config"], require_feature_identity=False
                )
                # The backfill snapshots the current JSON byte-for-byte in
                # semantic content; validation must not rewrite it.
                validated_configs[profile_id] = dict(metadata["config"])
            except Exception as exc:
                validation_errors[profile_id] = f"{type(exc).__name__}: {exc}"
        report = {
            "before": summarize_contracts(
                before,
                mode="dry-run",
                validation_errors=validation_errors,
            )
        }
        if not apply:
            await db.rollback()
            return report
        if validation_errors:
            await db.rollback()
            report["apply_blocked"] = "PROFILE_CONFIG_VALIDATION_FAILED"
            return report

        profiles = list(
            (
                await db.execute(
                    select(Profile)
                    .where(Profile.id.in_(profile_ids))
                    .order_by(Profile.id)
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )
        mutations = []
        for profile in profiles:
            contract = before[profile.id]["contract"]
            if contract["contract_valid"]:
                mutations.append(
                    {
                        "profile_id": str(profile.id),
                        "action": "unchanged",
                        "profile_version_id": contract["profile_version_id"],
                    }
                )
                continue
            version_id, _, created = await ensure_current_profile_version(
                db,
                profile_id=profile.id,
                config=validated_configs[profile.id],
                is_shadow_only=bool(profile.is_shadow_only),
            )
            mutations.append(
                {
                    "profile_id": str(profile.id),
                    "action": "created" if created else "reused",
                    "profile_version_id": str(version_id),
                }
            )
        await db.commit()

    async with AsyncSessionLocal() as verification_db:
        after = await load_profile_execution_snapshots(
            verification_db, profile_ids
        )
        report["mutations"] = mutations
        report["after"] = summarize_contracts(after, mode="verification")
        await verification_db.rollback()
    return report


async def main() -> None:
    args = _args()
    requested_ids = [UUID(raw) for raw in args.profile_id]
    try:
        report = await run(apply=args.apply, requested_ids=requested_ids)
        print(json.dumps(report, sort_keys=True, default=str))
        final = report.get("after") or report["before"]
        if args.apply and final["status"] != "MATCH":
            raise SystemExit(1)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
