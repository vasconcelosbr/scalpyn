"""Validate/apply the P0.1 measurement controls through the canonical service.

Dry-run is the default.  This script never writes ``config_profiles`` directly;
the aggregate service preserves unrelated ML keys, writes audit history and
invalidates configuration caches after commit.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any
from uuid import UUID

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.dirname(_HERE)
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from sqlalchemy import select  # noqa: E402


def _json(value: Any) -> str:
    return json.dumps(value, default=str, sort_keys=True, separators=(",", ":"))


async def run(args: argparse.Namespace) -> dict[str, Any]:
    from app.database import AsyncSessionLocal
    from app.models.config_profile import ConfigProfile
    from app.services.strategy_settings_service import strategy_settings_service

    async with AsyncSessionLocal() as db:
        query = select(ConfigProfile.user_id).where(
            ConfigProfile.config_type == "ml",
            ConfigProfile.is_active.is_(True),
        )
        if args.user_id:
            query = query.where(ConfigProfile.user_id == UUID(args.user_id))
        user_ids = list(dict.fromkeys((await db.execute(query)).scalars().all()))
        if len(user_ids) != 1:
            raise RuntimeError(
                f"expected_exactly_one_active_ml_owner:found={len(user_ids)};pass=--user-id"
            )
        user_id = user_ids[0]
        current = await strategy_settings_service.get_config(db, user_id)
        payload = {
            "ml_shadow": {
                "shadow_measurement_timeframe_priority": args.timeframes,
                "shadow_entry_max_lag_seconds": args.max_entry_lag_seconds,
            }
        }
        validated = await strategy_settings_service.validate_import(db, user_id, payload)
        result: dict[str, Any] = {
            "mode": "APPLY" if args.apply else "DRY_RUN",
            "user_id": str(user_id),
            "source_hash_before": current["config"]["source_hash"],
            "requested": payload["ml_shadow"],
            "diff": validated["diff"],
        }
        if args.apply:
            applied = await strategy_settings_service.apply(
                db,
                user_id,
                payload=payload,
                source_hash=current["config"]["source_hash"],
                change_description=(
                    "P0.1 activate Shadow measurement; lag threshold calibrated "
                    "from the audited recent entry-price distribution"
                ),
                source="P0.1_SHADOW_MEASUREMENT",
            )
            result.update(
                {
                    "changed_config_types": applied["changed_config_types"],
                    "source_hash_after": applied["config"]["source_hash"],
                    "readback": {
                        key: applied["config"]["ml_shadow"].get(key)
                        for key in (
                            "shadow_measurement_timeframe_priority",
                            "shadow_entry_max_lag_seconds",
                        )
                    },
                }
            )
        return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id")
    parser.add_argument(
        "--timeframes",
        nargs="+",
        required=True,
        choices=["1m", "5m", "15m", "1h"],
    )
    parser.add_argument("--max-entry-lag-seconds", type=int, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.max_entry_lag_seconds < 0:
        parser.error("--max-entry-lag-seconds must be non-negative")
    return args


if __name__ == "__main__":
    print(_json(asyncio.run(run(parse_args()))))
