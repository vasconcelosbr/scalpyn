"""Celery Task — Pipeline Scan (L1 → L2 → L3).

Runs every 5 minutes (triggered by compute_5m chain or beat schedule).
For each active user:
  1. Fetch all PipelineWatchlists (POOL / L1 / L2 / L3)
  2. Resolve the symbol universe per watchlist (from Pool or parent watchlist)
  3. Fetch market data (indicators + alpha_scores + market_metadata)
  4. Apply ProfileEngine filters/scoring per level
  5. Persist results in pipeline_watchlist_assets (upsert)
  6. Compare with prior snapshot in Redis → detect new L3 signals
  7. Broadcast new signals via WebSocket (channel "signals" + "pipeline")
"""

import asyncio
import json
import logging
from decimal import Decimal
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Optional
from uuid import uuid4

from ..tasks.celery_app import celery_app
from ..services.pipeline_rejections import evaluate_rejections
from ..utils.pipeline_profile_filters import (
    STRICT_META_FIELDS,
    effective_pipeline_level,
    resolve_pipeline_dependency,
    select_profile_filter_conditions,
    uses_pipeline_filters,
    WATCHLIST_STAGE_ORDER,
)

logger = logging.getLogger(__name__)

_REDIS_PREFIX = "spe:pipeline:"   # Redis key prefix per watchlist

# Default staleness threshold (minutes).  Assets not re-confirmed within this
# window are automatically marked 'down'.  Override per-watchlist via
# filters_json.staleness_minutes (GUI-editable).
_DEFAULT_STALENESS_MINUTES = 30
_PIPELINE_EXECUTION_TRACKING_SCHEMA_READY = False
_PIPELINE_EXECUTION_TRACKING_SCHEMA_LOCK = asyncio.Lock()

# Strict metadata fields — NULL means FAIL (not skip) in profile filters.
# Used by diagnostic logging in _apply_level_filter.
_DIAG_STRICT_META = STRICT_META_FIELDS


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ─── helpers ─────────────────────────────────────────────────────────────────

_PIPELINE_EXECUTION_ORDER = ("POOL", "L1", "L2", "L3")

def _uses_pipeline_filters(level: Optional[str]) -> bool:
    """Only POOL/L1/L2/L3 are filter-enforced pipeline stages."""
    return uses_pipeline_filters(level)


def _log_pipeline_event(
    *,
    level: str,
    execution_id: str,
    event_type: str,
    watchlist_id: Optional[str] = None,
    symbol: Optional[str] = None,
    **extra: Any,
) -> None:
    payload: dict[str, Any] = {
        "type": event_type,
        "level": level,
        "execution_id": execution_id,
    }
    if watchlist_id:
        payload["watchlist_id"] = watchlist_id
    if symbol:
        payload["symbol"] = symbol
    payload.update(extra)
    logger.error(payload)


def _log_stage_processing_summary(
    *,
    level: str,
    input_count: int,
    approved_count: int,
    rejected_count: int,
    watchlist_id: str,
    execution_id: str,
) -> None:
    if level == "POOL":
        logger.info("[POOL] scanned: %d assets", input_count)
        logger.info("[POOL] approved: %d", approved_count)
        return

    if level == "L1":
        total_processed = approved_count + rejected_count
        logger.info("[L1] input received: %d assets", input_count)
        logger.info("[L1] approved: %d", approved_count)
        logger.info("[L1] rejected: %d", rejected_count)
        logger.info("[L1] total processed: %d", total_processed)
        if total_processed != input_count:
            message = (
                f"Asset count mismatch: processed {total_processed} "
                f"but received {input_count}"
            )
            _log_pipeline_event(
                level=level,
                execution_id=execution_id,
                event_type="PIPELINE_INCONSISTENCY",
                watchlist_id=watchlist_id,
                expected=input_count,
                actual=total_processed,
                reason="processed_count_mismatch",
                message=message,
            )


def _normalize_sources_for_scan(
    *,
    level: str,
    watchlist_id: str,
    source_pool_id: Optional[str],
    source_watchlist_id: Optional[str],
    execution_id: str,
) -> tuple[Optional[str], Optional[str]]:
    normalized_level = (level or "").upper()
    pool_id = str(source_pool_id) if source_pool_id else None
    watchlist_source_id = str(source_watchlist_id) if source_watchlist_id else None

    if normalized_level in {"L1", "L2", "L3"} and pool_id and watchlist_source_id:
        logger.warning(
            {
                "type": "INVALID_SOURCE_CONFIG",
                "watchlist_id": watchlist_id,
                "level": normalized_level,
                "execution_id": execution_id,
                "message": "Both source_pool_id and source_watchlist_id set; prioritizing source_watchlist_id.",
            }
        )
        pool_id = None
    elif normalized_level in {"L1", "L2", "L3"} and pool_id and not watchlist_source_id:
        logger.warning(
            {
                "type": "INVALID_SOURCE_CONFIG",
                "watchlist_id": watchlist_id,
                "level": normalized_level,
                "execution_id": execution_id,
                "message": "source_pool_id is not allowed for L1/L2/L3; ignoring source_pool_id.",
            }
        )
        pool_id = None
    elif normalized_level == "POOL" and watchlist_source_id:
        logger.warning(
            {
                "type": "INVALID_SOURCE_CONFIG",
                "watchlist_id": watchlist_id,
                "level": normalized_level,
                "execution_id": execution_id,
                "message": "source_watchlist_id is not allowed for POOL; ignoring source_watchlist_id.",
            }
        )
        watchlist_source_id = None

    return pool_id, watchlist_source_id


def _intersect_with_upstream(
    *,
    symbols: list[str],
    upstream_symbols: set[str],
    level: str,
    watchlist_id: str,
    execution_id: str,
) -> list[str]:
    if not upstream_symbols:
        return []

    pruned: list[str] = []
    for symbol in symbols:
        if symbol in upstream_symbols:
            pruned.append(symbol)
            continue
        _log_pipeline_event(
            level=level,
            execution_id=execution_id,
            event_type="PIPELINE_VIOLATION",
            watchlist_id=watchlist_id,
            symbol=symbol,
            reason="symbol_not_in_upstream",
        )
    return pruned


def _placeholder_asset_without_market_data(symbol: str) -> dict:
    """Build a minimal asset shell so monitoring boards can list symbols without metadata yet."""
    return {
        "symbol": symbol,
        "name": symbol,
        "price": None,
        "change_24h": None,
        "volume_24h": None,
        "market_cap": None,
        "spread_pct": None,
        "orderbook_depth_usdt": None,
        "indicators": {},
    }


def _build_pipeline_asset(
    symbol: str,
    *,
    name: Optional[str],
    indicators: dict,
    score_row,
    has_market_metadata: bool,
    price=None,
    change_24h=None,
    market_cap=None,
    volume_24h=None,
    spread_pct=None,
    orderbook_depth_usdt=None,
) -> dict:
    """Build a normalized pipeline asset dict from metadata, indicators, and scores."""
    asset = {
        "symbol": symbol,
        "name": name or symbol,
        "price": price,
        "change_24h": change_24h,
        "market_cap": market_cap,
        "volume_24h": volume_24h,
        "spread_pct": spread_pct,
        "orderbook_depth_usdt": orderbook_depth_usdt,
        "indicators": indicators,
        "_has_market_metadata": has_market_metadata,
        **{k: v for k, v in indicators.items() if isinstance(v, (int, float, bool, str))},
    }

    if "atr_pct" in asset and "atr_percent" not in asset:
        asset["atr_percent"] = asset["atr_pct"]

    di_plus = asset.get("di_plus")
    di_minus = asset.get("di_minus")
    if di_plus is not None and di_minus is not None:
        try:
            asset["di_trend"] = float(di_plus) > float(di_minus)
        except (TypeError, ValueError):
            pass

    if score_row:
        asset["score"] = float(score_row.score) if score_row.score else 0.0
        asset["liquidity_score"] = float(score_row.liquidity_score) if score_row.liquidity_score else 0.0
        asset["market_structure_score"] = float(score_row.market_structure_score) if score_row.market_structure_score else 0.0
        asset["momentum_score"] = float(score_row.momentum_score) if score_row.momentum_score else 0.0
        asset["signal_score"] = float(score_row.signal_score) if score_row.signal_score else 0.0

    return asset


def _get_redis():
    """Return a Redis client (soft dependency — returns None if unavailable)."""
    try:
        import redis as redis_lib
        from ..config import settings
        return redis_lib.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=2,
        )
    except Exception as exc:
        logger.warning("Pipeline scan: Redis unavailable: %s", exc)
        return None


def _prior_signals(redis, watchlist_id: str) -> set:
    """Load the set of symbols that triggered L3 in the last scan."""
    if not redis:
        return set()
    try:
        raw = redis.get(f"{_REDIS_PREFIX}{watchlist_id}:signals")
        return set(json.loads(raw)) if raw else set()
    except Exception:
        return set()


def _save_signals(redis, watchlist_id: str, symbols: set, ttl: int = 300):
    """Persist the current signal set for the next comparison (TTL 5 min)."""
    if not redis:
        return
    try:
        redis.setex(f"{_REDIS_PREFIX}{watchlist_id}:signals", ttl, json.dumps(list(symbols)))
    except Exception:
        pass


def _prior_decision_states(redis, watchlist_id: str) -> dict:
    """Load the map of {symbol: {state, score, direction, saved_at}} from the last scan."""
    if not redis:
        return {}
    try:
        raw = redis.get(f"{_REDIS_PREFIX}{watchlist_id}:decision_states")
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


def _save_decision_states(redis, watchlist_id: str, states: dict, ttl: int = 600):
    """Persist current decision state map for next-cycle comparison (TTL 10 min)."""
    if not redis:
        return
    try:
        redis.setex(f"{_REDIS_PREFIX}{watchlist_id}:decision_states", ttl, json.dumps(states))
    except Exception:
        pass


