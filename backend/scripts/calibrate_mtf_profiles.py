"""Execute and export one server-authoritative Spot MTF calibration run."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from uuid import UUID

from sqlalchemy import text

from app.database import CeleryAsyncSessionLocal
from app.services.mtf_calibration_service import run_calibration


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", type=UUID)
    parser.add_argument("--output-dir", type=Path)
    return parser.parse_args()


async def main() -> int:
    args = _args()
    async with CeleryAsyncSessionLocal() as db:
        user_id = args.user_id
        if user_id is None:
            rows = (await db.execute(text("""
                SELECT DISTINCT user_id FROM config_profiles
                 WHERE config_type = 'mtf_calibration' AND is_active IS TRUE
            """))).fetchall()
            if len(rows) != 1:
                print(json.dumps({
                    "status": "CONFIG_REQUIRED",
                    "reason": f"user_id cardinality={len(rows)}",
                    "profiles_activation_mode": "DRAFT",
                    "thresholds_emitted": False,
                }, sort_keys=True))
                return 2
            user_id = rows[0].user_id
        try:
            result = await run_calibration(db, user_id=user_id)
        except ValueError as exc:
            print(json.dumps({
                "status": "CONFIG_REQUIRED", "reason": str(exc),
                "profiles_activation_mode": "DRAFT", "thresholds_emitted": False,
            }, sort_keys=True))
            return 2

    if args.output_dir:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        (args.output_dir / "mtf_calibration_run.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        profiles = result.get("selected_profiles") or {}
        for layer in ("L1", "L2"):
            if layer in profiles:
                (args.output_dir / f"mtf_profile_{layer.lower()}.json").write_text(
                    json.dumps(profiles[layer]["payload"], ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
        if profiles:
            package = {"profiles": [profiles[layer]["payload"] for layer in ("L1", "L2")]}
            (args.output_dir / "mtf_profiles_package.json").write_text(
                json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8"
            )
    print(json.dumps(result, sort_keys=True, default=str))
    return 0 if result.get("status") == "PASSED" else 3


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
