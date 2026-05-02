"""Celery Task — compute Alpha Scores via the robust engine.

Phase 4 cleanup: this task no longer runs the legacy ``ScoreEngine`` math.
For every fresh row in ``indicators`` it asks the robust engine to wrap the
indicator dict, validate, and emit a confidence-weighted score, and then
persists the result in ``alpha_scores`` (always ``scoring_version='v1'`` —
the legacy dual-write columns are written as ``NULL``). The same robust
score is reused by the level-transition detector so a single computation
drives both downstream consumers.
"""

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


def _robust_score_for(symbol: str, indicators: dict, rules: list) -> dict | None:
    """Return ``{score, score_confidence, global_confidence, matched_rules}``
    or ``None`` when the robust engine rejects the indicators.

    Thin wrapper around ``compute_asset_score`` so callers don't have to
    construct envelopes themselves.
    """
    from ..services.robust_indicators import compute_asset_score

    return compute_asset_score(symbol, indicators or {}, rules, is_futures=False)


async def _score_async():
    from ..database import run_db_task
    from ..services.seed_service import DEFAULT_SCORE

    logger.info("Starting Alpha Score computation (robust engine)...")

    rows: list = []
    scored: int = 0
    rules: list = []
    cached_scores: dict[str, float] = {}

    async def _phase1(db):
        nonlocal rows, scored, rules

        # Score config — only the ``scoring_rules`` list matters to the
        # robust engine. Prefer a user override when one exists.
        score_config: dict = DEFAULT_SCORE
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

        rules = (
            score_config.get("scoring_rules")
            or score_config.get("rules")
            or DEFAULT_SCORE.get("scoring_rules")
            or []
        )

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
                scored_payload = _robust_score_for(row.symbol, indicators, rules)
                if scored_payload is None:
                    continue

                # Each insert is isolated in its own SAVEPOINT so a failure
                # for one symbol does not abort the whole transaction.
                async with db.begin_nested():
                    await db.execute(text("""
                        INSERT INTO alpha_scores
                            (time, symbol, score, liquidity_score, market_structure_score,
                             momentum_score, signal_score, components_json,
                             alpha_score_v2, confidence_metrics, scoring_version)
                        VALUES
                            (:time, :symbol, :score, NULL, NULL, NULL, NULL, :components,
                             NULL, NULL, 'v1')
                    """), {
                        "time": now,
                        "symbol": row.symbol,
                        "score": scored_payload["score"],
                        "components": json.dumps({
                            "engine": "robust",
                            "score_confidence": scored_payload["score_confidence"],
                            "global_confidence": scored_payload["global_confidence"],
                            "matched_rules": scored_payload["matched_rules"],
                        }),
                    })

                # Cache the score so _detect_level_transitions doesn't need
                # to re-run the robust engine for the same row. SQLAlchemy
                # ``Row`` objects are immutable, so use an external dict.
                cached_scores[row.symbol] = scored_payload["score"]
                _scored += 1

            except Exception as e:
                logger.warning(f"Failed to compute score for {row.symbol}: {e}")
                continue

        scored = _scored
        return scored

    await run_db_task(_phase1, celery=True)
    logger.info(f"Alpha Score computation complete: {scored} symbols")

    # ── Phase 2: level transition detection (separate transaction) ─────────────
    async def _phase2(db):
        await _detect_level_transitions(db, rows, rules, cached_scores)

    try:
        await run_db_task(_phase2, celery=True)
    except Exception as e:
        logger.warning(f"Level transition detection failed: {e}")

    return scored


async def _detect_level_transitions(db, scored_rows, rules, cached_scores: dict) -> None:
    """For each symbol that just got a new score, check whether the asset's
    qualifying status (against the profile's ``min_score`` filter) changed
    and broadcast a ``level_change`` event when it did.
    """
    now = datetime.now(timezone.utc)

    new_scores: dict = dict(cached_scores)
    for row in scored_rows:
        if row.symbol in new_scores:
            continue
        scored_payload = _robust_score_for(row.symbol, row.indicators_json or {}, rules)
        if scored_payload is not None:
            new_scores[row.symbol] = scored_payload["score"]

    if not new_scores:
        return

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

        profile_cfg = ar.profile_config or {}
        min_score = float((profile_cfg.get("filters") or {}).get("min_score", 0))

        was_qualifying = old_score >= min_score if min_score > 0 else True
        now_qualifying = new_score >= min_score if min_score > 0 else True

        if was_qualifying == now_qualifying:
            continue

        direction = "up" if now_qualifying else "down"

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
            pass


@celery_app.task(name="app.tasks.compute_scores.score")
def score():
    count = _run_async(_score_async())
    celery_app.send_task("app.tasks.evaluate_signals.evaluate")
    return f"Scored {count} symbols"
