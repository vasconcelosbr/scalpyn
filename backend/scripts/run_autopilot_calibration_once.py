"""Manual executor for shadow calibration cycle.

Usage:
    railway run python -m backend.scripts.run_autopilot_calibration_once [--dry-run]
    railway run python -m backend.scripts.run_autopilot_calibration_once --once --target-scope SHADOW

Flags:
    --dry-run       Print what would be processed without writing to DB
    --once          Execute one full shadow calibration cycle and exit
    --target-scope  SHADOW (default) or PRODUCTION (requires explicit --no-safety-check)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("autopilot_calibration_once")


async def _dry_run_report(db) -> None:
    from sqlalchemy import text
    pending = await db.execute(text("""
        SELECT s.id, s.profile_id, s.profile_name,
               p.config->'scoring'->'thresholds'->>'buy' AS current_buy,
               s.confidence, s.created_at
        FROM profile_adjustment_suggestions s
        JOIN profiles p ON p.id = s.profile_id
        WHERE s.status = 'PENDING_SHADOW_VALIDATION'
          AND s.suggestion_type = 'REDUCE_RISK'
          AND s.target_field = 'minimum_score'
          AND NOT EXISTS (
              SELECT 1 FROM profile_adjustment_versions v WHERE v.suggestion_id = s.id
          )
        ORDER BY s.profile_id, s.created_at DESC
        LIMIT 50
    """))
    rows = pending.fetchall()

    # Deduplicate by profile (one per profile)
    seen = set()
    eligible = []
    for r in rows:
        if r.profile_id not in seen:
            seen.add(r.profile_id)
            eligible.append(r)

    print(f"\n[DRY RUN] Would process {len(eligible)} suggestions (one per profile):")
    print(f"{'Profile':<45} {'current_buy':>11} {'new_buy':>7} {'confidence':>10}")
    print("-" * 80)
    for r in eligible:
        current_buy = int(r.current_buy or 65)
        new_buy = min(current_buy + 5, 85)
        print(f"{str(r.profile_name):<45} {current_buy:>11} {new_buy:>7} {float(r.confidence or 0):>10.3f}")
    print(f"\n[DRY RUN] {len(eligible)} profiles would be processed. DB unchanged.")


async def _run(dry_run: bool, target_scope: str) -> None:
    if target_scope != "SHADOW":
        logger.error("Only SHADOW scope is supported without --no-safety-check. Aborting.")
        sys.exit(1)

    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import NullPool
    from backend.app.config import settings

    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with Session() as db:
        if dry_run:
            await _dry_run_report(db)
        else:
            from backend.app.services.profile_intelligence_live_service import (
                run_shadow_calibration_cycle,
            )
            logger.info("Running shadow calibration cycle (target_scope=%s)...", target_scope)
            result = await run_shadow_calibration_cycle(db)
            logger.info("Result: %s", json.dumps(result, indent=2))
            if result.get("errors"):
                logger.warning("Some errors occurred: %s", result["errors"])
            logger.info("Done. processed=%d failed=%d", result["processed"], result["failed"])

    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run autopilot shadow calibration once")
    parser.add_argument("--dry-run", action="store_true", help="Report only, no writes")
    parser.add_argument("--once", action="store_true", help="Execute one cycle and exit")
    parser.add_argument("--target-scope", default="SHADOW", help="SHADOW (default)")
    args = parser.parse_args()

    if not args.dry_run and not args.once:
        print("Must specify --dry-run or --once")
        parser.print_help()
        sys.exit(1)

    asyncio.run(_run(dry_run=args.dry_run, target_scope=args.target_scope))


if __name__ == "__main__":
    main()
