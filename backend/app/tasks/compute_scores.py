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
    from ..database import run_db_task
    from ..services.score_engine import ScoreEngine
    from ..services.seed_service import DEFAULT_SCORE

    logger.info("Starting Alpha Score computation...")

    # Shared state captured by the two phase closures below.
    rows: list = []
    scored: int = 0
    score_config: dict = DEFAULT_SCORE

    # ── Phase 1: score computation ─────────────────────────────────────────────
    # run_db_task opens a fresh Celery-safe session, begins a transaction,
    # runs the function, then auto-commits (or rolls back on exception).
    async def _phase1(db):
        nonlocal rows, scored, score_config

        try:
            from ..services.config_service import config_service
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

        result = await db.execute(text("""
            SELECT DISTINCT ON (symbol) symbol, indicators_json, time
            FROM indicators
            WHERE time > now() - interval '2 hours'
            ORDER BY symbol, time DESC
        """))
        rows = result.fetchall()

        now = datetime.now(timezone.utc)
        _scored = 0

        for row in rows:
            try:
                indicators = row.indicators_json or {}
                score_result = engine.compute_alpha_score(indicators)
                components = score_result.get("components", {})

                # Each insert is isolated in its own SAVEPOINT so a failure
                # for one symbol does not abort the whole transaction.
                async with db.begin_nested():
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

                _scored += 1

            except Exception as e:
                logger.warning(f"Failed to compute score for {row.symbol}: {e}")
                continue

        scored = _scored
        return scored

    await run_db_task(_phase1, celery=True)
    logger.info(f"Alpha Score computation complete: {scored} symbols")

    # ── Phase 2: level transition detection (separate transaction) ─────────────
    # Compare fresh scores against pipeline_watchlist_assets to detect
    # assets entering / leaving criteria (min_score from profile config).
    async def _phase2(db):
        await _detect_level_transitions(db, rows, score_config)

    try:
        await run_db_task(_phase2, celery=True)
    except Exception as e:
        logger.warning(f"Level transition detection failed: {e}")

    return scored


async def _detect_level_transitions(db, scored_rows, score_config=None) -> None:
    """
    For each symbol that just got a new score, check if its position in the
    pipeline has changed.  We look at pipeline_watchlist_assets rows and compare
    the new score against the PROFILE's min_score filter (not the watchlist's
    filters_json, which is no longer used for filtering).

    When a transition is detected:
      - Update level_direction + level_change_at in pipeline_watchlist_assets
      - Broadcast a WebSocket 'level_change' event via the alerts channel
    """
    from ..models.pipeline_watchlist import PipelineWatchlistAsset, PipelineWatchlist

    now = datetime.now(timezone.utc)

    # Build a quick symbol → new_score map from the rows we just scored
    # Use the same score config that was used to compute and store the scores,
    # so transition detection is consistent with the stored alpha_scores.
    new_scores: dict = {}
    from ..services.score_engine import ScoreEngine
    from ..services.seed_service import DEFAULT_SCORE
    _engine = ScoreEngine(score_config or DEFAULT_SCORE)
    for row in scored_rows:
        try:
            indicators = row.indicators_json or {}
            result = _engine.compute_alpha_score(indicators)
            new_scores[row.symbol] = result.get("total_score", 0)
        except Exception:
            continue

    if not new_scores:
        return

    # Fetch all pipeline_watchlist_assets for symbols with new scores,
    # including the profile config to get the min_score threshold.
    result = await db.execute(
        text("""
            SELECT pwa.id, pwa.watchlist_id, pwa.symbol,
                   pwa.alpha_score, pwa.level_direction,
                   pw.level, pw.user_id, pw.profile_id,
                   p.config AS profile_config
            FROM pipeline_watchlist_assets pwa
            JOIN pipeline_watchlists pw ON pw.id = pwa.watchlist_id
            LEFT JOIN profiles p ON p.id = pw.profile_id
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

        # Get min_score from the PROFILE (single source of truth)
        profile_cfg = ar.profile_config or {}
        min_score = float((profile_cfg.get("filters") or {}).get("min_score", 0))

        # Determine if asset currently meets profile criteria
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
