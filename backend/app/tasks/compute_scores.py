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

    async with AsyncSessionLocal() as db:
        # Load score config from the first user who has it configured
        score_config = DEFAULT_SCORE
        try:
            from ..services.config_service import config_service
            from ..models.pipeline_watchlist import PipelineWatchlist
            from sqlalchemy import select as sa_select
            user_row = (await db.execute(
                text("SELECT DISTINCT user_id FROM pipeline_watchlists LIMIT 1")
            )).fetchone()
            if user_row:
                cfg = await config_service.get_config(db, "score", user_row.user_id)
                if cfg and cfg.get("scoring_rules"):
                    score_config = cfg
        except Exception as _e:
            logger.debug("compute_scores: could not load user score config: %s", _e)

        engine = ScoreEngine(score_config)
        scored = 0

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

    # ── Level transition detection ────────────────────────────────────────────
    # Compare fresh scores against pipeline_watchlist_assets to detect
    # assets entering / leaving L3 criteria (score >= 75 as default threshold).
    try:
        await _detect_level_transitions(db, rows)
    except Exception as e:
        logger.warning(f"Level transition detection failed: {e}")

    return scored


async def _detect_level_transitions(db, scored_rows) -> None:
    """
    For each symbol that just got a new score, check if its position in the
    pipeline has changed.  We look at pipeline_watchlist_assets rows and compare
    the new score against the watchlist's min_score filter.

    When a transition is detected:
      - Update level_direction + level_change_at in pipeline_watchlist_assets
      - Broadcast a WebSocket 'level_change' event via the alerts channel
    """
    from ..models.pipeline_watchlist import PipelineWatchlistAsset, PipelineWatchlist

    now = datetime.now(timezone.utc)

    # Build a quick symbol → new_score map from the rows we just scored
    new_scores: dict = {}
    for row in scored_rows:
        try:
            indicators = row.indicators_json or {}
            from ..services.score_engine import ScoreEngine
            from ..services.seed_service import DEFAULT_SCORE
            result = ScoreEngine(DEFAULT_SCORE).compute_alpha_score(indicators)
            new_scores[row.symbol] = result.get("total_score", 0)
        except Exception:
            continue

    if not new_scores:
        return

    # Fetch all pipeline_watchlist_assets for symbols with new scores
    result = await db.execute(
        text("""
            SELECT pwa.id, pwa.watchlist_id, pwa.symbol,
                   pwa.alpha_score, pwa.level_direction,
                   pw.filters_json, pw.level, pw.user_id
            FROM pipeline_watchlist_assets pwa
            JOIN pipeline_watchlists pw ON pw.id = pwa.watchlist_id
            WHERE pwa.symbol = ANY(:symbols)
        """),
        {"symbols": list(new_scores.keys())},
    )
    asset_rows = result.fetchall()

    changed: list = []

    for ar in asset_rows:
        symbol = ar.symbol
        new_score = new_scores.get(symbol, 0)
        old_score = float(ar.alpha_score or 0)
        filters = ar.filters_json or {}
        min_score = float(filters.get("min_score", 0))

        # Determine if asset currently meets watchlist criteria
        was_qualifying = old_score >= min_score if min_score > 0 else True
        now_qualifying = new_score >= min_score if min_score > 0 else True

        if was_qualifying == now_qualifying:
            continue  # No change

        direction = "up" if now_qualifying else "down"

        # Update the asset row
        await db.execute(
            text("""
                UPDATE pipeline_watchlist_assets
                SET alpha_score = :score,
                    level_direction = :direction,
                    level_change_at = :now
                WHERE id = :id
            """),
            {"score": new_score, "direction": direction, "now": now, "id": str(ar.id)},
        )

        changed.append({
            "user_id": str(ar.user_id),
            "symbol": symbol,
            "direction": direction,
            "level": ar.level,
        })

    if changed:
        await db.commit()

    # Broadcast WebSocket events
    for ch in changed:
        try:
            from ..websocket.scalpyn_ws_server import broadcast_alert
            await broadcast_alert(
                ch["user_id"],
                "level_change",
                {
                    "symbol": ch["symbol"],
                    "direction": ch["direction"],
                    "level": ch["level"],
                },
            )
        except Exception:
            pass  # WS not critical path


@celery_app.task(name="app.tasks.compute_scores.score")
def score():
    count = _run_async(_score_async())
    celery_app.send_task("app.tasks.evaluate_signals.evaluate")
    return f"Scored {count} symbols"
