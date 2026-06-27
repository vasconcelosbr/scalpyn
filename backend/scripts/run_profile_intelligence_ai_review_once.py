"""
Manual trigger for Profile Intelligence AI Critic review.

Usage:
    python backend/scripts/run_profile_intelligence_ai_review_once.py --dry-run
    python backend/scripts/run_profile_intelligence_ai_review_once.py --once

Safety:
    - Does NOT create profiles
    - Does NOT apply mutations
    - Does NOT alter suggestions, watchlists, or shadow trades
    - Does NOT activate live trading or ML Gate
"""
import argparse
import asyncio
import logging
import os
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Make backend importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


async def _dry_run():
    """Print what the AI review would do without touching the DB or Anthropic."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import NullPool
    from app.core.config import settings

    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as db:
        # Key resolution
        ai_key = os.environ.get("ANTHROPIC_API_KEY", "")
        key_source = "env" if ai_key else "missing"

        if not ai_key:
            try:
                from app.services.ai_keys_service import decrypt_value
                key_row = await db.execute(text("""
                    SELECT api_key_encrypted FROM ai_provider_keys
                    WHERE provider = 'anthropic' AND is_active = true AND is_validated = true
                    ORDER BY last_tested_at DESC NULLS LAST
                    LIMIT 1
                """))
                enc = key_row.scalar_one_or_none()
                if enc:
                    decrypted = decrypt_value(bytes(enc) if not isinstance(enc, bytes) else enc)
                    key_source = "db"
                    key_decrypt_status = "success"
                    key_len_gt20 = len(decrypted) > 20
                else:
                    key_source = "missing"
                    key_decrypt_status = "no_record"
                    key_len_gt20 = False
            except Exception as exc:
                key_source = "db"
                key_decrypt_status = f"failed: {type(exc).__name__}: {exc}"
                key_len_gt20 = False
        else:
            key_decrypt_status = "not_needed"
            key_len_gt20 = len(ai_key) > 20

        # Payload summary
        row = await db.execute(text("""
            SELECT COUNT(*) AS profiles,
                   COUNT(DISTINCT profile_id) AS distinct_profiles
            FROM profile_indicator_performance
        """))
        r = row.fetchone()
        indicator_rows = r[0] if r else 0
        indicator_profiles = r[1] if r else 0

        hard_neg = await db.execute(text("SELECT COUNT(*) FROM profile_hard_negative_patterns"))
        hard_neg_rows = hard_neg.scalar() or 0

        sugg = await db.execute(text("""
            SELECT COUNT(*) FROM profile_adjustment_suggestions
            WHERE status = 'PENDING_SHADOW_VALIDATION'
        """))
        suggestions_count = sugg.scalar() or 0

        needs_ai = await db.execute(text("""
            SELECT COUNT(*) FROM profile_ai_reviews
            WHERE status IN ('SCHEDULED', 'RUNNING')
        """))
        in_progress = (needs_ai.scalar() or 0) > 0

    await engine.dispose()

    print("=" * 60)
    print("AI CRITIC DRY RUN")
    print("=" * 60)
    print(f"key_source            = {key_source}")
    print(f"key_decrypt_status    = {key_decrypt_status}")
    print(f"key_len_gt20          = {key_len_gt20}")
    print(f"payload_indicator_rows= {indicator_rows} (profiles={indicator_profiles})")
    print(f"payload_hard_neg_rows = {hard_neg_rows}")
    print(f"payload_suggestions   = {suggestions_count}")
    print(f"review_in_progress    = {in_progress}")
    print(f"would_call_anthropic  = {key_source != 'missing' and key_decrypt_status in ('success', 'not_needed')}")
    print(f"would_mutate          = False")
    print("=" * 60)

    if key_source == "missing" or key_decrypt_status not in ("success", "not_needed"):
        print("WARNING: AI call would be SKIPPED — key missing or decrypt failed")
        print("Status would be: FAILED_MISSING_KEY or FAILED_KEY_DECRYPT")
        return False
    return True


async def _once():
    """Run a single real AI review cycle."""
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import NullPool
    from app.core.config import settings
    from app.services.profile_intelligence_live_service import run_ai_review_cycle

    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    try:
        async with factory() as db:
            logger.info("[AIReviewOnce] Starting AI review...")
            result = await run_ai_review_cycle(db)
            logger.info("[AIReviewOnce] Done: %s", result)
            print("\n" + "=" * 60)
            print("RESULT")
            print("=" * 60)
            print(f"review_id    = {result.get('review_id')}")
            print(f"status       = {result.get('status')}")
            print(f"summary      = {str(result.get('summary') or '')[:200]}")
            print(f"next_review  = {result.get('next_review_at')}")
            print("=" * 60)
            return result.get("status") == "COMPLETED"
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description="Manual AI Critic trigger (safe, no mutations)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="Print what would happen without calling Anthropic")
    group.add_argument("--once", action="store_true", help="Run a single real AI review cycle")
    args = parser.parse_args()

    if args.dry_run:
        ok = asyncio.run(_dry_run())
        sys.exit(0 if ok else 1)
    elif args.once:
        ok = asyncio.run(_once())
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