def _should_log_decision(
    decision: dict,
    prior: Optional[dict],
    score_delta_threshold: float = 5.0,
    direction_change_logs: bool = True,
) -> tuple[bool, Optional[str]]:
    """
    Decide whether a decision dict should be written to the Decision Log and
    return the event_type string.

    Rules:
    - None/absent → ALLOW   → log, NEW_SIGNAL
    - ALLOW       → BLOCK   → log, SIGNAL_LOST
    - BLOCK       → ALLOW   → log, SIGNAL_REGAINED
    - ALLOW → ALLOW stable  → skip
    - ALLOW → ALLOW score delta > threshold → log, SIGNAL_EVOLVED_SCORE
    - ALLOW → ALLOW direction flip         → log, SIGNAL_EVOLVED_DIRECTION (if enabled)
    - BLOCK → BLOCK         → skip
    - None/absent → BLOCK   → skip

    Recovery rule: if prior state is ALLOW but was never confirmed by a successful DB
    write (db_confirmed_at is absent), treat prior as None so the symbol is re-logged.
    This auto-recovers symbols stuck by the ordering bug where Redis advanced ahead of DB.
    """
    current_state = decision.get("decision")
    current_score = float(decision.get("score") or 0)
    current_direction = decision.get("direction")

    # Recovery: unconfirmed ALLOW state means the DB write never happened — reset
    if prior is not None and prior.get("state") == "ALLOW" and not prior.get("db_confirmed_at"):
        prior = None

    if prior is None:
        if current_state == "ALLOW":
            return True, "NEW_SIGNAL"
        return False, None

    prior_state = prior.get("state")
    prior_score = float(prior.get("score") or 0)
    prior_direction = prior.get("direction")

    if prior_state != "ALLOW" and current_state == "ALLOW":
        return True, "SIGNAL_REGAINED"

    if prior_state == "ALLOW" and current_state == "BLOCK":
        return True, "SIGNAL_LOST"

    if prior_state == "ALLOW" and current_state == "ALLOW":
        if abs(current_score - prior_score) > score_delta_threshold:
            return True, "SIGNAL_EVOLVED_SCORE"
        if direction_change_logs and current_direction and prior_direction and current_direction != prior_direction:
            return True, "SIGNAL_EVOLVED_DIRECTION"
        return False, None

    return False, None


async def _update_last_scanned(db, watchlist_id: str):
    """Update last_scanned_at on a PipelineWatchlist after each scan attempt."""
    from sqlalchemy import text
    now = datetime.now(timezone.utc)
    try:
        await db.execute(
            text("UPDATE pipeline_watchlists SET last_scanned_at = :now WHERE id = :wid"),
            {"now": now, "wid": watchlist_id},
        )
        await db.commit()
    except Exception as exc:
        logger.debug("[PipelineScan] Failed to update last_scanned_at for %s: %s", watchlist_id, exc)
        # Reset the session so the next watchlist in the scan loop does not
        # inherit an aborted asyncpg transaction (InFailedSQLTransactionError).
        try:
            await db.rollback()
        except Exception as rb_exc:
            logger.debug("[PipelineScan] Rollback after last_scanned_at failure failed: %s", rb_exc)


# ─── market data loader ───────────────────────────────────────────────────────

async def _fetch_market_data(db, symbols: list) -> list:
    """
    Return a list of asset dicts for the given symbols,
    joining market_metadata + indicators + alpha_scores.
    Mirrors _get_assets_with_indicators from custom_watchlists.py.
    """
    from sqlalchemy import text

    if not symbols:
        return []

    syms_list = list(symbols)

    # Step 1: Fetch market metadata — try with new liquidity columns, fall back if absent
    try:
        meta_rows = (await db.execute(
            text("""
                SELECT
                    m.symbol, m.name,
                    COALESCE(m.market_cap,  pwa.market_cap)  AS market_cap,
                    COALESCE(m.volume_24h,  pwa.volume_24h)  AS volume_24h,
                    m.price,
                    m.price_change_24h,
                    m.spread_pct,
                    m.orderbook_depth_usdt
                FROM market_metadata m
                LEFT JOIN (
                    SELECT DISTINCT ON (symbol)
                           symbol, market_cap, volume_24h
                    FROM   pipeline_watchlist_assets
                    WHERE  symbol = ANY(:symbols)
                    ORDER  BY symbol, entered_at DESC
                ) pwa ON pwa.symbol = m.symbol
                WHERE  m.symbol = ANY(:symbols)
            """),
            {"symbols": syms_list},
        )).fetchall()
    except Exception:
        # Fallback: columns spread_pct / orderbook_depth_usdt may not exist yet
        meta_rows = (await db.execute(
            text("""
                SELECT
                    m.symbol, m.name,
                    COALESCE(m.market_cap, pwa.market_cap) AS market_cap,
                    COALESCE(m.volume_24h, pwa.volume_24h) AS volume_24h,
                    m.price,
                    m.price_change_24h,
                    NULL AS spread_pct,
                    NULL AS orderbook_depth_usdt
                FROM market_metadata m
                LEFT JOIN (
                    SELECT DISTINCT ON (symbol)
                           symbol, market_cap, volume_24h
                    FROM   pipeline_watchlist_assets
                    WHERE  symbol = ANY(:symbols)
                    ORDER  BY symbol, entered_at DESC
                ) pwa ON pwa.symbol = m.symbol
                WHERE  m.symbol = ANY(:symbols)
            """),
            {"symbols": syms_list},
        )).fetchall()

    # Step 2: Fetch indicators via dual-scheduler merge utility
    # Merges structural (15m cadence) + microstructure (5m cadence) rows with
    # per-key latest-timestamp-wins semantics. Falls back to legacy single-row
    # query when scheduler_group column is absent.
    try:
        from ..utils.indicator_merge import fetch_merged_indicators
        _merged_by_sym = await fetch_merged_indicators(db, syms_list)

        score_rows = (await db.execute(
            text("""
                SELECT DISTINCT ON (symbol)
                    symbol, score,
                    liquidity_score, market_structure_score,
                    momentum_score, signal_score
                FROM alpha_scores
                WHERE symbol = ANY(:symbols)
                  AND time > now() - interval '2 hours'
                ORDER BY symbol, time DESC
            """),
            {"symbols": syms_list},
        )).fetchall()

    except Exception as exc:
        logger.warning("Pipeline scan: market data fetch failed: %s", exc)
        return None

    # Build flat ind_map from merged dual-scheduler results
    ind_map = {sym: mi.as_flat_dict() for sym, mi in _merged_by_sym.items()}
    score_map = {r.symbol: r for r in score_rows}

    # ── Funnel stats: symbols requested vs. found in market_metadata ─────────
    requested_set = set(symbols)
    found_meta_set = {r.symbol for r in meta_rows}
    missing_meta = requested_set - found_meta_set
    if missing_meta:
        logger.info(
            "[PipelineScan] market_metadata gap: %d/%d symbols have NO metadata "
            "(sample: %s)",
            len(missing_meta), len(requested_set),
            sorted(missing_meta)[:10],
        )

    # Indicator coverage
    has_indicators = set(ind_map.keys())
    missing_ind = found_meta_set - has_indicators
    if missing_ind:
        logger.info(
            "[PipelineScan] indicator gap: %d/%d symbols with metadata have NO indicators "
            "(sample: %s)",
            len(missing_ind), len(found_meta_set),
            sorted(missing_ind)[:10],
        )

    # Score coverage
    has_scores = set(score_map.keys())
    missing_scores = found_meta_set - has_scores
    if missing_scores:
        logger.debug(
            "[PipelineScan] score gap: %d/%d symbols with metadata have NO alpha_score",
            len(missing_scores), len(found_meta_set),
        )

    assets = []
    for row in meta_rows:
        sym = row.symbol
        indicators = ind_map.get(sym, {})
        score_row  = score_map.get(sym)

        assets.append(_build_pipeline_asset(
            sym,
            name=row.name,
            indicators=indicators,
            score_row=score_row,
            has_market_metadata=True,
            price=float(row.price) if row.price else 0.0,
            change_24h=float(row.price_change_24h) if row.price_change_24h else 0.0,
            market_cap=float(row.market_cap) if row.market_cap is not None else None,
            volume_24h=float(row.volume_24h) if row.volume_24h is not None else None,
            spread_pct=float(row.spread_pct) if row.spread_pct is not None else None,
            orderbook_depth_usdt=float(row.orderbook_depth_usdt) if row.orderbook_depth_usdt is not None else None,
        ))

    for sym in sorted(missing_meta):
        indicators = ind_map.get(sym, {})
        score_row = score_map.get(sym)
        assets.append(_build_pipeline_asset(
            sym,
            name=sym,
            indicators=indicators,
            score_row=score_row,
            has_market_metadata=False,
        ))

    return assets


# ─── core indicator completeness guard ───────────────────────────────────────

def _resolve_required_core_indicators() -> tuple[str, ...]:
    """Return the set of core indicators that must be non-null for pipeline advancement.

    Derived from DEFAULT_INDICATORS so the list automatically reflects the active
    indicator config (ZERO HARDCODE: no magic strings here — the source of truth
    is the seed config which is DB-backed via config_profiles).

    Only indicators with both ``enabled=True`` *and* a numeric ``period``
    (i.e. time-series indicators that can legitimately return null during warm-up)
    are included in the mandatory check.
    """
    from ..services.seed_service import DEFAULT_INDICATORS

    # Only the three fundamental indicators required by DEFAULT_SIGNAL are
    # treated as mandatory core checks.  Additional indicators can be added to
    # DEFAULT_INDICATORS without automatically becoming pipeline blockers.
    _CORE_KEYS = ("adx", "rsi", "macd")
    return tuple(
        key for key in _CORE_KEYS
        if DEFAULT_INDICATORS.get(key, {}).get("enabled", False)
    )


#: Resolved at module load time from DEFAULT_INDICATORS.  Re-computed on each
#: import so changes to seed config are picked up without restarting workers.
_REQUIRED_CORE_INDICATORS: tuple[str, ...] = _resolve_required_core_indicators()


def _filter_incomplete_indicators(assets: list) -> tuple[list, list]:
    """Separate assets into (complete, incomplete) based on core indicator presence.

    An asset is *incomplete* if any of the required core indicators (ADX, RSI,
    MACD) is ``None`` or absent from its indicator dict.  Incomplete assets are
    excluded from the pipeline and logged so the issue is visible in production
    logs without raising an exception.

    Returns
    -------
    complete : list
        Assets where all core indicators are non-null.
    incomplete : list
        Assets with at least one null core indicator (quarantined).
    """
    complete: list = []
    incomplete: list = []

    for asset in assets:
        indicators = asset.get("indicators", {})
        missing = [
            ind for ind in _REQUIRED_CORE_INDICATORS
            if indicators.get(ind) is None
        ]
        if missing:
            incomplete.append(asset)
            logger.warning(
                "[PipelineScan] QUARANTINED %s — core indicators null: %s "
                "(asset will not advance until indicators are fully computed)",
                asset.get("symbol", "?"),
                missing,
            )
        else:
            complete.append(asset)

    if incomplete:
        logger.info(
            "[PipelineScan] Core indicator guard: %d/%d assets quarantined "
            "(null ADX/RSI/MACD). Sample: %s",
            len(incomplete), len(assets),
            [a.get("symbol") for a in incomplete[:10]],
        )

    return complete, incomplete


# ─── level evaluators ─────────────────────────────────────────────────────────

