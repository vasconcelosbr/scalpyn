"""Preview or apply governed MTF v5 validity evidence.

Dry-run is the default.  The measurement window and user identity are always
explicit so no hidden observation horizon can enter the contract.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
import json
import os
from pathlib import Path
import sys
from uuid import UUID


BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
if os.environ.get("DATABASE_PUBLIC_URL"):
    os.environ["DATABASE_URL"] = os.environ["DATABASE_PUBLIC_URL"]

from app.database import AsyncSessionLocal  # noqa: E402
from app.services.strategy_settings_service import strategy_settings_service  # noqa: E402


def _datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


async def _run(args: argparse.Namespace) -> None:
    async with AsyncSessionLocal() as db:
        result = await strategy_settings_service.refresh_multilayer_validity(
            db,
            UUID(args.user_id),
            window_started_at=_datetime(args.window_started_at),
            window_ended_at=_datetime(args.window_ended_at),
            apply=args.apply,
        )
    output = result if args.full else {
        "changed": result["changed"],
        "applied": result["applied"],
        "previous_contract_hash": result["previous_contract_hash"],
        "contract_hash": result["contract_hash"],
        "contract_versions": {
            "provenance": result["multilayer_contract"]["provenance_policy_version"],
            "decision": result["multilayer_contract"]["decision_feature_contract_version"],
        },
        "validity_evidence": result["validity_evidence"],
        "coverage": {
            key: value for key, value in result["coverage"].items()
            if key != "identities"
        },
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, default=str))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--window-started-at", required=True)
    parser.add_argument("--window-ended-at", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--full", action="store_true")
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
