"""Fail-closed entrypoint for Spot MTF walk-forward calibration."""

from __future__ import annotations

import asyncio
import json

from sqlalchemy import text

from app.database import CeleryAsyncSessionLocal
from app.services.mtf_walk_forward import (
    MTFCalibrationConfigRequired,
    require_calibration_config,
)


async def main() -> int:
    async with CeleryAsyncSessionLocal() as db:
        row = (await db.execute(text("""
            SELECT config_json
              FROM config_profiles
             WHERE config_type = 'mtf_calibration' AND is_active IS TRUE
             ORDER BY updated_at DESC LIMIT 1
        """))).mappings().one_or_none()
        try:
            config = require_calibration_config(
                dict(row["config_json"] or {}) if row else {}
            )
        except MTFCalibrationConfigRequired as exc:
            print(json.dumps({
                "status": "CONFIG_REQUIRED",
                "reason": str(exc),
                "profiles_activation_mode": "DRAFT",
                "thresholds_emitted": False,
            }, sort_keys=True))
            return 2
        print(json.dumps({
            "status": "READY_FOR_DATASET_BUILD",
            "config": config,
            "thresholds_emitted": False,
        }, sort_keys=True, default=str))
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
