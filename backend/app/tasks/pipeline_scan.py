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
from typing import Optional
from uuid import uuid4

from ..tasks.celery_app import celery_app
from ..services.pipeline_rejections import evaluate_rejections
from ..utils.pipeline_profile_filters import (
    STRICT_META_FIELDS,
    effective_pipeline_level,
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

def _uses_pipeline_filters(level: Optional[str]) -> bool:
    """Only POOL/L1/L2/L3 are filter-enforced pipeline stages."""
    return uses_pipeline_filters(level)


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

    # Step 2: Fetch indicators — always runs regardless of which meta path was taken
    try:
        # Prefer 5m indicators (fresh, 5-min cadence); fall back to any timeframe
        ind_rows = (await db.execute(
            text("""
                SELECT DISTINCT ON (symbol) symbol, indicators_json
                FROM indicators
                WHERE symbol = ANY(:symbols)
                  AND timeframe = '5m'
                ORDER BY symbol, time DESC
            """),
            {"symbols": syms_list},
        )).fetchall()

        found_syms = {r.symbol for r in ind_rows}
        missing = [s for s in symbols if s not in found_syms]
        if missing:
            fallback_rows = (await db.execute(
                text("""
                    SELECT DISTINCT ON (symbol) symbol, indicators_json
                    FROM indicators
                    WHERE symbol = ANY(:symbols)
                    ORDER BY symbol, time DESC
                """),
                {"symbols": missing},
            )).fetchall()
            ind_rows = list(ind_rows) + list(fallback_rows)

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

    ind_map   = {r.symbol: (r.indicators_json or {}) for r in ind_rows}
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
            "created_at": datetime.now(timezone.utc),
            "_processed": processed,
            "_asset": asset,
        })

    return decisions


async def _persist_decision_logs(db, user_id, decisions: list[dict]):
    from ..models.backoffice import DecisionLog

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
            "created_at": row.created_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        logger.info("[Decision] %s | score=%s | %s", row.symbol, round(float(row.score or 0), 2), row.decision)
        payloads.append(payload)
    return payloads


