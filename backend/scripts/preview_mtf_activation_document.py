"""Run the governed MTF activation preview without committing any changes."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import AsyncSessionLocal  # noqa: E402
from app.services.mtf_profile_activation_service import (  # noqa: E402
    activate_existing_mtf_profiles,
)


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", type=UUID, required=True)
    parser.add_argument("--document", type=Path, required=True)
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> None:
    payload = json.loads(args.document.read_text(encoding="utf-8-sig"))
    async with AsyncSessionLocal() as db:
        result = await activate_existing_mtf_profiles(
            db, user_id=args.user_id, payload=payload, apply=False
        )
        await db.rollback()
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(_run(_args()))