def _check_condition_would_fail(cond: dict, actual_value) -> bool:
    """Quick check whether a single filter condition would reject an asset.

    Used only for diagnostic logging — not for actual filtering decisions.
    """
    op_str = cond.get("operator", ">=")
    target = cond.get("value")
    if target is None:
        return False
    try:
        av = float(actual_value) if not isinstance(actual_value, bool) else actual_value
        tv = float(target) if not isinstance(target, bool) else target
        ops = {
            ">=": av >= tv, "<=": av <= tv, ">": av > tv, "<": av < tv,
            "==": av == tv, "=": av == tv, "!=": av != tv,
        }
        return not ops.get(op_str, True)
    except (TypeError, ValueError):
        return False

def _apply_level_filter(
    assets: list,
    profile_config: Optional[dict],
    level: str,
    score_config: Optional[dict] = None,
    apply_profile_filters: bool = True,
) -> tuple[list, list]:
    """
    Apply ProfileEngine filters for a given level.
    Returns (passed, all_scored).

    score_config: when provided, overrides the ProfileEngine's internal score engine
    with the user's global /settings/score configuration.  Profile-level
    Alpha Score Weights are merged in so they are respected.
    """
    from ..services.profile_engine import ProfileEngine
    from ..services.score_engine import ScoreEngine, merge_score_config

    engine = ProfileEngine(profile_config)

    # Merge global scoring rules with profile weights so both are respected
    if score_config:
        merged = merge_score_config(score_config, profile_config)
        engine.score_engine = ScoreEngine(merged)

    min_score = 0.0

    # L2: min alpha score gate
    if level == "L2":
        min_score = float((profile_config or {}).get("filters", {}).get("min_score", 0))

    # ── Diagnostic: analyse rejections per filter condition ────────────────
    filter_conditions = (profile_config or {}).get("filters", {}).get("conditions", [])
    if apply_profile_filters and filter_conditions and len(assets) > 0:
        rejection_counts: dict[str, int] = {}
        null_counts: dict[str, int] = {}

        for asset in assets:
            indicators = asset.get("indicators", {})
            flat = {**asset, **{k: v for k, v in indicators.items() if isinstance(v, (int, float, bool, str))}}
            for cond in filter_conditions:
                field = cond.get("field", "")
                if not field:
                    continue
                val = flat.get(field)
                if val is None:
                    null_counts[field] = null_counts.get(field, 0) + 1
                    if field in _DIAG_STRICT_META:
                        rejection_counts[field + " (NULL→FAIL)"] = rejection_counts.get(field + " (NULL→FAIL)", 0) + 1
                else:
                    if _check_condition_would_fail(cond, val):
                        rejection_counts[field] = rejection_counts.get(field, 0) + 1

        if rejection_counts or null_counts:
            logger.info(
                "[PipelineScan] %s filter diagnostics (%d assets):\n"
                "  NULL fields: %s\n"
                "  Rejection causes: %s",
                level, len(assets),
                {k: f"{v}/{len(assets)}" for k, v in sorted(null_counts.items(), key=lambda x: -x[1])},
                {k: f"{v}/{len(assets)}" for k, v in sorted(rejection_counts.items(), key=lambda x: -x[1])},
            )

    # Apply structural filters.
    # strict_indicators=True: assets with missing indicator data FAIL indicator
    # conditions rather than skipping them.  This prevents assets that have never
    # had indicators computed (e.g. newly-added pool coins) from bypassing EMA /
    # RSI / ADX conditions and incorrectly appearing in pipeline stages.
    filtered = (
        engine._apply_filters(assets, strict_indicators=True)
        if apply_profile_filters
        else list(assets)
    )

    if apply_profile_filters:
        logger.info(
            "[PipelineScan] %s profile filters: %d → %d assets (rejected %d)",
            level, len(assets), len(filtered), len(assets) - len(filtered),
        )
    else:
        logger.info(
            "[PipelineScan] %s monitoring mode: keeping all %d assets visible (profile filters bypassed)",
            level, len(filtered),
        )

    # Compute scores for all passing assets
    scored = []
    below_min_score = 0
    for asset in filtered:
        processed = engine._process_single_asset(asset, include_details=True)
        total = processed.get("score", {}).get("total_score", 0)
        if total >= min_score:
            scored.append({**asset, "_score": total, "_processed": processed})
        else:
            below_min_score += 1

    if below_min_score:
        logger.info(
            "[PipelineScan] %s min_score gate (%.1f): rejected %d/%d filtered assets",
            level, min_score, below_min_score, len(filtered),
        )

    return scored, filtered


def _evaluate_l3_signals(assets: list, profile_config: Optional[dict], score_config: Optional[dict] = None) -> list:
    """
    Apply L3 signal conditions and return triggered assets.

    If the profile has NO signal conditions configured, fall back to scoring-only
    mode: return all assets that passed the profile filters, sorted by score.
    This prevents L3 from being permanently empty just because no signal conditions
    have been set up yet.

    score_config: when provided, overrides the ProfileEngine's internal score engine
    with the user's global /settings/score configuration.  Profile-level
    Alpha Score Weights are merged in so they are respected.
    """
    from ..services.profile_engine import ProfileEngine
    from ..services.score_engine import ScoreEngine, merge_score_config

    engine = ProfileEngine(profile_config)

    # Merge global scoring rules with profile weights so both are respected
    if score_config:
        merged = merge_score_config(score_config, profile_config)
        engine.score_engine = ScoreEngine(merged)

    # Check if the profile has any signal conditions at all.
    # Signal conditions may be stored under 'entry_triggers' OR 'signals'.
    sig_conditions = (
        (profile_config or {}).get("entry_triggers", {}).get("conditions") or
        (profile_config or {}).get("signals", {}).get("conditions") or
        []
    )
    has_signal_conditions = bool(sig_conditions)

    result = engine.process_watchlist(assets, include_details=True)

    if has_signal_conditions:
        # Signal evaluation mode: only return assets with triggered signals
        signals = []
        for asset in result.get("assets", []):
            sig = asset.get("signal", {})
            if sig.get("triggered"):
                signals.append({
                    "symbol":             asset["symbol"],
                    "score":              asset.get("score", {}).get("total_score", 0),
                    "price":              asset.get("price", 0),
                    "change_24h":         asset.get("change_24h", 0),
                    "volume_24h":         asset.get("volume_24h"),
                    "market_cap":         asset.get("market_cap"),
                    "matched_conditions": sig.get("matched_conditions", []),
                })
        signals.sort(key=lambda x: x["score"], reverse=True)
        return signals
    else:
        # No signal conditions — fall back to scoring mode: return all filtered
        # assets sorted by score (same as L2 behavior)
        logger.info(
            "[PipelineScan] L3: no signal conditions in profile — using scoring fallback (%d assets)",
            len(result.get("assets", [])),
        )
        fallback = []
        for asset in result.get("assets", []):
            total = asset.get("score", {}).get("total_score", 0)
            fallback.append({
                "symbol":             asset["symbol"],
                "score":              total,
                "price":              asset.get("price", 0),
                "change_24h":         asset.get("change_24h", 0),
                "volume_24h":         asset.get("volume_24h"),
                "market_cap":         asset.get("market_cap"),
                "matched_conditions": [],
            })
        fallback.sort(key=lambda x: x["score"], reverse=True)
        return fallback


def _jsonable(value):
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _decision_reason_map(processed: dict, has_signal_conditions: bool) -> dict:
    reasons: dict[str, str] = {}

    evaluation = processed.get("_evaluation") or {}
    for rule in evaluation.get("score_matched_rules") or []:
        reason_key = None
        if isinstance(rule, dict):
            reason_key = rule.get("indicator") or rule.get("id")
        elif isinstance(rule, str):
            reason_key = rule
        elif isinstance(rule, (int, float)):
            reason_key = str(rule)
        if isinstance(reason_key, str) and reason_key:
            reasons[reason_key] = "OK"

    signal = processed.get("signal", {})
    for matched in signal.get("matched_conditions", []):
        reasons[str(matched)] = "OK"
    for failed in signal.get("failed_required", []):
        reasons[str(failed)] = "FAIL"

    entry = processed.get("entry", {})
    for matched in entry.get("matched", []):
        reasons[str(matched)] = "OK"
    for failed in entry.get("failed_required", []):
        reasons[str(failed)] = "FAIL"

    if processed.get("blocked"):
        reasons["block_rules"] = "FAIL"
    if processed.get("passed_filter") is False:
        for failed in processed.get("filter_failed", []):
            reasons[str(failed)] = "FAIL"
    if not has_signal_conditions and processed.get("passed_filter"):
        reasons.setdefault("scoring_fallback", "OK")

    if not reasons:
        reasons["pipeline"] = "OK" if processed.get("passed_filter") else "FAIL"
    return reasons


def _decision_metrics(asset: dict, processed: dict) -> dict:
    score = processed.get("score", {}) or {}
    metrics = {
        **(asset.get("indicators") or {}),
        "price": asset.get("price"),
        "change_24h": asset.get("change_24h"),
        "volume_24h": asset.get("volume_24h"),
        "market_cap": asset.get("market_cap"),
        "score_components": score.get("components", {}),
        "score_classification": score.get("classification"),
        "signal_direction": processed.get("signal", {}).get("direction"),
    }
    return _jsonable(metrics)


def _evaluate_l3_decisions(
    assets: list,
    profile_config: Optional[dict],
    strategy_level: str,
    score_config: Optional[dict] = None,
) -> list[dict]:
    from ..services.profile_engine import ProfileEngine
    from ..services.score_engine import ScoreEngine, merge_score_config

    engine = ProfileEngine(profile_config)
    if score_config:
        merged = merge_score_config(score_config, profile_config)
        engine.score_engine = ScoreEngine(merged)

    sig_conditions = (
        (profile_config or {}).get("entry_triggers", {}).get("conditions")
        or (profile_config or {}).get("signals", {}).get("conditions")
        or []
    )
    has_signal_conditions = bool(sig_conditions)
    timeframe = (profile_config or {}).get("default_timeframe", "5m")

    decisions: list[dict] = []
    for asset in assets:
        started_at = datetime.now(timezone.utc)
        processed = engine.evaluate_asset(asset)
        latency_ms = int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000)
        score = (processed.get("score") or {}).get("total_score", 0)
        decision = "BLOCK"
        l3_pass = False

        if not processed.get("blocked") and processed.get("passed_filter", False):
            if has_signal_conditions:
                l3_pass = bool(processed.get("signal", {}).get("triggered"))
                decision = "ALLOW" if l3_pass else "BLOCK"
            else:
                l3_pass = True
                decision = "ALLOW"

        decisions.append({
            "symbol": asset.get("symbol"),
            "strategy": strategy_level,
            "timeframe": timeframe,
            "score": score,
            "decision": decision,
            "l1_pass": True,
            "l2_pass": True,
            "l3_pass": l3_pass,
            "reasons": _decision_reason_map(processed, has_signal_conditions),
            "metrics": _decision_metrics(asset, processed),
            "latency_ms": latency_ms,
            "direction": asset.get("futures_direction"),
            "created_at": datetime.now(timezone.utc),
            "_processed": processed,
            "_asset": asset,
        })

    return decisions