async def _run_staleness_only(db, watchlist_id: str, filters_json: dict | None = None):
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
            level_change_at = :now
        WHERE watchlist_id = :wid
          AND level_direction IS NULL
          AND refreshed_at IS NOT NULL
          AND refreshed_at < :cutoff
        RETURNING symbol
    """), {"wid": watchlist_id, "now": now, "cutoff": staleness_cutoff})
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


async def _replace_rejection_snapshot(
    db,
    watchlist_id: str,
    user_id,
    profile_id,
    rows: list[dict],
):
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
                expected_value, evaluation_trace, recorded_at
            )
            VALUES (
                :id, :watchlist_id, :user_id, :profile_id, :symbol, :stage,
                :failed_type, :failed_indicator, :condition_text, CAST(:current_value AS jsonb),
                :expected_value, CAST(:evaluation_trace AS jsonb), :recorded_at
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
            "recorded_at": now,
        })


# ─── DB upsert ────────────────────────────────────────────────────────────────

async def _upsert_assets(db, watchlist_id: str, assets: list, filters_json: dict | None = None):
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
                     level_direction)
                VALUES
                    (gen_random_uuid(), :wid, :sym, :price, :chg,
                     :vol, :mc, :score, :now, :now, NULL)
                ON CONFLICT (watchlist_id, symbol)
                DO UPDATE SET
                    current_price    = EXCLUDED.current_price,
                    price_change_24h = EXCLUDED.price_change_24h,
                    volume_24h       = EXCLUDED.volume_24h,
                    market_cap       = EXCLUDED.market_cap,
                    alpha_score      = EXCLUDED.alpha_score,
                    refreshed_at     = EXCLUDED.refreshed_at,
                    level_direction  = NULL
            """), {
                "wid":   watchlist_id,
                "sym":   a["symbol"],
                "price": a.get("price"),
                "chg":   a.get("change_24h"),
                "vol":   a.get("volume_24h"),
                "mc":    a.get("market_cap"),
                "score": a.get("_score", a.get("score")),
                "now":   now,
            })

        # Mark symbols that are no longer passing as 'down'
        active_syms = [a["symbol"] for a in assets]
        await db.execute(
            text("""
                UPDATE pipeline_watchlist_assets
                SET level_direction = 'down',
                    level_change_at = :now
                WHERE watchlist_id = :wid
                  AND NOT (symbol = ANY(:active_syms))
                  AND (level_direction IS NULL OR level_direction != 'down')
            """),
            {"wid": watchlist_id, "now": now, "active_syms": active_syms},
        )

    else:
        # No assets passed — mark all as 'down'
        await db.execute(text("""
            UPDATE pipeline_watchlist_assets
            SET level_direction = 'down',
                level_change_at = :now
            WHERE watchlist_id = :wid
              AND (level_direction IS NULL OR level_direction != 'down')
        """), {"wid": watchlist_id, "now": now})

    # Expire assets that have exceeded max_stay_minutes (GUI-configurable per watchlist)
    max_stay = (filters_json or {}).get("max_stay_minutes")
    if max_stay:
        cutoff = now - timedelta(minutes=int(max_stay))
        await db.execute(text("""
            UPDATE pipeline_watchlist_assets
            SET level_direction = 'down',
                level_change_at = :now
            WHERE watchlist_id = :wid
              AND level_direction IS NULL
              AND entered_at < :cutoff
        """), {"wid": watchlist_id, "now": now, "cutoff": cutoff})

    # Staleness expiry: assets not re-confirmed by a pipeline scan within
    # staleness_minutes (default 30 min) are marked 'down'.
    # This prevents assets from lingering when the scan skips due to
    # missing market data or upstream failures.
    staleness_minutes = int((filters_json or {}).get("staleness_minutes", _DEFAULT_STALENESS_MINUTES))
    staleness_cutoff = now - timedelta(minutes=staleness_minutes)
    stale_result = await db.execute(text("""
        UPDATE pipeline_watchlist_assets
        SET level_direction = 'down',
            level_change_at = :now
        WHERE watchlist_id = :wid
          AND level_direction IS NULL
          AND refreshed_at IS NOT NULL
          AND refreshed_at < :cutoff
        RETURNING symbol
    """), {"wid": watchlist_id, "now": now, "cutoff": staleness_cutoff})
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
    from ..models.pipeline_watchlist import PipelineWatchlist
    from ..models.pool import Pool, PoolCoin
    from ..models.profile import Profile
    from sqlalchemy import select

    redis = _get_redis()
    stats = {"watchlists": 0, "new_signals": 0, "errors": 0, "funnels": []}

    async with AsyncSessionLocal() as db:
        # Load all pipeline watchlists with auto_refresh=true
        wl_rows = (await db.execute(
            select(PipelineWatchlist).where(PipelineWatchlist.auto_refresh == True)
        )).scalars().all()

        if not wl_rows:
            logger.debug("[PipelineScan] No pipeline watchlists with auto_refresh — skipping.")
            return stats

        profile_ids = {wl.profile_id for wl in wl_rows if wl.profile_id}
        profile_config_map = {}
        if profile_ids:
            profile_rows = (await db.execute(
                select(Profile).where(Profile.id.in_(profile_ids))
            )).scalars().all()
            profile_config_map = {row.id: row.config for row in profile_rows}

        wl_rows.sort(
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

        logger.info("[PipelineScan] Processing %d pipeline watchlists…", len(wl_rows))

        for wl in wl_rows:
            try:
                stats["watchlists"] += 1
                wl_id = str(wl.id)
                level = (wl.level or "L1").upper()
                filters_json = wl.filters_json or {}

                # ── 1. Resolve symbol universe ────────────────────────────────
                symbols: list[str] = []

                def _normalize_sym(s: str) -> str:
                    """Normalize symbol to BTC_USDT format (add underscore if missing)."""
                    s = s.upper().strip()
                    if "_" not in s and s.endswith("USDT"):
                        return s[:-4] + "_USDT"
                    return s

                if wl.source_pool_id:
                    # Pool origin: use pool_coins
                    from ..utils.symbol_filters import filter_real_assets
                    coin_rows = (await db.execute(
                        select(PoolCoin).where(
                            PoolCoin.pool_id == wl.source_pool_id,
                            PoolCoin.is_active == True,
                        )
                    )).scalars().all()
                    # Normalize symbols to BTC_USDT format (market_metadata uses underscores)
                    raw_syms = [_normalize_sym(c.symbol) for c in coin_rows]
                    symbols = filter_real_assets(raw_syms)
                    logger.info(
                        "[PipelineScan] %s (%s): pool %s → %d raw coins → %d after leveraged-token filter",
                        wl.name, level, wl.source_pool_id, len(raw_syms), len(symbols),
                    )

                elif wl.source_watchlist_id:
                    # Upstream watchlist: use only ACTIVE pipeline_watchlist_assets
                    # ('down' assets are stale — excluded to prevent ghost data in L2/L3)
                    from sqlalchemy import text
                    from ..utils.symbol_filters import filter_real_assets
                    asset_rows = (await db.execute(text("""
                        SELECT symbol FROM pipeline_watchlist_assets
                        WHERE watchlist_id = :wid
                          AND (level_direction IS NULL OR level_direction = 'up')
                        ORDER BY alpha_score DESC NULLS LAST
                    """), {"wid": str(wl.source_watchlist_id)})).fetchall()
                    symbols = filter_real_assets([_normalize_sym(r.symbol) for r in asset_rows])
                    logger.info(
                        "[PipelineScan] %s (%s): upstream watchlist %s → %d symbols",
                        wl.name, level, wl.source_watchlist_id, len(symbols),
                    )

                if not symbols:
                    # L1 fallback: when no source is configured, use all
                    # active coins across the user's pools as the scan universe.
                    if level == "L1" and not wl.source_pool_id and not wl.source_watchlist_id:
                        from ..utils.symbol_filters import filter_real_assets as _filt
                        all_coins = (await db.execute(
                            select(PoolCoin)
                            .join(Pool, PoolCoin.pool_id == Pool.id)
                            .where(
                                Pool.user_id == wl.user_id,
                                Pool.is_active == True,
                                PoolCoin.is_active == True,
                            )
                        )).scalars().all()
                        symbols = _filt([c.symbol for c in all_coins])
                        if symbols:
                            logger.info(
                                "[PipelineScan] %s (L1): no source configured — using "
                                "%d coins from all user pools as fallback universe.",
                                wl.name, len(symbols),
                            )

                if not symbols:
                    logger.info(
                        "[PipelineScan] %s (%s): no symbols from upstream — "
                        "running staleness check on existing assets.",
                        wl.name, level,
                    )
                    await _run_staleness_only(db, wl_id, filters_json)
                    await _update_last_scanned(db, wl_id)
                    continue

                # ── 2. Fetch market data ──────────────────────────────────────
                assets = await _fetch_market_data(db, symbols)
                if assets is None:
                    logger.warning("[PipelineScan] %s (%s): market data fetch error — running staleness check.", wl.name, level)
                    await _run_staleness_only(db, wl_id, filters_json)
                    await _update_last_scanned(db, wl_id)
                    continue
                if not assets:
                    logger.warning(
                        "[PipelineScan] %s (%s): no market data found for %d requested symbols "
                        "(sample: %s) — running staleness check on existing assets.",
                        wl.name, level, len(symbols), symbols[:5],
                    )
                    # Don't wipe immediately, but DO run staleness expiry so
                    # assets that haven't been refreshed in staleness_minutes
                    # get marked 'down' instead of lingering forever.
                    await _run_staleness_only(db, wl_id, filters_json)
                    await _update_last_scanned(db, wl_id)
                    continue

                logger.info(
                    "[PipelineScan] %s (%s) FUNNEL: %d pool symbols → %d with market_metadata (%d lost)",
                    wl.name, level, len(symbols), len(assets), len(symbols) - len(assets),
                )

                # Quick data quality summary for diagnostics
                n_has_mcap = sum(1 for a in assets if a.get("market_cap") is not None)
                n_has_vol = sum(1 for a in assets if a.get("volume_24h") is not None)
                n_has_spread = sum(1 for a in assets if a.get("spread_pct") is not None)
                n_has_depth = sum(1 for a in assets if a.get("orderbook_depth_usdt") is not None)
                n_has_ind = sum(1 for a in assets if a.get("indicators"))
                logger.info(
                    "[PipelineScan] %s (%s) DATA COVERAGE of %d assets: "
                    "market_cap=%d, volume_24h=%d, spread_pct=%d, depth=%d, indicators=%d",
                    wl.name, level, len(assets),
                    n_has_mcap, n_has_vol, n_has_spread, n_has_depth, n_has_ind,
                )
                assets_with_metadata = sum(1 for a in assets if a.get("_has_market_metadata"))
                profile_candidate_count = len(assets)

                # ── 3. Load profile config ────────────────────────────────────
                profile_config: Optional[dict] = None
                if wl.profile_id:
                    prof = (await db.execute(
                        select(Profile).where(Profile.id == wl.profile_id)
                    )).scalars().first()
                    if prof:
                        # .config always holds filters/signals conditions; preset_ia_config is IA metadata only
                        profile_config = prof.config

                # ── 3b. Load global score config (/settings/score) ────────────
                # This ensures Alpha Score respects the user's configured scoring rules.
                score_config: Optional[dict] = None
                try:
                    from ..services.config_service import config_service
                    from ..services.seed_service import DEFAULT_SCORE
                    score_config = await config_service.get_config(db, "score", wl.user_id)
                    if not score_config:
                        score_config = DEFAULT_SCORE
                except Exception:
                    from ..services.seed_service import DEFAULT_SCORE
                    score_config = DEFAULT_SCORE

                # ── 4. Per-level evaluation ───────────────────────────────────
                # Source-pool watchlists with profile filter conditions should be
                # treated as POOL even if legacy data stored them as "custom".
                # Pure custom boards without filter conditions remain monitoring
                # boards and keep all pool assets visible.
                effective_level = effective_pipeline_level(
                    level,
                    source_pool_id=wl.source_pool_id,
                    profile_config=profile_config,
                )
                if effective_level == "POOL" and not _uses_pipeline_filters(level):
                    logger.info(
                        "[PipelineScan] %s (%s): source-pool watchlist promoted to POOL so universe filter conditions are enforced.",
                        wl.name,
                        level,
                    )

                if effective_level == "custom":
                    existing_symbols = {a.get("symbol") for a in assets}
                    missing_symbols = [sym for sym in symbols if sym not in existing_symbols]
                    if missing_symbols:
                        assets.extend([_placeholder_asset_without_market_data(sym) for sym in missing_symbols])
                        logger.info(
                            "[PipelineScan] %s (%s): appended %d symbols without market data so the monitoring board shows the full pool",
                            wl.name, level, len(missing_symbols),
                        )
                    await _replace_rejection_snapshot(db, wl_id, user_id, wl.profile_id, [])

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
                        if selected["relaxed_strict_meta"]:
                            effective_profile_config = {
                                **profile_config,
                                "filters": {
                                    **filter_cfg,
                                    "conditions": selected["conditions"],
                                },
                            }
                            selected_filter_conditions = selected["conditions"]
                            logger.info(
                                "[PipelineScan] %s (%s): sparse market data coverage %.1f%% (%d/%d) — "
                                "ignoring %d strict meta conditions for this pass",
                                wl.name, level,
                                selected["coverage_ratio"] * 100,
                                assets_with_metadata, len(symbols),
                                len(selected["strict_meta_conditions"]),
                            )
                        else:
                            selected_filter_conditions = selected["conditions"]

                    profile_passed, rejected_rows = evaluate_rejections(
                        assets,
                        profile_config=effective_profile_config,
                        stage=effective_level,
                        profile_id=str(wl.profile_id) if wl.profile_id else None,
                        selected_filter_conditions=selected_filter_conditions,
                    )
                    passed, _ = _apply_level_filter(
                        profile_passed,
                        effective_profile_config,
                        effective_level,
                        score_config=score_config,
                        # Block rules + filters are already applied above via
                        # evaluate_rejections so this pass only computes scores
                        # and enforces downstream score-based gates.
                        apply_profile_filters=False,
                    )
                    profile_rejected = len(rejected_rows)
                    await _replace_rejection_snapshot(db, wl_id, user_id, wl.profile_id, rejected_rows)

                    # ── FUNNEL SUMMARY ────────────────────────────────────────
                    logger.info(
                        "[PipelineScan] ═══ %s (%s) FUNNEL SUMMARY ═══\n"
                        "  Pool symbols:       %d\n"
                        "  With market data:   %d  (-%d no metadata)\n"
                        "  Profile candidates: %d\n"
                        "  After profile rules:%d  (-%d rejected)\n"
                        "  After score gate:   %d\n"
                        "  ═══════════════════════════",
                        wl.name, level,
                        len(symbols),
                        assets_with_metadata, len(symbols) - assets_with_metadata,
                        profile_candidate_count,
                        len(profile_passed), profile_rejected,
                        len(passed),
                    )

                    # Broadcast funnel stats for frontend diagnostic panel
                    await _broadcast_scan_funnel(
                        wl_id, wl.name, level,
                        pool_total=len(symbols),
                        with_metadata=assets_with_metadata,
                        profile_candidates=profile_candidate_count,
                        after_profile_filter=len(profile_passed),
                        after_blocking=len(passed),
                    )

                    stats["funnels"].append({
                        "watchlist": wl.name, "level": level,
                        "pool_total": len(symbols),
                        "with_metadata": assets_with_metadata,
                        "profile_candidates": profile_candidate_count,
                        "after_profile_filter": len(profile_passed),
                        "after_blocking": len(passed),
                    })

                    await _upsert_assets(db, wl_id, passed, filters_json)
                    await _update_last_scanned(db, wl_id)

                elif effective_level == "L3":
                    profile_passed, rejected_rows = evaluate_rejections(
                        assets,
                        profile_config=profile_config,
                        stage=effective_level,
                        profile_id=str(wl.profile_id) if wl.profile_id else None,
                    )
                    await _replace_rejection_snapshot(db, wl_id, user_id, wl.profile_id, rejected_rows)
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
                            "matched_conditions": decision["_processed"].get("signal", {}).get("matched_conditions", []),
                        }
                        for decision in decisions
                        if decision["decision"] == "ALLOW"
                    ]

                    logger.info(
                        "[PipelineScan] ═══ %s (L3) FUNNEL SUMMARY ═══\n"
                        "  From upstream:      %d\n"
                        "  With market data:   %d\n"
                        "  Rejected by rules:  %d\n"
                        "  After signals:      %d\n"
                        "  ═══════════════════════════",
                        wl.name,
                        len(symbols), assets_with_metadata,
                        len(rejected_rows), len(signals),
                    )

                    # ── 5. Detect new signals ─────────────────────────────────
                    current_set = {s["symbol"] for s in signals}
                    prior_set   = _prior_signals(redis, wl_id)
                    new_syms    = sorted(current_set - prior_set)

                    _save_signals(redis, wl_id, current_set)
                    decision_payloads = await _persist_decision_logs(db, wl.user_id, decisions)
                    await _upsert_assets(db, wl_id, signals, filters_json)
                    await _update_last_scanned(db, wl_id)

                    if decision_payloads:
                        from ..api.websocket import broadcast_decision_created

                        for payload in decision_payloads:
                            await broadcast_decision_created(payload)

                    if new_syms:
                        stats["new_signals"] += len(new_syms)
                        logger.info(
                            "[PipelineScan] 🚨 New L3 signals in %s: %s",
                            wl.name, new_syms,
                        )
                        await _broadcast_pipeline_update(
                            watchlist_id=wl_id,
                            watchlist_name=wl.name,
                            level="L3",
                            new_symbols=new_syms,
                            all_signals=signals,
                        )

                else:
                    monitored, _ = _apply_level_filter(
                        assets,
                        profile_config,
                        effective_level,
                        score_config=score_config,
                        apply_profile_filters=False,
                    )

                    logger.info(
                        "[PipelineScan] ═══ %s (%s) MONITOR SUMMARY ═══\n"
                        "  Pool symbols:       %d\n"
                        "  With market data:   %d  (-%d no metadata)\n"
                        "  Visible in board:   %d\n"
                        "  ═══════════════════════════",
                        wl.name, level,
                        len(symbols),
                        assets_with_metadata, len(symbols) - assets_with_metadata,
                        len(monitored),
                    )

                    await _broadcast_scan_funnel(
                        wl_id, wl.name, level,
                        pool_total=len(symbols),
                        with_metadata=assets_with_metadata,
                        profile_candidates=len(monitored),
                        after_profile_filter=len(monitored),
                        after_blocking=len(monitored),
                    )

                    stats["funnels"].append({
                        "watchlist": wl.name, "level": level,
                        "pool_total": len(symbols),
                        "with_metadata": assets_with_metadata,
                        "profile_candidates": len(monitored),
                        "after_profile_filter": len(monitored),
                        "after_blocking": len(monitored),
                    })

                    await _upsert_assets(db, wl_id, monitored, filters_json)
                    await _update_last_scanned(db, wl_id)

            except Exception as exc:
                logger.exception("[PipelineScan] Error processing watchlist %s: %s", wl.name, exc)
                stats["errors"] += 1
                continue

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
