"""Reclassify hollow AI reviews with immutable snapshots and activity evidence."""
from __future__ import annotations
import argparse
import asyncio
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


async def run(*, apply: bool, expected_count: int, fix_deployed_at: datetime) -> int:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import NullPool
    from app.config import settings
    from app.services.ai_review_safety_service import reclassified_status

    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as db:
            rows = (await db.execute(text("""
                SELECT id, status, requested_at, created_at, to_jsonb(r) AS snapshot
                FROM profile_ai_reviews r
                WHERE (requested_at >= now() - interval '24 hours'
                       OR created_at >= now() - interval '24 hours')
                  AND status = 'COMPLETED'
                  AND COALESCE(tokens_input, 0) = 0
                  AND COALESCE(tokens_output, 0) = 0
                  AND NULLIF(BTRIM(COALESCE(summary, '')), '') IS NULL
                ORDER BY COALESCE(requested_at, created_at), id
                FOR UPDATE
            """))).fetchall()
            if len(rows) != expected_count:
                raise RuntimeError(f"scope mismatch: expected={expected_count} actual={len(rows)}")

            preview = []
            for row in rows:
                new_status = reclassified_status(requested_at=row.requested_at,
                    created_at=row.created_at, fix_deployed_at=fix_deployed_at)
                preview.append({"review_id": str(row.id), "old_status": row.status,
                                "new_status": new_status})
                if apply:
                    reason = ("pre-fix hollow artifact" if new_status == "LEGACY_HOLLOW_REVIEW"
                              else "post-fix hollow response")
                    await db.execute(text("""
                        INSERT INTO profile_ai_reviews_reclassification_audit
                            (review_id, old_status, new_status, reason, fix_deployed_at,
                             review_snapshot, actor)
                        VALUES (:id, :old, :new, :reason, :fix_at,
                                CAST(:snapshot AS jsonb),
                                'controlled-safety-reclassification-2026-06-28')
                    """), {"id": str(row.id), "old": row.status, "new": new_status,
                           "reason": reason, "fix_at": fix_deployed_at,
                           "snapshot": json.dumps(row.snapshot, default=str)})
                    result = await db.execute(text("""
                        UPDATE profile_ai_reviews
                        SET status = CAST(:new_status AS varchar),
                            risk_flags = COALESCE(risk_flags, '[]'::jsonb) ||
                                jsonb_build_array(jsonb_build_object(
                                    'flag', CAST(:new_flag AS text), 'reason', CAST(:reason AS text),
                                    'reclassified_at', now(), 'fix_deployed_at',
                                    CAST(:fix_at AS timestamptz)))
                        WHERE id = :id AND status = 'COMPLETED'
                    """), {"id": str(row.id), "new_status": new_status, "new_flag": new_status,
                           "reason": reason, "fix_at": fix_deployed_at})
                    if result.rowcount != 1:
                        raise RuntimeError(f"concurrent update for review {row.id}")
                    await db.execute(text("""
                        INSERT INTO profile_intelligence_activity_log
                            (event_type, phase, severity, message, payload)
                        VALUES ('AI_REVIEW_RECLASSIFIED', 'ai', 'warning',
                                'Hollow AI review reclassified with full audit snapshot',
                                jsonb_build_object('review_id', CAST(:id AS text),
                                                   'old_status', CAST(:old AS text),
                                                   'new_status', CAST(:new AS text),
                                                   'reason', CAST(:reason AS text)))
                    """), {"id": str(row.id), "old": row.status,
                           "new": new_status, "reason": reason})

            print(json.dumps({"apply": apply, "count": len(rows), "reviews": preview}, indent=2))
            if apply:
                await db.commit()
            else:
                await db.rollback()
            return len(rows)
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-count", required=True, type=int)
    parser.add_argument("--fix-deployed-at", required=True, type=datetime.fromisoformat)
    args = parser.parse_args()
    asyncio.run(run(apply=args.apply, expected_count=args.expected_count,
                    fix_deployed_at=args.fix_deployed_at))


if __name__ == "__main__":
    main()
