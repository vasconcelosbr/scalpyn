"""Celery Task — compute Alpha Scores via the robust engine.

Phase 4 cleanup: this task no longer runs the legacy ``ScoreEngine`` math.
For every fresh row in ``indicators`` it asks the robust engine to wrap the
indicator dict, validate, and emit a deterministic score, and then
persists the result in ``alpha_scores`` (always ``scoring_version='v1'`` —
the legacy dual-write columns are written as ``NULL``). The same robust
score is reused by the level-transition detector so a single computation
drives both downstream consumers.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone

from asyncpg.exceptions import UndefinedColumnError as _AsyncpgUndefinedColumn
from sqlalchemy import text

from ..tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


def _is_scoring_version_drift(exc: BaseException) -> bool:
    """Detect alpha_scores.scoring_version missing-column drift.

    Mirrors the structural-scheduler `_is_scheduler_group_drift` guard
    (Task #178 / migration 032) and the same anti-false-positive rule:
    we only match against the asyncpg exception's OWN message (which is
    "column ... of relation alpha_scores does not exist"), never against
    str(SQLAlchemy.ProgrammingError) — that wrapper includes the SQL
    statement text and our INSERT always contains the literal column
    name, so matching on the wrapper would silently swallow unrelated
    asyncpg errors (FK violations, lock timeouts, deadlocks, ...).

    Migration 028 adds the column, but Task #233's audit observed
    drift in the production schema where the migration succeeded but
    the column ended up missing (suspected hand-rolled rollback).
    Until the prod schema is repaired this guard prevents one bad
    INSERT per symbol per cycle from poisoning every subsequent
    `_persist` callback inside the structural scheduler.
    """
    orig = getattr(exc, "orig", None)
    if isinstance(orig, _AsyncpgUndefinedColumn) and "scoring_version" in str(orig):
        return True
    if isinstance(exc, _AsyncpgUndefinedColumn) and "scoring_version" in str(exc):
        return True
    return False


# Boot-once flag: log the drift error a single time per process so we don't
# flood the logs with one error per symbol per cycle.
_scoring_version_drift_logged: bool = False


def _run_async(coro):
    """Run async coroutine in a sync Celery task.

    Creates a dedicated event loop per task invocation. Drains all pending
    asyncpg tasks and disposes the NullPool engine before closing the loop.

    Without dispose + drain, asyncpg schedules _terminate_graceful_close
    via loop.create_task() during GC of NullPool connections after loop.close(),
    causing RuntimeError: Event loop is closed on the next invocation.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        # Step 1 — cancel and drain pending asyncio tasks.
        try:
            pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
            for t in pending:
                t.cancel()
            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
        except BaseException as exc:
            logger.debug("[_run_async] pending-task drain failed: %s", exc)

        # Step 2 — graceful engine dispose (closes asyncpg sockets in-loop).
        try:
            from ..database import _celery_engine
            loop.run_until_complete(_celery_engine.dispose())
        except BaseException as exc:
            logger.debug("[_run_async] _celery_engine.dispose failed: %s", exc)

        # Step 3 — hard-terminate any asyncpg connection still cached on the pool.
        try:
            from ..database import _celery_engine as _ce
            sync_pool = _ce.sync_engine.pool
            records = list(getattr(sync_pool, "_all_conns", None) or [])
            for record in records:
                raw = (
                    getattr(record, "dbapi_connection", None)
                    or getattr(record, "connection", None)
                )
                asyncpg_conn = (
                    getattr(raw, "_connection", None)
                    or getattr(raw, "connection", None)
                    or raw
                )
                terminate = getattr(asyncpg_conn, "terminate", None)
                if callable(terminate):
                    try:
                        terminate()
                    except BaseException:
                        pass
        except BaseException as exc:
            logger.debug("[_run_async] hard-terminate sweep failed: %s", exc)

        # Step 4 — drain async generators registered on the loop.
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        except BaseException as exc:
            logger.debug("[_run_async] shutdown_asyncgens failed: %s", exc)

        # Step 5 — close the loop. Always last; never propagate.
        try:
            loop.close()
        except BaseException as exc:
            logger.debug("[_run_async] loop.close failed: %s", exc)
        try:
            asyncio.set_event_loop(None)
        except BaseException:
            pass


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

        # Task #215: route through the unified provider so the partial-row
        # ``DISTINCT ON`` anti-pattern is gone here too. compute_scores
        # writes ``alpha_scores``, which is consumed downstream — feeding
        # it micro-only-latest payloads would write incomplete scores
        # for the same ~67% of cycles.
        # Task #232: scoring is INGESTION-DOMAIN — every active symbol
        # must be scored so the L2/L3 funnel sees its full candidate
        # universe. Trading authorisation (``is_tradable``) is enforced
        # later in evaluate_signals/execute_buy and never here.
        from ..services.indicators_provider import (
            get_merged_indicators,
            is_complete,
        )
        from types import SimpleNamespace

        pool_res = await db.execute(text("""
            SELECT DISTINCT symbol FROM pool_coins
            WHERE is_active = true
        """))
        pool_symbols = [r.symbol for r in pool_res.fetchall()]

        merged_by_sym = (
            await get_merged_indicators(db, pool_symbols)
            if pool_symbols else {}
        )

        # Build row-shaped objects so the loop body and Phase 2 keep
        # operating on the same shape (.symbol / .indicators_json / .time).
        import sqlalchemy.exc as _sqla_exc
        rows = []
        for symbol, mi in merged_by_sym.items():
            flat = mi.as_flat_dict()
            ok, missing = is_complete(flat)
            if not ok:
                logger.warning(
                    "[compute_scores] QUARANTINED %s — missing core: %s",
                    symbol, missing,
                )
                continue
            rows.append(SimpleNamespace(
                symbol=symbol,
                indicators_json=flat,
                time=datetime.now(timezone.utc),
            ))

        # Task #273: deterministic sort by symbol — every UPSERT into
        # ``alpha_scores`` (and the downstream UPDATE on
        # ``pipeline_watchlist_assets``) acquires row-locks in the order
        # rows are iterated. ``merged_by_sym`` comes from a dict whose
        # ordering depends on provider internals, so two concurrent
        # ``score`` runs (or score + level-transition) could deadlock on
        # cross order. Same root cause as #251.
        rows.sort(key=lambda r: r.symbol)

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
                try:
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
                except Exception as exc:
                    # Task #234 — schema drift fallback: if alpha_scores.scoring_version
                    # is missing in production we retry once WITHOUT the column so the
                    # cycle keeps producing scores. Logged once per process.
                    if not _is_scoring_version_drift(exc):
                        raise
                    global _scoring_version_drift_logged
                    if not _scoring_version_drift_logged:
                        logger.error(
                            "[compute_scores] SCHEMA DRIFT: alpha_scores.scoring_version "
                            "column missing — migration 028 not applied or rolled back. "
                            "Falling back to legacy INSERT (no scoring_version) until the "
                            "column is repaired. See docs/runbooks/critical-schema-drift.md."
                        )
                        _scoring_version_drift_logged = True
                    try:
                        async with db.begin_nested():
                            await db.execute(text("""
                                INSERT INTO alpha_scores
                                    (time, symbol, score, liquidity_score, market_structure_score,
                                     momentum_score, signal_score, components_json,
                                     alpha_score_v2, confidence_metrics)
                                VALUES
                                    (:time, :symbol, :score, NULL, NULL, NULL, NULL, :components,
                                     NULL, NULL)
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
                    except Exception as _sp_exc:
                        logger.error(
                            "[compute_scores] SAVEPOINT (drift-fallback) failed for %s — rolling back via run_db_task: %s",
                            row.symbol, _sp_exc,
                        )
                        raise

                # Cache the score so _detect_level_transitions doesn't need
                # to re-run the robust engine for the same row. SQLAlchemy
                # ``Row`` objects are immutable, so use an external dict.
                cached_scores[row.symbol] = scored_payload["score"]
                _scored += 1

            except Exception as e:
                if isinstance(e, _sqla_exc.PendingRollbackError):
                    logger.error(
                        "[compute_scores] PendingRollbackError for %s — aborting scoring loop, run_db_task will rollback: %s",
                        row.symbol, e,
                    )
                    raise
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
    # Task #273: iterate sorted so re-score path matches the ordering
    # used by the main scoring loop above.
    for row in sorted(scored_rows, key=lambda r: r.symbol):
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
            ORDER BY pwa.symbol, pwa.id
        """),
        {"symbols": sorted(new_scores.keys())},
    )
    # Task #273: SELECT is ORDER BY symbol/id, but defensively re-sort
    # the materialized rows so the per-row UPDATE loop below acquires
    # row-locks on ``pipeline_watchlist_assets`` in deterministic
    # order even if the driver re-shuffles fetchall() output.
    asset_rows = sorted(result.fetchall(), key=lambda ar: (ar.symbol, str(ar.id)))

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
    # Chain: scoring → signal evaluation (execution queue, isolated workers).
    # TTL = evaluate time_limit (120s) + 30s margin.
    from . import task_dispatch
    task_dispatch.enqueue(
        "app.tasks.evaluate_signals.evaluate",
        dedup_key="evaluate",
        ttl_seconds=150,
    )
    return f"Scored {count} symbols"
