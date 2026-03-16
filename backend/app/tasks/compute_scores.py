"""Celery Task — compute Alpha Scores using Score Engine."""

import asyncio
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import text

from ..tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _score_async():
    from ..database import AsyncSessionLocal
    from ..services.score_engine import ScoreEngine
    from ..services.seed_service import DEFAULT_SCORE

    logger.info("Starting Alpha Score computation...")

    score_config = DEFAULT_SCORE
    engine = ScoreEngine(score_config)
    scored = 0

    async with AsyncSessionLocal() as db:
        # Get latest indicators for all symbols
        result = await db.execute(text("""
            SELECT DISTINCT ON (symbol) symbol, indicators_json, time
            FROM indicators
            WHERE time > now() - interval '2 hours'
            ORDER BY symbol, time DESC
        """))
        rows = result.fetchall()

        now = datetime.now(timezone.utc)

        for row in rows:
            try:
                indicators = row.indicators_json or {}
                score_result = engine.compute_alpha_score(indicators)

                components = score_result.get("components", {})

                await db.execute(text("""
                    INSERT INTO alpha_scores
                        (time, symbol, score, liquidity_score, market_structure_score,
                         momentum_score, signal_score, components_json)
                    VALUES
                        (:time, :symbol, :score, :liq, :ms, :mom, :sig, :components)
                """), {
                    "time": now,
                    "symbol": row.symbol,
                    "score": score_result["total_score"],
                    "liq": components.get("liquidity_score", 0),
                    "ms": components.get("market_structure_score", 0),
                    "mom": components.get("momentum_score", 0),
                    "sig": components.get("signal_score", 0),
                    "components": json.dumps({
                        "classification": score_result.get("classification"),
                        "matched_rules": score_result.get("matched_rules", []),
                    }),
                })

                scored += 1

            except Exception as e:
                logger.warning(f"Failed to compute score for {row.symbol}: {e}")
                continue

        await db.commit()

    logger.info(f"Alpha Score computation complete: {scored} symbols")
    return scored


@celery_app.task(name="app.tasks.compute_scores.score")
def score():
    count = _run_async(_score_async())
    celery_app.send_task("app.tasks.evaluate_signals.evaluate")
    return f"Scored {count} symbols"