async def _persist_decision_logs(db, user_id, decisions: list[dict]):
    from ..models.backoffice import DecisionLog
    from sqlalchemy import text

    if not decisions:
        return []

    # DEDUPLICATION: Check for recent duplicate decisions (last 5 minutes)
    # This prevents duplicate logging from multiple pipeline cycles
    now = datetime.now(timezone.utc)
    recent_window = now - timedelta(minutes=5)

    # Build dedup query to check for existing recent decisions
    dedup_checks = []
    for decision in decisions:
        symbol = decision["symbol"]
        strategy = decision["strategy"]
        direction = decision.get("direction")

        dedup_checks.append((symbol, strategy, direction))

    # Query for existing decisions in the recent window
    if dedup_checks:
        # Create a set to check uniquely
        unique_checks = list(set(dedup_checks))

        existing_result = await db.execute(text("""
            SELECT DISTINCT symbol, strategy, direction
            FROM decisions_log
            WHERE created_at >= :recent_window
              AND (symbol, strategy, COALESCE(direction, '')) IN :checks
        """), {
            "recent_window": recent_window,
            "checks": tuple((s, st, d or "") for s, st, d in unique_checks)
        })

        existing_decisions = {
            (row.symbol, row.strategy, row.direction or None)
            for row in existing_result.fetchall()
        }

        # Filter out duplicates
        decisions_to_insert = []
        skipped_count = 0
        for decision in decisions:
            key = (
                decision["symbol"],
                decision["strategy"],
                decision.get("direction")
            )
            if key in existing_decisions:
                logger.debug(
                    "[Decision] SKIP duplicate: %s | strategy=%s | direction=%s (logged in last 5 min)",
                    key[0], key[1], key[2] or "—"
                )
                skipped_count += 1
            else:
                decisions_to_insert.append(decision)

        if skipped_count > 0:
            logger.info(
                "[Decision] Deduplication: skipped %d duplicate(s), inserting %d new decision(s)",
                skipped_count, len(decisions_to_insert)
            )

        # Use filtered list
        decisions = decisions_to_insert

    if not decisions:
        return []

    rows = [
        DecisionLog(
            symbol=decision["symbol"],
            strategy=decision["strategy"],
            timeframe=decision.get("timeframe"),
            score=decision.get("score"),
            decision=decision["decision"],
            l1_pass=decision.get("l1_pass"),
            l2_pass=decision.get("l2_pass"),
            l3_pass=decision.get("l3_pass"),
            reasons=decision.get("reasons"),
            metrics=decision.get("metrics"),
            latency_ms=decision.get("latency_ms"),
            direction=decision.get("direction"),
            event_type=decision.get("event_type"),
            user_id=user_id,
            created_at=decision.get("created_at"),
        )
        for decision in decisions
    ]
    db.add_all(rows)
    await db.flush()

    payloads = []
    for row in rows:
        payload = {
            "id": row.id,
            "symbol": row.symbol,
            "strategy": row.strategy,
            "timeframe": row.timeframe,
            "score": row.score,
            "decision": row.decision,
            "l1_pass": row.l1_pass,
            "l2_pass": row.l2_pass,
            "l3_pass": row.l3_pass,
            "reasons": row.reasons or {},
            "metrics": row.metrics or {},
            "latency_ms": row.latency_ms,
            "direction": row.direction,
            "event_type": row.event_type,
            "created_at": row.created_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        logger.info(
            "[Decision] PERSISTED | id=%s | %s | score=%s | %s | event=%s",
            row.id, row.symbol, round(float(row.score or 0), 2), row.decision, row.event_type or "—",
        )
        payloads.append(payload)

    # Log summary
    logger.info(
        "[Decision] Batch persisted: %d decision(s) successfully logged to decisions_log table",
        len(payloads)
    )

    return payloads


async def _run_staleness_only(
    db,
    watchlist_id: str,
    filters_json: dict | None = None,
    execution_id: Optional[str] = None,
):
    """Run ONLY staleness expiry + cleanup — no active/down marking.

    Called when a pipeline scan cannot fetch market data, so we don't want to
    wipe the watchlist. Instead, we only expire assets whose refreshed_at is
    older than staleness_minutes (default 30 min).
    """
    from sqlalchemy import text
    now = datetime.now(timezone.utc)

    staleness_minutes = int((filters_json or {}).get("staleness_minutes", _DEFAULT_STALENESS_MINUTES))
    staleness_cutoff = now - timedelta(minutes=staleness_minutes)
    stale_result = await db.execute(text("""
        UPDATE pipeline_watchlist_assets
        SET level_direction = 'down',
            level_change_at = :now,
            execution_id = :execution_id
        WHERE watchlist_id = :wid
          AND level_direction IS NULL
          AND refreshed_at IS NOT NULL
          AND refreshed_at < :cutoff
        RETURNING symbol
    """), {"wid": watchlist_id, "now": now, "cutoff": staleness_cutoff, "execution_id": execution_id})
    stale_rows = stale_result.fetchall()
    if stale_rows:
        logger.info(
            "[PipelineScan] Staleness-only expiry (%d min): marked %d assets as 'down' "
            "in watchlist %s (no market data): %s",
            staleness_minutes, len(stale_rows), watchlist_id,
            [r.symbol for r in stale_rows],
        )

    # Cleanup: remove 'down' records older than 2h
    await db.execute(text("""
        DELETE FROM pipeline_watchlist_assets
        WHERE watchlist_id = :wid
          AND level_direction = 'down'
          AND level_change_at < now() - interval '2 hours'
    """), {"wid": watchlist_id})

    await db.commit()


def _apply_robust_authoritative_scoring(
    assets: list,
    *,
    score_config: dict | None,
    is_futures: bool,
) -> dict[str, int]:
    """Phase 2 — for bucketed symbols replace the legacy score with the
    confidence-weighted robust score and tag ``engine_tag`` on each asset.

    Mutates each asset dict in place:
      * Always sets ``engine_tag`` to ``"robust"`` or ``"legacy"`` so the
        upsert path can persist the column for the asset row.
      * For bucketed-and-robust-succeeded assets, overrides ``_score`` /
        ``alpha_score`` (spot) and ``confidence_score`` /
        ``score_long`` / ``score_short`` (futures) with the robust score.
        For futures, ``score_long`` / ``score_short`` are scaled in the
        same direction as the legacy split so the entry gate (LONG vs
        SHORT) keeps the legacy direction signal but the magnitude is
        re-grounded on the validated confidence-weighted total.
      * For bucketed-but-failed assets, leaves the legacy score alone
        and bumps the silent-fallback counter (already done inside
        ``select_authoritative_score``).

    Returns counters: ``{"bucketed", "robust_used", "fallbacks", "legacy"}``
    so the caller can log a single "rollout summary" line per scan.
    """
    from ..services.robust_indicators import select_authoritative_score

    counters = {"bucketed": 0, "robust_used": 0, "fallbacks": 0, "legacy": 0}
    if not assets:
        return counters

    for asset in assets:
        symbol = asset.get("symbol")
        if not symbol:
            continue
        indicators = asset.get("indicators") or {}
        legacy_score = (
            asset.get("confidence_score")
            if is_futures and asset.get("confidence_score") is not None
            else asset.get("_score", asset.get("score"))
        )
        flow_hint = (
            indicators.get("taker_source")
            if isinstance(indicators, dict) else None
        )
        result = select_authoritative_score(
            str(symbol),
            indicators,
            legacy_score=legacy_score,
            score_config=score_config,
            flow_source_hint=flow_hint,
        )

        asset["engine_tag"] = result.engine_tag
        if result.bucketed:
            counters["bucketed"] += 1
            if result.fell_back:
                counters["fallbacks"] += 1
            else:
                counters["robust_used"] += 1
        else:
            counters["legacy"] += 1

        if result.engine_tag != "robust" or result.score is None:
            continue

        new_score = float(result.score)
        if is_futures:
            old_long = asset.get("score_long")
            old_short = asset.get("score_short")
            old_conf = asset.get("confidence_score")
            asset["confidence_score"] = round(new_score, 2)
            # Preserve direction: scale LONG/SHORT proportionally so the
            # entry gate keeps the legacy direction signal but its
            # magnitude is grounded on the robust score.
            if old_conf and old_conf > 0:
                ratio = new_score / float(old_conf)
            else:
                ratio = 1.0
            if old_long is not None:
                asset["score_long"] = round(
                    max(0.0, min(100.0, float(old_long) * ratio)), 2
                )
            if old_short is not None:
                asset["score_short"] = round(
                    max(0.0, min(100.0, float(old_short) * ratio)), 2
                )
        # For both spot and futures, override the canonical alpha_score
        # column read by downstream consumers (evaluate_signals, UI, etc.)
        asset["_score"] = new_score
        asset["score"] = new_score
        asset["alpha_score"] = new_score
        if result.robust_score is not None:
            asset["_robust_score"] = result.robust_score.to_dict()

    return counters


async def _replace_rejection_snapshot(
    db,
    watchlist_id: str,
    user_id,
    profile_id,
    rows: list[dict],
    execution_id: Optional[str] = None,
):
    from sqlalchemy import text

    await db.execute(
        text("DELETE FROM pipeline_watchlist_rejections WHERE watchlist_id = :wid"),
        {"wid": watchlist_id},
    )
    now = datetime.now(timezone.utc)
    for row in rows:
        await db.execute(text("""
            INSERT INTO pipeline_watchlist_rejections (
                id, watchlist_id, user_id, profile_id, symbol, stage,
                failed_type, failed_indicator, condition_text, current_value,
                expected_value, evaluation_trace, analysis_snapshot, recorded_at, execution_id,
                engine_tag
            )
            VALUES (
                :id, :watchlist_id, :user_id, :profile_id, :symbol, :stage,
                :failed_type, :failed_indicator, :condition_text, CAST(:current_value AS jsonb),
                :expected_value, CAST(:evaluation_trace AS jsonb), CAST(:analysis_snapshot AS jsonb),
                :recorded_at, :execution_id, :engine_tag
            )
        """), {
            "id": str(uuid4()),
            "watchlist_id": watchlist_id,
            "user_id": str(user_id),
            "profile_id": str(profile_id) if profile_id else None,
            "symbol": row["symbol"],
            "stage": row["stage"],
            "failed_type": row["failed_type"],
            "failed_indicator": row["failed_indicator"],
            "condition_text": row["condition"],
            "current_value": json.dumps(_jsonable(row.get("current_value"))),
            "expected_value": row.get("expected"),
            "evaluation_trace": json.dumps(_jsonable(row.get("evaluation_trace") or [])),
            "analysis_snapshot": json.dumps(_jsonable(row.get("analysis_snapshot") or {})),
            "recorded_at": now,
            "execution_id": execution_id,
            "engine_tag": row.get("engine_tag"),
        })


# ─── Futures scoring injection ────────────────────────────────────────────────

def _tag_futures_scores(
    assets: list,
    watchlist_level: str,
    scoring_futures: Optional[dict] = None,
) -> None:
    """Mutate each asset dict to add futures score fields.

    Reads the pre-computed ``indicators`` sub-dict (populated by
    ``_build_pipeline_asset`` from the ``indicators`` table) and adds
    (in BLOCK > ENTRY > SCORE priority order):
      block_both            — shared BLOCK gate (ADX < adx_min); pre-empts both dirs
      entry_long_blocked    — ENTRY gate LONG (includes block_both)
      entry_short_blocked   — ENTRY gate SHORT (includes block_both)
      score_long, score_short, confidence_score  — SCORE layer (display)
      futures_direction     — LONG | SHORT | NEUTRAL (L3 only); None for non-L3

    Called only when ``wl.market_mode == 'futures'``.
    Direction is 'LONG' | 'SHORT' | 'NEUTRAL' for L3;
    None for non-L3 (not yet evaluated at that stage).

    Parameters
    ----------
    scoring_futures:
        Optional sub-dict from profile/global config with futures-specific
        thresholds (direction_gap_min, entry_adx_min, etc.).  If None,
        defaults defined in ``score_futures`` are used.
    """
    try:
        from ..scoring.futures_pipeline_scorer import score_futures
    except ImportError as exc:
        logger.warning("[PipelineScan] futures scorer unavailable: %s", exc)
        return

    for asset in assets:
        ind = asset.get("indicators") or {}
        try:
            result = score_futures(
                ind,
                watchlist_level=watchlist_level,
                scoring_futures=scoring_futures,
            )
            asset["score_long"]          = result["score_long"]
            asset["score_short"]         = result["score_short"]
            asset["confidence_score"]    = result["confidence_score"]
            asset["futures_direction"]   = result["futures_direction"]
            # BLOCK > ENTRY priority: block_both is the shared BLOCK gate
            asset["block_both"]          = result["block_both"]
            asset["entry_long_blocked"]  = result["entry_long_blocked"]
            asset["entry_short_blocked"] = result["entry_short_blocked"]
        except Exception as exc:
            logger.debug("[PipelineScan] futures scorer error for %s: %s", asset.get("symbol"), exc)


# ─── DB upsert ────────────────────────────────────────────────────────────────

async def _upsert_assets(
    db,
    watchlist_id: str,
    assets: list,
    filters_json: dict | None = None,
    execution_id: Optional[str] = None,
):
    """Upsert current pipeline_watchlist_assets snapshot for a watchlist.

    Symbols in `assets` → INSERT or UPDATE (level_direction stays/becomes NULL).
    Symbols previously saved but not in `assets` → UPDATE level_direction = 'down'.
    Records with level_direction = 'down' older than 2h are cleaned up.
    If filters_json contains max_stay_minutes, assets older than that are expired.
    Staleness expiry: assets not refreshed in staleness_minutes (default 30) are marked 'down'.
    """
    from sqlalchemy import text

    now = datetime.now(timezone.utc)

    if assets:
        # Upsert active symbols (preserve entered_at on conflict, update refreshed_at)
        for a in assets:
            await db.execute(text("""
                INSERT INTO pipeline_watchlist_assets
                    (id, watchlist_id, symbol, current_price, price_change_24h,
                      volume_24h, market_cap, alpha_score, entered_at, refreshed_at,
                      level_direction, analysis_snapshot, execution_id,
                      score_long, score_short, confidence_score,
                      futures_direction, entry_long_blocked, entry_short_blocked,
                      engine_tag)
                VALUES
                    (gen_random_uuid(), :wid, :sym, :price, :chg,
                     :vol, :mc, :score, :now, :now, NULL, CAST(:analysis_snapshot AS jsonb), :execution_id,
                     :score_long, :score_short, :confidence_score,
                     :futures_direction, :entry_long_blocked, :entry_short_blocked,
                     :engine_tag)
                ON CONFLICT (watchlist_id, symbol)
                DO UPDATE SET
                    current_price       = EXCLUDED.current_price,
                    price_change_24h    = EXCLUDED.price_change_24h,
                    volume_24h          = EXCLUDED.volume_24h,
                    market_cap          = EXCLUDED.market_cap,
                    alpha_score         = EXCLUDED.alpha_score,
                    refreshed_at        = EXCLUDED.refreshed_at,
                    level_direction     = NULL,
                    analysis_snapshot   = EXCLUDED.analysis_snapshot,
                    execution_id        = EXCLUDED.execution_id,
                    score_long          = EXCLUDED.score_long,
                    score_short         = EXCLUDED.score_short,
                    confidence_score    = EXCLUDED.confidence_score,
                    futures_direction   = EXCLUDED.futures_direction,
                    entry_long_blocked  = EXCLUDED.entry_long_blocked,
                    entry_short_blocked = EXCLUDED.entry_short_blocked,
                    engine_tag          = EXCLUDED.engine_tag
            """), {
                "wid":                watchlist_id,
                "sym":                a["symbol"],
                "price":              a.get("price"),
                "chg":                a.get("change_24h"),
                "vol":                a.get("volume_24h"),
                "mc":                 a.get("market_cap"),
                "score":              a.get("_score", a.get("score")),
                "analysis_snapshot":  json.dumps(_jsonable(a.get("analysis_snapshot") or {})),
                "now":                now,
                "execution_id":       execution_id,
                "score_long":         a.get("score_long"),
                "score_short":        a.get("score_short"),
                "confidence_score":   a.get("confidence_score"),
                "futures_direction":  a.get("futures_direction"),
                "entry_long_blocked": bool(a.get("entry_long_blocked", False)),
                "entry_short_blocked": bool(a.get("entry_short_blocked", False)),
                "engine_tag":         a.get("engine_tag"),
            })

        # Mark symbols that are no longer passing as 'down'
        active_syms = [a["symbol"] for a in assets]
        await db.execute(
            text("""
                UPDATE pipeline_watchlist_assets
                SET level_direction = 'down',
                    level_change_at = :now,
                    execution_id = :execution_id
                WHERE watchlist_id = :wid
                  AND NOT (symbol = ANY(:active_syms))
                  AND (level_direction IS NULL OR level_direction != 'down')
            """),
            {"wid": watchlist_id, "now": now, "active_syms": active_syms, "execution_id": execution_id},
        )

    else:
        # No assets passed — mark all as 'down'
        await db.execute(text("""
            UPDATE pipeline_watchlist_assets
            SET level_direction = 'down',
                level_change_at = :now,
                execution_id = :execution_id
            WHERE watchlist_id = :wid
              AND (level_direction IS NULL OR level_direction != 'down')
        """), {"wid": watchlist_id, "now": now, "execution_id": execution_id})

    # Expire assets that have exceeded max_stay_minutes (GUI-configurable per watchlist)
    max_stay = (filters_json or {}).get("max_stay_minutes")
    if max_stay:
        cutoff = now - timedelta(minutes=int(max_stay))
        await db.execute(text("""
            UPDATE pipeline_watchlist_assets
            SET level_direction = 'down',
                level_change_at = :now,
                execution_id = :execution_id
            WHERE watchlist_id = :wid
              AND level_direction IS NULL
              AND entered_at < :cutoff
        """), {"wid": watchlist_id, "now": now, "cutoff": cutoff, "execution_id": execution_id})

    # Staleness expiry: assets not re-confirmed by a pipeline scan within
    # staleness_minutes (default 30 min) are marked 'down'.
    # This prevents assets from lingering when the scan skips due to
    # missing market data or upstream failures.
    staleness_minutes = int((filters_json or {}).get("staleness_minutes", _DEFAULT_STALENESS_MINUTES))
    staleness_cutoff = now - timedelta(minutes=staleness_minutes)
    stale_result = await db.execute(text("""
        UPDATE pipeline_watchlist_assets
        SET level_direction = 'down',
            level_change_at = :now,
            execution_id = :execution_id
        WHERE watchlist_id = :wid
          AND level_direction IS NULL
          AND refreshed_at IS NOT NULL
          AND refreshed_at < :cutoff
        RETURNING symbol
    """), {"wid": watchlist_id, "now": now, "cutoff": staleness_cutoff, "execution_id": execution_id})
    stale_rows = stale_result.fetchall()
    if stale_rows:
        logger.info(
            "[PipelineScan] Staleness expiry (%d min): marked %d assets as 'down' in watchlist %s: %s",
            staleness_minutes, len(stale_rows), watchlist_id,
            [r.symbol for r in stale_rows],
        )

    # Cleanup: remove 'down' records older than 2h to keep the table lean
    await db.execute(text("""
        DELETE FROM pipeline_watchlist_assets
        WHERE watchlist_id = :wid
          AND level_direction = 'down'
          AND level_change_at < now() - interval '2 hours'
    """), {"wid": watchlist_id})

    await db.commit()


async def validate_pipeline_integrity(
    db,
    *,
    wl_rows: list,
    profile_config_map: dict,
    execution_id: str,
) -> dict[str, Any]:
    from sqlalchemy import text

    if not wl_rows:
        return {"violations": 0, "corrected": 0}

    watchlist_ids = [str(wl.id) for wl in wl_rows]
    active_rows = (await db.execute(
        text("""
            SELECT watchlist_id::text AS watchlist_id, symbol
            FROM pipeline_watchlist_assets
            WHERE watchlist_id::text = ANY(:watchlist_ids)
              AND (level_direction IS NULL OR level_direction = 'up')
        """),
        {"watchlist_ids": watchlist_ids},
    )).fetchall()

    symbols_by_watchlist: dict[str, set[str]] = {wid: set() for wid in watchlist_ids}
    for row in active_rows:
        symbols_by_watchlist.setdefault(row.watchlist_id, set()).add(row.symbol)

    wl_map = {str(wl.id): wl for wl in wl_rows}
    violations = 0
    corrected = 0
    for wl_id, wl in wl_map.items():
        wl_level = effective_pipeline_level(
            wl.level,
            source_pool_id=wl.source_pool_id,
            profile_config=profile_config_map.get(wl.profile_id),
        )
        if wl_level not in {"L1", "L2", "L3"}:
            continue

        parent_id = str(wl.source_watchlist_id) if wl.source_watchlist_id else None
        if not parent_id:
            continue
        parent_symbols = symbols_by_watchlist.get(parent_id, set())
        child_symbols = symbols_by_watchlist.get(wl_id, set())
        invalid_symbols = sorted(child_symbols - parent_symbols)
        if not invalid_symbols:
            continue

        for symbol in invalid_symbols:
            _log_pipeline_event(
                level=wl_level,
                execution_id=execution_id,
                event_type="PIPELINE_VIOLATION",
                watchlist_id=wl_id,
                symbol=symbol,
                reason="integrity_check_not_in_upstream",
            )
        violations += len(invalid_symbols)

        await db.execute(
            text("""
                UPDATE pipeline_watchlist_assets
                SET level_direction = 'down',
                    level_change_at = :now,
                    execution_id = :execution_id
                WHERE watchlist_id::text = :watchlist_id
                  AND symbol = ANY(:symbols)
                  AND (level_direction IS NULL OR level_direction = 'up')
            """),
            {
                "now": datetime.now(timezone.utc),
                "execution_id": execution_id,
                "watchlist_id": wl_id,
                "symbols": invalid_symbols,
            },
        )
        corrected += len(invalid_symbols)

    await db.commit()
    return {"violations": violations, "corrected": corrected}


# ─── WebSocket broadcast ──────────────────────────────────────────────────────

async def _broadcast_pipeline_update(
    watchlist_id: str,
    watchlist_name: str,
    level: str,
    new_symbols: list,
    all_signals: list,
):
    """Broadcast new L3 signals via the 'signals' WebSocket channel."""
    try:
        from ..api.websocket import manager
        from datetime import datetime, timezone

        payload = {
            "type":           "pipeline_signal",
            "level":          level,
            "watchlist_id":   watchlist_id,
            "watchlist_name": watchlist_name,
            "new_signals":    new_symbols,
            "all_signals":    all_signals[:20],  # cap at 20 for WS payload
            "ts":             datetime.now(timezone.utc).isoformat(),
        }

        await manager.broadcast("signals", payload)
        logger.info(
            "[PipelineScan] Broadcast %d new L3 signals for watchlist %s",
            len(new_symbols), watchlist_name,
        )
    except Exception as exc:
        logger.warning("[PipelineScan] WebSocket broadcast failed: %s", exc)


async def _broadcast_scan_funnel(
    watchlist_id: str,
    watchlist_name: str,
    level: str,
    pool_total: int,
    with_metadata: int,
    profile_candidates: int,
    after_profile_filter: int,
    after_blocking: int,
):
    """Broadcast scan funnel stats via 'pipeline' WebSocket channel for frontend diagnostics."""
    try:
        from ..api.websocket import manager

        payload = {
            "type":           "scan_funnel",
            "level":          level,
            "watchlist_id":   watchlist_id,
            "watchlist_name": watchlist_name,
            "funnel": {
                "pool_total":            pool_total,
                "with_metadata":         with_metadata,
                "no_metadata":           pool_total - with_metadata,
                "profile_candidates":    profile_candidates,
                "after_profile_filter":  after_profile_filter,
                "rejected_by_profile":   max(0, profile_candidates - after_profile_filter),
                "after_blocking":        after_blocking,
                "blocked":               after_profile_filter - after_blocking,
            },
            "ts": datetime.now(timezone.utc).isoformat(),
        }

        await manager.broadcast("pipeline", payload)
    except Exception as exc:
        logger.debug("[PipelineScan] Funnel broadcast failed: %s", exc)


# ─── core async pipeline ──────────────────────────────────────────────────────

async def _run_pipeline_scan():
    from ..database import CeleryAsyncSessionLocal as AsyncSessionLocal
    from ..init_db import backfill_execution_tracking_columns
    from ..models.pipeline_watchlist import PipelineWatchlist
    from ..models.pool import PoolCoin
    from ..models.profile import Profile
    from sqlalchemy import select, text
    from ..utils.symbol_filters import filter_real_assets

    redis = _get_redis()
    execution_id = str(uuid4())
    stats = {"watchlists": 0, "new_signals": 0, "errors": 0, "funnels": [], "execution_id": execution_id}

    # Robust-indicators (Phase 1) shadow tasks accumulate here and are
    # awaited once at the very end of the scan so they run *concurrently*
    # with subsequent watchlist iterations rather than blocking each one.
    _shadow_tasks: list = []

    async with AsyncSessionLocal() as db:
        global _PIPELINE_EXECUTION_TRACKING_SCHEMA_READY
        if not _PIPELINE_EXECUTION_TRACKING_SCHEMA_READY:
            async with _PIPELINE_EXECUTION_TRACKING_SCHEMA_LOCK:
                if not _PIPELINE_EXECUTION_TRACKING_SCHEMA_READY:
                    await backfill_execution_tracking_columns(db)
                    await db.commit()
                    _PIPELINE_EXECUTION_TRACKING_SCHEMA_READY = True

        # Load all pipeline watchlists with auto_refresh=true
        wl_rows = (await db.execute(
            select(PipelineWatchlist).where(PipelineWatchlist.auto_refresh == True)
        )).scalars().all()

        if not wl_rows:
            logger.debug("[PipelineScan] No pipeline watchlists with auto_refresh — skipping.")
            return stats

        # Materialise watchlists into primitive snapshots BEFORE any further
        # work. The session is guaranteed healthy here (we just loaded the
        # rows). After this point, the per-watchlist loop and integrity check
        # operate exclusively on these snapshots and never touch ORM
        # attributes — so a rollback in any iteration cannot expire fields
        # that subsequent iterations need, which eliminates the
        # MissingGreenlet lazy-load on aborted sessions and the resulting
        # InFailedSQLTransactionError cascade (Task #114).
        wl_snapshots = [
            SimpleNamespace(
                id=wl.id,
                name=wl.name,
                level=wl.level,
                market_mode=wl.market_mode,
                profile_id=wl.profile_id,
                source_pool_id=wl.source_pool_id,
                source_watchlist_id=wl.source_watchlist_id,
                user_id=wl.user_id,
                filters_json=wl.filters_json,
                created_at=wl.created_at,
            )
            for wl in wl_rows
        ]

        profile_ids = {wl.profile_id for wl in wl_snapshots if wl.profile_id}
        profile_config_map = {}
        if profile_ids:
            profile_rows = (await db.execute(
                select(Profile).where(Profile.id.in_(profile_ids))
            )).scalars().all()
            profile_config_map = {row.id: row.config for row in profile_rows}

        wl_snapshots.sort(
            key=lambda wl: (
                WATCHLIST_STAGE_ORDER.get(
                    effective_pipeline_level(
                        wl.level,
                        source_pool_id=wl.source_pool_id,
                        profile_config=profile_config_map.get(wl.profile_id),
                    ),
                    len(WATCHLIST_STAGE_ORDER),
                ),
                wl.created_at or datetime.min.replace(tzinfo=timezone.utc),
            )
        )

        logger.info(
            "[PipelineScan] Processing %d pipeline watchlists… execution_id=%s",
            len(wl_snapshots),
            execution_id,
        )

        stage_buckets: dict[str, list] = {stage: [] for stage in (*_PIPELINE_EXECUTION_ORDER, "custom")}
        effective_level_map: dict[str, str] = {}
        pool_gate_watchlist_map: dict[tuple[str, str], str] = {}
        for _wl in wl_snapshots:
            _eff = effective_pipeline_level(
                _wl.level,
                source_pool_id=_wl.source_pool_id,
                profile_config=profile_config_map.get(_wl.profile_id),
            )
            effective_level_map[str(_wl.id)] = _eff
            stage_buckets.setdefault(_eff, []).append(_wl)
            if _eff == "POOL" and _wl.source_pool_id:
                pool_gate_watchlist_map[(str(_wl.user_id), str(_wl.source_pool_id))] = str(_wl.id)

        for stage in (*_PIPELINE_EXECUTION_ORDER, "custom"):
            for wl in stage_buckets.get(stage, []):
                # `wl` is a SimpleNamespace primitive snapshot — never an ORM
                # object — so attribute access here cannot trigger lazy-load
                # IO and cannot raise MissingGreenlet (Task #114).
                wl_id = str(wl.id)
                try:
                    stats["watchlists"] += 1
                    level = (wl.level or "L1").upper()
                    profile_config = profile_config_map.get(wl.profile_id) if wl.profile_id else None
                    effective_level = effective_pipeline_level(
                        level,
                        source_pool_id=wl.source_pool_id,
                        profile_config=profile_config,
                    )
                    filters_json = wl.filters_json or {}

                    source_watchlist_level = (
                        effective_level_map.get(str(wl.source_watchlist_id))
                        if wl.source_watchlist_id
                        else None
                    )
                    pool_gate_watchlist_id = None
                    if effective_level == "L1" and wl.source_pool_id:
                        pool_gate_watchlist_id = pool_gate_watchlist_map.get(
                            (str(wl.user_id), str(wl.source_pool_id))
                        )
                        if pool_gate_watchlist_id == wl_id:
                            pool_gate_watchlist_id = None
                    dependency = resolve_pipeline_dependency(
                        level=effective_level,
                        source_pool_id=wl.source_pool_id,
                        source_watchlist_id=wl.source_watchlist_id,
                        source_watchlist_level=source_watchlist_level,
                        pool_gate_watchlist_id=pool_gate_watchlist_id,
                    )
                    source_pool_id = dependency["source_pool_id"]
                    source_watchlist_id = dependency["source_watchlist_id"]
                    if dependency["error"]:
                        logger.error(
                            {
                                "type": "INVALID_SOURCE_CONFIG",
                                "watchlist_id": wl_id,
                                "level": effective_level,
                                "execution_id": execution_id,
                                "message": "Missing explicit upstream dependency for pipeline stage.",
                                "expected_upstream_level": dependency["expected_upstream_level"],
                                "source_pool_id": str(wl.source_pool_id) if wl.source_pool_id else None,
                                "source_watchlist_id": str(wl.source_watchlist_id) if wl.source_watchlist_id else None,
                                "source_watchlist_level": source_watchlist_level,
                                "error": dependency["error"],
                            }
                        )
                    elif dependency["resolution"] == "implicit_pool_gate":
                        logger.info(
                            "[PipelineScan] %s (%s): resolved legacy POOL dependency via watchlist %s",
                            wl.name,
                            effective_level,
                            source_watchlist_id,
                        )

                    def _normalize_sym(s: str) -> str:
                        s = s.upper().strip()
                        if "_" not in s and s.endswith("USDT"):
                            return s[:-4] + "_USDT"
                        return s

                    symbols: list[str] = []
                    upstream_symbols: set[str] = set()

                    if source_watchlist_id:
                        upstream_rows = (await db.execute(text("""
                            SELECT symbol
                            FROM pipeline_watchlist_assets
                            WHERE watchlist_id = :wid
                              AND (level_direction IS NULL OR level_direction = 'up')
                            ORDER BY alpha_score DESC NULLS LAST
                        """), {"wid": source_watchlist_id})).fetchall()
                        symbols = filter_real_assets([_normalize_sym(r.symbol) for r in upstream_rows])
                        upstream_symbols = set(symbols)
                        logger.info(
                            "[PipelineScan] %s (%s): upstream watchlist %s → %d symbols",
                            wl.name, effective_level, source_watchlist_id, len(symbols),
                        )
                    elif source_pool_id:
                        coin_rows = (await db.execute(
                            select(PoolCoin).where(
                                PoolCoin.pool_id == source_pool_id,
                                PoolCoin.is_active == True,
                            )
                        )).scalars().all()
                        symbols = filter_real_assets([_normalize_sym(c.symbol) for c in coin_rows])
                        upstream_symbols = set(symbols)
                        logger.info(
                            "[PipelineScan] %s (%s): pool %s → %d symbols",
                            wl.name, effective_level, source_pool_id, len(symbols),
                        )

                    if effective_level in {"L1", "L2", "L3"}:
                        symbols = _intersect_with_upstream(
                            symbols=symbols,
                            upstream_symbols=upstream_symbols,
                            level=effective_level,
                            watchlist_id=wl_id,
                            execution_id=execution_id,
                        )
                        assert set(symbols).issubset(upstream_symbols)

                    if not symbols:
                        if source_watchlist_id:
                            # Upstream watchlist was consulted and approved 0 symbols.
                            # Immediately clear all active assets in this stage so the
                            # downstream reflects the upstream's 0-approved state.
                            logger.info(
                                "[PipelineScan] %s (%s): upstream watchlist %s approved 0 symbols — clearing active assets.",
                                wl.name, effective_level, source_watchlist_id,
                            )
                            await _upsert_assets(db, wl_id, [], filters_json, execution_id=execution_id)
                        else:
                            logger.info(
                                "[PipelineScan] %s (%s): no symbols from upstream — running staleness check.",
                                wl.name, effective_level,
                            )
                            await _run_staleness_only(db, wl_id, filters_json, execution_id=execution_id)
                        await _update_last_scanned(db, wl_id)
                        continue

                    assets = await _fetch_market_data(db, symbols)
                    if assets is None or not assets:
                        logger.warning(
                            "[PipelineScan] %s (%s): no market data available — running staleness check.",
                            wl.name, effective_level,
                        )
                        await _run_staleness_only(db, wl_id, filters_json, execution_id=execution_id)
                        await _update_last_scanned(db, wl_id)
                        continue

                    # Mandatory core-indicator completeness guard.
                    # Assets with null ADX, RSI, or MACD are quarantined here and
                    # never allowed to advance to any pipeline stage (POOL → L3).
                    assets, quarantined = _filter_incomplete_indicators(assets)
                    if quarantined and effective_level in {"POOL", "L1", "L2", "L3"}:
                        logger.info(
                            "[PipelineScan] %s (%s): %d asset(s) quarantined for null core indicators "
                            "and excluded from this scan cycle.",
                            wl.name, effective_level, len(quarantined),
                        )

                    assets_with_metadata = sum(1 for a in assets if a.get("_has_market_metadata"))
                    profile_candidate_count = len(assets)

                    score_config: Optional[dict] = None
                    # Best-effort score config read.  Wrapped in a SAVEPOINT so a
                    # DB-level failure (e.g. timeout, missing column) only rolls
                    # back the savepoint and leaves the parent session healthy
                    # for the rest of this watchlist's writes.  Without this,
                    # asyncpg's poisoned-tx state cascades to _upsert_assets
                    # below and ultimately to validate_pipeline_integrity at the
                    # end of the cycle (Task #125).
                    from ..services.seed_service import DEFAULT_SCORE
                    try:
                        from ..services.config_service import config_service
                        async with db.begin_nested():
                            score_config = await config_service.get_config(db, "score", wl.user_id)
                        if not score_config:
                            score_config = DEFAULT_SCORE
                    except Exception as _sc_exc:
                        logger.warning(
                            "[PipelineScan] %s: score config read failed (%s) — falling back to DEFAULT_SCORE",
                            wl.name, _sc_exc,
                        )
                        score_config = DEFAULT_SCORE

                    # Futures mode: compute dual LONG/SHORT scores for all assets.
                    # Must run BEFORE upsert so scores are persisted to DB.
                    # Direction: LONG|SHORT|NEUTRAL at L3; None for non-L3 (pre-rating).
                    # Placed after score_config load so scoring_futures config is available.
                    is_futures = getattr(wl, "market_mode", "spot") == "futures"
                    if is_futures and assets:
                        # scoring_futures key must be present in config; if absent, degrade
                        # silently to spot behavior rather than running with unknown defaults.
                        futures_cfg = (score_config or {}).get("scoring_futures")
                        if futures_cfg is None:
                            logger.info(
                                "[PipelineScan] %s: scoring_futures config absent — "
                                "skipping futures scoring (spot fallback)",
                                wl.name,
                            )
                        else:
                            _tag_futures_scores(assets, effective_level, scoring_futures=futures_cfg)
                            logger.info(
                                "[PipelineScan] %s (%s): tagged futures scores on %d assets",
                                wl.name, effective_level, len(assets),
                            )

                    # ── Robust indicators (Phase 1) — shadow mode ─────────
                    # Best-effort, gated by USE_ROBUST_INDICATORS. We
                    # snapshot the asset list *now* (so subsequent legacy
                    # mutations can't race the shadow path) and dispatch
                    # the work as a background asyncio task so legacy scan
                    # progress is never blocked. The whole block is wrapped
                    # in try/except so any failure here can NEVER cascade
                    # into the legacy pipeline.
                    try:
                        from ..services.robust_indicators import is_shadow_enabled as _is_shadow_enabled
                        if _is_shadow_enabled() and assets:
                            from ..services.robust_indicators import run_shadow_scan
                            from ..database import CeleryAsyncSessionLocal as _ShadowSessionLocal
                            indicators_cfg: dict = {}
                            try:
                                from ..services.config_service import config_service
                                async with db.begin_nested():
                                    indicators_cfg = (
                                        await config_service.get_config(db, "indicators", wl.user_id)
                                    ) or {}
                            except Exception:
                                indicators_cfg = {}
                            # Capture immutable copies for the background task.
                            shadow_assets = [dict(a) for a in assets]
                            shadow_score_cfg = dict(score_config or {})
                            shadow_user_id = wl.user_id
                            shadow_wl_id = wl_id
                            shadow_level = effective_level
                            # Phase 1: shadow mode runs for both spot AND
                            # futures watchlists. The futures asset dicts
                            # carry score_long/score_short/confidence_score
                            # which run_shadow_scan picks up via its
                            # _legacy_score helper for divergence bucketing.
                            shadow_market_mode = getattr(wl, "market_mode", None) or "spot"

                            async def _shadow_runner():
                                # Use an isolated session so a parent-tx
                                # rollback can never poison the shadow write.
                                try:
                                    async with _ShadowSessionLocal() as shadow_db:
                                        await run_shadow_scan(
                                            shadow_db,
                                            assets=shadow_assets,
                                            score_config=shadow_score_cfg,
                                            indicators_config=indicators_cfg,
                                            user_id=shadow_user_id,
                                            watchlist_id=shadow_wl_id,
                                            watchlist_level=shadow_level,
                                            market_mode=shadow_market_mode,
                                        )
                                        await shadow_db.commit()
                                except Exception as _shadow_inner:
                                    logger.debug(
                                        "[PipelineScan] shadow runner failed for %s: %s",
                                        shadow_wl_id, _shadow_inner,
                                    )

                            _shadow_tasks.append(asyncio.create_task(_shadow_runner()))
                    except Exception as _shadow_exc:
                        logger.debug(
                            "[PipelineScan] %s (%s): shadow scan dispatch failed (%s) — "
                            "legacy pipeline unaffected",
                            wl.name, effective_level, _shadow_exc,
                        )

                    # ── Robust indicators (Phase 2) — gradual rollout ─────
                    # Apply the deterministic per-symbol bucketing AFTER
                    # the shadow snapshot so divergence comparisons keep
                    # seeing the original legacy score. For symbols in the
                    # rollout bucket the robust confidence-weighted score
                    # becomes authoritative on the asset dict; downstream
                    # rejection/upsert/UI all read from the mutated dict.
                    try:
                        rollout_counters = _apply_robust_authoritative_scoring(
                            assets,
                            score_config=score_config,
                            is_futures=is_futures,
                        )
                        if rollout_counters["bucketed"] or rollout_counters["fallbacks"]:
                            logger.info(
                                "[PipelineScan] %s (%s): rollout — bucketed=%d "
                                "robust_used=%d fallbacks=%d legacy=%d",
                                wl.name, effective_level,
                                rollout_counters["bucketed"],
                                rollout_counters["robust_used"],
                                rollout_counters["fallbacks"],
                                rollout_counters["legacy"],
                            )
                    except Exception as _rollout_exc:
                        logger.warning(
                            "[PipelineScan] %s (%s): robust rollout step failed "
                            "(%s) — legacy scores retained",
                            wl.name, effective_level, _rollout_exc,
                        )

                    if effective_level == "custom":
                        existing_symbols = {a.get("symbol") for a in assets}
                        missing_symbols = [sym for sym in symbols if sym not in existing_symbols]
                        if missing_symbols:
                            assets.extend([_placeholder_asset_without_market_data(sym) for sym in missing_symbols])
                        monitored, _ = _apply_level_filter(
                            assets,
                            profile_config,
                            effective_level,
                            score_config=score_config,
                            apply_profile_filters=False,
                        )
                        await _replace_rejection_snapshot(
                            db, wl_id, wl.user_id, wl.profile_id, [], execution_id=execution_id
                        )
                        await _upsert_assets(db, wl_id, monitored, filters_json, execution_id=execution_id)
                        await _update_last_scanned(db, wl_id)
                        continue

                    if effective_level in ("POOL", "L1", "L2"):
                        effective_profile_config = profile_config
                        selected_filter_conditions = None
                        if profile_config:
                            filter_cfg = (profile_config.get("filters") or {})
                            selected = select_profile_filter_conditions(
                                filter_cfg.get("conditions"),
                                total_symbols=len(symbols),
                                symbols_with_meta=assets_with_metadata,
                            )
                            selected_filter_conditions = selected["conditions"]
                            if selected["relaxed_strict_meta"]:
                                effective_profile_config = {
                                    **profile_config,
                                    "filters": {**filter_cfg, "conditions": selected["conditions"]},
                                }

                        profile_passed, rejected_rows = evaluate_rejections(
                            assets,
                            profile_config=effective_profile_config,
                            stage=effective_level,
                            profile_id=str(wl.profile_id) if wl.profile_id else None,
                            selected_filter_conditions=selected_filter_conditions,
                        )
                        _log_stage_processing_summary(
                            level=effective_level,
                            input_count=len(symbols),
                            approved_count=len(profile_passed),
                            rejected_count=len(rejected_rows),
                            watchlist_id=wl_id,
                            execution_id=execution_id,
                        )
                        passed, _ = _apply_level_filter(
                            profile_passed,
                            effective_profile_config,
                            effective_level,
                            score_config=score_config,
                            apply_profile_filters=False,
                        )
                        await _replace_rejection_snapshot(
                            db,
                            wl_id,
                            wl.user_id,
                            wl.profile_id,
                            rejected_rows,
                            execution_id=execution_id,
                        )

                        if effective_level in {"L1", "L2"}:
                            normalized_passed = []
                            for asset in passed:
                                symbol = asset.get("symbol")
                                if symbol in upstream_symbols:
                                    normalized_passed.append(asset)
                                else:
                                    _log_pipeline_event(
                                        level=effective_level,
                                        execution_id=execution_id,
                                        event_type="PIPELINE_VIOLATION",
                                        watchlist_id=wl_id,
                                        symbol=symbol,
                                        reason="persist_not_in_upstream",
                                    )
                            passed = normalized_passed
                            assert {a.get("symbol") for a in passed}.issubset(upstream_symbols)

                        await _broadcast_scan_funnel(
                            wl_id, wl.name, effective_level,
                            pool_total=len(symbols),
                            with_metadata=assets_with_metadata,
                            profile_candidates=profile_candidate_count,
                            after_profile_filter=len(profile_passed),
                            after_blocking=len(passed),
                        )
                        await _upsert_assets(db, wl_id, passed, filters_json, execution_id=execution_id)
                        await _update_last_scanned(db, wl_id)
                        continue

                    # L3
                    profile_passed, rejected_rows = evaluate_rejections(
                        assets,
                        profile_config=profile_config,
                        stage=effective_level,
                        profile_id=str(wl.profile_id) if wl.profile_id else None,
                    )
                    await _replace_rejection_snapshot(
                        db,
                        wl_id,
                        wl.user_id,
                        wl.profile_id,
                        rejected_rows,
                        execution_id=execution_id,
                    )
                    decisions = _evaluate_l3_decisions(
                        profile_passed,
                        profile_config,
                        level,
                        score_config=score_config,
                    )
                    signals = [
                        {
                            "symbol": decision["symbol"],
                            "score": decision.get("score", 0),
                            "price": decision["_asset"].get("price", 0),
                            "change_24h": decision["_asset"].get("change_24h", 0),
                            "volume_24h": decision["_asset"].get("volume_24h"),
                            "market_cap": decision["_asset"].get("market_cap"),
                            "analysis_snapshot": decision["_asset"].get("analysis_snapshot") or {},
                            "matched_conditions": decision["_processed"].get("signal", {}).get("matched_conditions", []),
                            # Futures scores — non-None only when is_futures and _tag_futures_scores ran
                            "score_long":          decision["_asset"].get("score_long"),
                            "score_short":         decision["_asset"].get("score_short"),
                            "confidence_score":    decision["_asset"].get("confidence_score"),
                            "futures_direction":   decision["_asset"].get("futures_direction"),
                            "entry_long_blocked":  decision["_asset"].get("entry_long_blocked", False),
                            "entry_short_blocked": decision["_asset"].get("entry_short_blocked", False),
                        }
                        for decision in decisions
                        if decision["decision"] == "ALLOW"
                    ]
                    normalized_signals = []
                    for asset in signals:
                        symbol = asset.get("symbol")
                        if symbol in upstream_symbols:
                            normalized_signals.append(asset)
                        else:
                            _log_pipeline_event(
                                level="L3",
                                execution_id=execution_id,
                                event_type="PIPELINE_VIOLATION",
                                watchlist_id=wl_id,
                                symbol=symbol,
                                reason="persist_not_in_upstream",
                            )
                    signals = normalized_signals
                    assert {a.get("symbol") for a in signals}.issubset(upstream_symbols)

                    current_set = {s["symbol"] for s in signals}
                    prior_set = _prior_signals(redis, wl_id)
                    new_syms = sorted(current_set - prior_set)

                    _save_signals(redis, wl_id, current_set)

                    # ── Decision Log deduplication ────────────────────────────
                    from ..services.seed_service import DEFAULT_DECISION_LOG as _DL_DEFAULTS
                    dl_score_delta = float(_DL_DEFAULTS.get("score_delta_threshold", 5.0))
                    dl_direction_logs = bool(_DL_DEFAULTS.get("direction_change_logs", True))
                    # Best-effort decision-log config read.  SAVEPOINT-wrapped
                    # for the same reason as the score config above (Task #125):
                    # a swallowed exception here used to poison the parent tx
                    # and cascade into the next _upsert_assets call.
                    try:
                        from ..services.config_service import config_service
                        async with db.begin_nested():
                            _dl_cfg = await config_service.get_config(db, "decision_log", wl.user_id)
                        if isinstance(_dl_cfg, dict):
                            dl_score_delta = float(_dl_cfg.get("score_delta_threshold", dl_score_delta))
                            dl_direction_logs = bool(_dl_cfg.get("direction_change_logs", dl_direction_logs))
                    except Exception as _dl_cfg_exc:
                        logger.warning(
                            "[PipelineScan] %s: decision_log config read failed (%s) — using defaults",
                            wl.name, _dl_cfg_exc,
                        )

                    prior_states = _prior_decision_states(redis, wl_id)
                    new_states: dict = {}
                    decisions_to_log: list = []
                    for d in decisions:
                        sym = d.get("symbol")
                        prior = prior_states.get(sym)
                        # Warn when recovering a symbol stuck due to ordering bug
                        if prior and prior.get("state") == "ALLOW" and not prior.get("db_confirmed_at"):
                            logger.warning(
                                "[Decision] Recovering unconfirmed ALLOW state for %s in watchlist %s",
                                sym, wl_id,
                            )
                        should_log, event_type = _should_log_decision(
                            d, prior,
                            score_delta_threshold=dl_score_delta,
                            direction_change_logs=dl_direction_logs,
                        )
                        new_states[sym] = {
                            "state": d.get("decision"),
                            "score": d.get("score"),
                            "direction": d.get("direction"),
                            "saved_at": datetime.now(timezone.utc).isoformat(),
                            # Preserve db_confirmed_at from prior for filtered symbols
                            "db_confirmed_at": prior.get("db_confirmed_at") if prior else None,
                        }
                        if should_log:
                            d["event_type"] = event_type
                            decisions_to_log.append(d)
                    # ─────────────────────────────────────────────────────────
                    # IMPORTANT: persist to DB FIRST, then update Redis.
                    # If DB fails, Redis must NOT advance — otherwise the symbol
                    # gets stuck as ALLOW with no DB record and is silently
                    # filtered forever (ordering bug, Task #109).
                    #
                    # The decision log INSERT is wrapped in a SAVEPOINT so that
                    # a DB-level failure (e.g. missing columns from migration 026)
                    # only rolls back the savepoint and leaves the parent session
                    # healthy for _upsert_assets / _update_last_scanned below.
                    decision_payloads = []
                    try:
                        async with db.begin_nested():
                            decision_payloads = await _persist_decision_logs(db, wl.user_id, decisions_to_log)
                            # Stamp db_confirmed_at on each successfully persisted symbol
                            if decisions_to_log:
                                _confirmed_at = datetime.now(timezone.utc).isoformat()
                                for _d in decisions_to_log:
                                    _sym = _d.get("symbol")
                                    if _sym in new_states:
                                        new_states[_sym]["db_confirmed_at"] = _confirmed_at
                    except Exception as _dl_exc:
                        logger.error(
                            "FATAL: Decision persistence failed for watchlist %s: %s "
                            "— verify migration 026 (direction/event_type columns) is applied",
                            wl_id, _dl_exc, exc_info=True
                        )
                        # CRITICAL: Re-raise exception to prevent silent failure
                        raise RuntimeError(
                            f"Decision persistence failed for watchlist {wl_id}: {_dl_exc}"
                        ) from _dl_exc
                    _save_decision_states(redis, wl_id, new_states)
                    await _upsert_assets(db, wl_id, signals, filters_json, execution_id=execution_id)
                    await _update_last_scanned(db, wl_id)

                    if decision_payloads:
                        from ..api.websocket import broadcast_decision_created
                        for payload in decision_payloads:
                            await broadcast_decision_created(payload)

                    if new_syms:
                        stats["new_signals"] += len(new_syms)
                        await _broadcast_pipeline_update(
                            watchlist_id=wl_id,
                            watchlist_name=wl.name,
                            level="L3",
                            new_symbols=new_syms,
                            all_signals=signals,
                        )

                except Exception as exc:
                    logger.exception("[PipelineScan] Error processing watchlist %s: %s", wl.name, exc)
                    stats["errors"] += 1
                    # Roll back any failed transaction so subsequent watchlists
                    # are not affected by an InFailedSQLTransactionError cascade.
                    # Surface rollback failures at WARNING — they used to be
                    # silently swallowed and were the entry point for the
                    # cascade tracked in Task #125.
                    try:
                        await db.rollback()
                    except Exception as _rb_exc:
                        logger.warning(
                            "[PipelineScan] %s: rollback after watchlist failure raised %s: %s "
                            "— session may be unusable for subsequent watchlists",
                            wl.name, type(_rb_exc).__name__, _rb_exc,
                        )
                    continue

        # Run the integrity check on a *fresh* session.  Defense-in-depth: even
        # if the per-watchlist loop accidentally leaks an aborted-tx state into
        # the loop session, a brand-new session for integrity guarantees the
        # final SELECT/UPDATE pair won't see InFailedSQLTransactionError
        # (Task #125).
        async with AsyncSessionLocal() as integrity_db:
            stats["integrity"] = await validate_pipeline_integrity(
                integrity_db,
                wl_rows=wl_snapshots,
                profile_config_map=profile_config_map,
                execution_id=execution_id,
            )

    # Drain any in-flight shadow tasks. Bounded by a generous timeout so a
    # single hung shadow run can't stall the next pipeline tick.
    if _shadow_tasks:
        try:
            done, pending = await asyncio.wait(_shadow_tasks, timeout=30)
            if pending:
                logger.info(
                    "[PipelineScan] %d shadow task(s) still running after 30s — leaving in background",
                    len(pending),
                )
        except Exception as _gather_exc:
            logger.debug("[PipelineScan] shadow gather failed: %s", _gather_exc)

    logger.info(
        "[PipelineScan] Done — watchlists=%d  new_signals=%d  errors=%d",
        stats["watchlists"], stats["new_signals"], stats["errors"],
    )
    return stats


# ─── Celery task ──────────────────────────────────────────────────────────────

@celery_app.task(name="app.tasks.pipeline_scan.scan", bind=True, max_retries=0)
def scan(self):
    """Periodic pipeline scan — L1 filter → L2 ranking → L3 signals (5 min)."""
    logger.info("[PipelineScan] Starting pipeline scan…")
    try:
        result = _run_async(_run_pipeline_scan())
        logger.info("[PipelineScan] Result: %s", result)
        return result
    except Exception as exc:
        logger.exception("[PipelineScan] Fatal error: %s", exc)
        raise
