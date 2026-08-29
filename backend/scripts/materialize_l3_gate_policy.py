"""Materialize the six P1 L3 controls through StrategySettingsService.

Dry-run is the default. Use ``--apply`` only in the intended environment.
"""

from __future__ import annotations

import argparse
import asyncio
import json

from sqlalchemy import select

from app.database import CeleryAsyncSessionLocal
from app.models.pipeline_watchlist import PipelineWatchlist
from app.services.strategy_settings_service import strategy_settings_service


async def run(*, apply: bool) -> dict:
    async with CeleryAsyncSessionLocal() as db:
        user_ids = list(
            (
                await db.execute(
                    select(PipelineWatchlist.user_id)
                    .where(PipelineWatchlist.auto_refresh.is_(True))
                    .distinct()
                    .order_by(PipelineWatchlist.user_id)
                )
            ).scalars()
        )
        rows = []
        for user_id in user_ids:
            rows.append(
                await strategy_settings_service.materialize_l3_gate_policy(
                    db, user_id, apply=apply
                )
            )
        return {
            "mode": "apply" if apply else "dry-run",
            "users": len(user_ids),
            "changed": sum(1 for row in rows if row["changed"]),
            "applied": sum(1 for row in rows if row["applied"]),
            "rows": rows,
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(apply=args.apply)), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
