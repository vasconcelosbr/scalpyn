"""Prepare and capture reconstructible historical entry-risk contracts.

Dry-run is the default.  ``--apply`` marks only rows with the minimum immutable
lineage as PENDING and quarantines the rest as LEGACY_UNVERIFIABLE; the shared
capture service then processes the bounded batch.
"""

from __future__ import annotations

import argparse
import asyncio
import json

from sqlalchemy import text

from app.database import run_db_task
from app.services.entry_risk_capture_service import capture_pending_entry_risk


async def _run(db, *, apply: bool, limit: int) -> dict:
    counts = (await db.execute(text("""
        SELECT
            count(*) FILTER (
                WHERE entry_timestamp IS NOT NULL
                  AND exchange IS NOT NULL
                  AND feature_source_times IS NOT NULL
                  AND feature_source_times <> '{}'::jsonb
            ) AS reconstructible,
            count(*) FILTER (
                WHERE entry_timestamp IS NULL
                   OR exchange IS NULL
                   OR feature_source_times IS NULL
                   OR feature_source_times = '{}'::jsonb
            ) AS unverifiable
          FROM shadow_trades
         WHERE entry_risk_capture_status = 'NOT_AVAILABLE'
    """))).mappings().one()
    result = {
        "mode": "APPLY" if apply else "DRY_RUN",
        "reconstructible": int(counts["reconstructible"] or 0),
        "legacy_unverifiable": int(counts["unverifiable"] or 0),
    }
    if not apply:
        return result
    await db.execute(text("""
        UPDATE shadow_trades
           SET entry_risk_capture_status = CASE
                 WHEN entry_timestamp IS NOT NULL
                  AND exchange IS NOT NULL
                  AND feature_source_times IS NOT NULL
                  AND feature_source_times <> '{}'::jsonb
                 THEN 'PENDING'
                 ELSE 'INVALID'
               END,
               entry_risk_features_json = CASE
                 WHEN entry_timestamp IS NOT NULL
                  AND exchange IS NOT NULL
                  AND feature_source_times IS NOT NULL
                  AND feature_source_times <> '{}'::jsonb
                 THEN jsonb_build_object(
                   'schema_version', 'entry_risk_features_v1',
                   'capture_input', jsonb_build_object('backfill', true),
                   'contract_status', jsonb_build_object(
                     'status', 'PENDING',
                     'entry_risk_contract_valid', false,
                     'entry_risk_eligible_for_training', false,
                     'reason_codes', jsonb_build_array('CAPTURE_PENDING')
                   )
                 )
                 ELSE jsonb_build_object(
                   'schema_version', 'entry_risk_features_v1',
                   'contract_status', jsonb_build_object(
                     'status', 'INVALID',
                     'entry_risk_contract_valid', false,
                     'entry_risk_eligible_for_training', false,
                     'reason_codes', jsonb_build_array('LEGACY_UNVERIFIABLE')
                   )
                 )
               END,
               updated_at = now()
         WHERE entry_risk_capture_status = 'NOT_AVAILABLE'
    """))
    result["capture"] = await capture_pending_entry_risk(db, limit=limit)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    result = asyncio.run(
        run_db_task(
            lambda db: _run(db, apply=args.apply, limit=args.limit),
            celery=False,
        )
    )
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
