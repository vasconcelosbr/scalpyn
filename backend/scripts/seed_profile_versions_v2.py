"""Seed exact current profile/score versions for ML lineage v2.

This is intentionally forward-only: it snapshots active profiles as they exist
now and never assigns invented versions to historical shadow trades.
"""

from __future__ import annotations

import asyncio
import json

from sqlalchemy import text

from app.database import AsyncSessionLocal, engine
from app.services.profile_versioning_v2 import ensure_current_profile_version


async def main() -> None:
    result = {
        "profiles_seen": 0,
        "profile_versions_created": 0,
        "profile_versions_reused": 0,
        "errors": [],
    }
    async with AsyncSessionLocal() as db:
        profiles = (await db.execute(text("""
            SELECT id, config, is_shadow_only
              FROM profiles
             WHERE is_active IS TRUE
             ORDER BY id
        """))).mappings().all()
        result["profiles_seen"] = len(profiles)
        for profile in profiles:
            try:
                async with db.begin_nested():
                    _, _, created = await ensure_current_profile_version(
                        db,
                        profile_id=profile["id"],
                        config=profile["config"] or {},
                        is_shadow_only=bool(profile["is_shadow_only"]),
                    )
                key = (
                    "profile_versions_created"
                    if created
                    else "profile_versions_reused"
                )
                result[key] += 1
            except Exception as exc:
                result["errors"].append({
                    "profile_id": str(profile["id"]),
                    "error": f"{type(exc).__name__}: {exc}",
                })
        if result["errors"]:
            await db.rollback()
        else:
            await db.commit()
    await engine.dispose()
    print(json.dumps(result, sort_keys=True, default=str))
    if result["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
