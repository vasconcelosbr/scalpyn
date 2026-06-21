"""
Counterfactual Combination Services.

CounterfactualCombinationMiner — evaluates pre-defined seed rule sets against
shadow trades to discover which setups actually worked.

DynamicCombinationGenerator — builds combinations from top-performing indicator
buckets discovered by IndicatorLiftAnalyzer.
"""
import hashlib
import itertools
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.profile_intelligence import ProfileRuleCombination

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Seed definitions
# ---------------------------------------------------------------------------

COUNTERFACTUAL_SEEDS = [
    {
        "name": "L3_META_EARLY_PULLBACK_FAST_V1",
        "family": "early_pullback",
        "rules": [
            {"indicator": "ema50_gt_ema200", "operator": "==", "value": True},
            {"indicator": "rsi", "operator": ">=", "value": 30},
            {"indicator": "rsi", "operator": "<=", "value": 45},
            {"indicator": "zscore", "operator": ">=", "value": -1.5},
            {"indicator": "zscore", "operator": "<=", "value": 0.8},
            {"indicator": "vwap_distance_pct", "operator": "<=", "value": 1.5},
            {"indicator": "macd_histogram_pct", "operator": ">", "value": 0},
            {"indicator": "volume_delta", "operator": ">=", "value": 0},
        ],
    },
    {
        "name": "L3_META_ADX_WAKEUP_V1",
        "family": "adx_wakeup",
        "rules": [
            {"indicator": "adx", "operator": ">=", "value": 12},
            {"indicator": "adx", "operator": "<=", "value": 20},
            {"indicator": "adx_acceleration", "operator": ">", "value": 0},
            {"indicator": "macd_histogram_pct", "operator": ">", "value": 0},
            {"indicator": "volume_delta", "operator": ">=", "value": 0},
            {"indicator": "taker_ratio", "operator": ">=", "value": 0.55},
            {"indicator": "rsi", "operator": "<=", "value": 62},
            {"indicator": "zscore", "operator": "<=", "value": 1.2},
        ],
    },
    {
        "name": "L3_META_COMPRESSION_RELEASE_V1",
        "family": "compression_release",
        "rules": [
            {"indicator": "bb_width", "operator": ">=", "value": 0.012},
            {"indicator": "bb_width", "operator": "<=", "value": 0.035},
            {"indicator": "volume_spike", "operator": ">=", "value": 1.3},
            {"indicator": "volume_spike", "operator": "<=", "value": 2.8},
            {"indicator": "macd_histogram_pct", "operator": ">", "value": 0},
            {"indicator": "adx_acceleration", "operator": ">", "value": 0},
            {"indicator": "rsi", "operator": ">=", "value": 45},
            {"indicator": "rsi", "operator": "<=", "value": 70},
            {"indicator": "zscore", "operator": "<=", "value": 1.8},
        ],
    },
    {
        "name": "L3_META_ORDERFLOW_FIRST_V1",
        "family": "order_flow",
        "rules": [
            {"indicator": "taker_ratio", "operator": ">=", "value": 0.58},
            {"indicator": "buy_pressure", "operator": ">=", "value": 0.55},
            {"indicator": "volume_delta", "operator": ">=", "value": 10},
            {"indicator": "orderbook_pressure", "operator": ">=", "value": 0.35},
            {"indicator": "spread_pct", "operator": "<=", "value": 0.20},
            {"indicator": "rsi", "operator": "<=", "value": 68},
            {"indicator": "zscore", "operator": "<=", "value": 1.5},
        ],
    },
    {
        "name": "L3_META_EMA9_RETEST_V1",
        "family": "ema_retest",
        "rules": [
            {"indicator": "ema9_gt_ema21", "operator": "==", "value": True},
            {"indicator": "ema9_distance_pct", "operator": "<=", "value": 1.0},
            {"indicator": "rsi", "operator": ">=", "value": 40},
            {"indicator": "rsi", "operator": "<=", "value": 58},
            {"indicator": "macd_histogram_pct", "operator": ">", "value": 0},
            {"indicator": "volume_delta", "operator": ">=", "value": 0},
            {"indicator": "adx", "operator": ">=", "value": 18},
            {"indicator": "adx", "operator": "<=", "value": 32},
            {"indicator": "zscore", "operator": "<=", "value": 1.2},
        ],
    },
    {
        "name": "L3_META_SHORT_TREND_EARLY_V1",
        "family": "early_pullback",
        "rules": [
            {"indicator": "ema9_gt_ema21", "operator": "==", "value": True},
            {"indicator": "ema50_gt_ema200", "operator": "==", "value": True},
            {"indicator": "adx", "operator": ">=", "value": 18},
            {"indicator": "macd_histogram_pct", "operator": ">", "value": 0},
            {"indicator": "rsi", "operator": ">=", "value": 38},
            {"indicator": "rsi", "operator": "<=", "value": 60},
            {"indicator": "zscore", "operator": "<=", "value": 1.2},
            {"indicator": "vwap_distance_pct", "operator": "<=", "value": 2.0},
        ],
    },
    {
        "name": "L3_META_SOFT_MOMENTUM_NO_EXHAUSTION_V1",
        "family": "anti_exhaustion",
        "rules": [
            {"indicator": "rsi", "operator": ">=", "value": 40},
            {"indicator": "rsi", "operator": "<=", "value": 62},
            {"indicator": "stochastic_k", "operator": "<=", "value": 75},
            {"indicator": "zscore", "operator": "<=", "value": 1.2},
            {"indicator": "vwap_distance_pct", "operator": "<=", "value": 2.0},
            {"indicator": "volume_spike", "operator": "<=", "value": 2.5},
            {"indicator": "macd_histogram_pct", "operator": ">", "value": 0},
            {"indicator": "adx", "operator": ">=", "value": 18},
            {"indicator": "adx", "operator": "<=", "value": 30},
        ],
    },
    {
        "name": "L3_META_LIQUIDITY_PIVOT_V1",
        "family": "liquidity_pivot",
        "rules": [
            {"indicator": "spread_pct", "operator": "<=", "value": 0.15},
            {"indicator": "orderbook_depth_usdt", "operator": ">=", "value": 20000},
            {"indicator": "orderbook_pressure", "operator": ">=", "value": 0.45},
            {"indicator": "taker_ratio", "operator": ">=", "value": 0.55},
            {"indicator": "volume_delta", "operator": ">=", "value": 0},
            {"indicator": "rsi", "operator": ">=", "value": 35},
            {"indicator": "rsi", "operator": "<=", "value": 60},
        ],
    },
    {
        "name": "L3_META_CONTROLLED_BOUNCE_V1",
        "family": "mean_reversion",
        "rules": [
            {"indicator": "rsi", "operator": ">=", "value": 24},
            {"indicator": "rsi", "operator": "<=", "value": 38},
            {"indicator": "zscore", "operator": ">=", "value": -3.0},
            {"indicator": "zscore", "operator": "<=", "value": -1.2},
            {"indicator": "adx", "operator": "<=", "value": 25},
            {"indicator": "volume_delta", "operator": ">=", "value": -10},
            {"indicator": "taker_ratio", "operator": ">=", "value": 0.45},
            {"indicator": "orderbook_pressure", "operator": ">=", "value": 0.35},
            {"indicator": "spread_pct", "operator": "<=", "value": 0.20},
        ],
    },
    {
        "name": "L3_META_CLEAN_BREAKOUT_V1",
        "family": "breakout",
        "rules": [
            {"indicator": "bb_width", "operator": ">=", "value": 0.018},
            {"indicator": "bb_width", "operator": "<=", "value": 0.060},
            {"indicator": "volume_spike", "operator": ">=", "value": 1.2},
            {"indicator": "volume_spike", "operator": "<=", "value": 2.8},
            {"indicator": "taker_ratio", "operator": ">=", "value": 0.55},
            {"indicator": "buy_pressure", "operator": ">=", "value": 0.55},
            {"indicator": "volume_delta", "operator": ">=", "value": 10},
            {"indicator": "rsi", "operator": ">=", "value": 50},
            {"indicator": "rsi", "operator": "<=", "value": 74},
            {"indicator": "zscore", "operator": "<=", "value": 2.0},
            {"indicator": "vwap_distance_pct", "operator": "<=", "value": 2.0},
        ],
    },
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _evaluate_rules(features: dict, rules: list) -> bool:
    """
    Evaluate a list of rules against a features dict.
    Returns True only if ALL rules pass (AND logic).
    Missing features skip the rule (lenient — does not fail the whole set).
    """
    for rule in rules:
        indicator = rule.get("indicator")
        operator = rule.get("operator")
        threshold = rule.get("value")
        if indicator is None or operator is None or threshold is None:
            continue

        raw_val = features.get(indicator)
        if raw_val is None:
            return False

        # Normalise to float for numeric comparisons
        if isinstance(raw_val, bool):
            feature_val = raw_val
        else:
            try:
                feature_val = float(raw_val)
            except (TypeError, ValueError):
                return False

        # Handle boolean/equality operators
        if operator == "==":
            # For boolean indicators compare truthiness
            if isinstance(threshold, bool):
                if bool(feature_val) != threshold:
                    return False
            else:
                if feature_val != threshold:
                    return False
        elif operator == ">=":
            if float(feature_val) < float(threshold):
                return False
        elif operator == "<=":
            if float(feature_val) > float(threshold):
                return False
        elif operator == ">":
            if float(feature_val) <= float(threshold):
                return False
        elif operator == "<":
            if float(feature_val) >= float(threshold):
                return False

    return True


def _missing_features_count(features: dict, rules: list) -> int:
    return sum(
        1
        for rule in rules
        if rule.get("indicator") and features.get(rule["indicator"]) is None
    )


def _compute_metrics_from_trades(trades: list) -> dict:
    """Aggregate win/loss/pnl/holding/mae/mfe/tp-rates from a list of pre-processed trade dicts."""
    total = len(trades)
    if total == 0:
        return {
            "total_cases": 0, "wins": 0, "losses": 0, "timeouts": 0,
            "win_rate": 0.0, "loss_rate": 0.0,
            "avg_pnl_pct": 0.0, "avg_holding_seconds": 0.0,
            "avg_winner_holding_seconds": 0.0,
            "avg_mae_pct": 0.0, "avg_mfe_pct": 0.0,
            "tp_15m_rate": 0.0, "tp_30m_rate": 0.0, "tp_60m_rate": 0.0,
        }

    wins = sum(1 for t in trades if t["is_win"])
    losses = sum(1 for t in trades if t["is_loss"])
    timeouts = sum(1 for t in trades if t["is_timeout"])
    closed = wins + losses + timeouts

    pnl_vals = [t["pnl_pct"] for t in trades]
    holding_vals = [t["holding_seconds"] for t in trades]
    winner_holding = [t["holding_seconds"] for t in trades if t["is_win"]]
    mae_vals = [t["mae_pct"] for t in trades]
    mfe_vals = [t["mfe_pct"] for t in trades]

    tp15 = sum(1 for t in trades if t["is_win"] and t["holding_seconds"] <= 900)
    tp30 = sum(1 for t in trades if t["is_win"] and t["holding_seconds"] <= 1800)
    tp60 = sum(1 for t in trades if t["is_win"] and t["holding_seconds"] <= 3600)

    denom = max(closed, 1)
    return {
        "total_cases": total,
        "wins": wins,
        "losses": losses,
        "timeouts": timeouts,
        "win_rate": wins / denom,
        "loss_rate": losses / denom,
        "avg_pnl_pct": sum(pnl_vals) / len(pnl_vals) if pnl_vals else 0.0,
        "avg_holding_seconds": sum(holding_vals) / total,
        "avg_winner_holding_seconds": sum(winner_holding) / len(winner_holding) if winner_holding else 0.0,
        "avg_mae_pct": sum(mae_vals) / total,
        "avg_mfe_pct": sum(mfe_vals) / total,
        "tp_15m_rate": tp15 / denom,
        "tp_30m_rate": tp30 / denom,
        "tp_60m_rate": tp60 / denom,
    }


async def _load_trades_for_window(
    db: AsyncSession,
    user_id: UUID,
    start: datetime,
    end: datetime,
) -> list:
    """Load closed shadow trades with features_snapshot for a given window."""
    rows = (
        await db.execute(
            text("""
                SELECT
                    profile_id,
                    profile_name,
                    symbol,
                    created_at,
                    outcome,
                    pnl_pct,
                    mae_pct,
                    mfe_pct,
                    holding_seconds,
                    features_snapshot
                FROM shadow_trades
                WHERE user_id = :uid
                  AND created_at >= :start
                  AND created_at < :end
                  AND outcome IN ('TP_HIT', 'SL_HIT', 'TIMEOUT')
                  AND features_snapshot IS NOT NULL
                ORDER BY created_at
                LIMIT 50000
            """),
            {"uid": str(user_id), "start": start, "end": end},
        )
    ).fetchall()

    trades = []
    for row in rows:
        features = row.features_snapshot
        if isinstance(features, str):
            try:
                features = json.loads(features)
            except Exception:
                features = {}
        if not isinstance(features, dict):
            features = {}
        outcome = row.outcome or ""
        trades.append({
            "profile_id": row.profile_id,
            "profile_name": row.profile_name,
            "symbol": row.symbol,
            "created_at": row.created_at,
            "outcome": outcome,
            "is_win": outcome == "TP_HIT",
            "is_loss": outcome == "SL_HIT",
            "is_timeout": outcome == "TIMEOUT",
            "pnl_pct": row.pnl_pct if row.pnl_pct is not None else 0.0,
            "mae_pct": row.mae_pct if row.mae_pct is not None else 0.0,
            "mfe_pct": row.mfe_pct if row.mfe_pct is not None else 0.0,
            "holding_seconds": row.holding_seconds if row.holding_seconds is not None else 0,
            "features": features,
        })
    return trades


def _match_trades(trades: list, rules: list) -> tuple[list, int]:
    matching = []
    missing_count = 0
    for trade in trades:
        missing_count += _missing_features_count(trade["features"], rules)
        if _evaluate_rules(trade["features"], rules):
            matching.append(trade)
    return matching, missing_count


def _window_metrics(
    matching: list,
    all_trades: list,
    start: datetime,
    end: datetime,
    missing_count: int,
) -> dict:
    from .profile_validation_service import diversity_metrics

    metrics = _compute_metrics_from_trades(matching)
    base = _compute_metrics_from_trades(all_trades)
    metrics.update({
        "start": start.isoformat(),
        "end": end.isoformat(),
        "trade_count": metrics["total_cases"],
        "base_win_rate": base["win_rate"],
        "lift": (
            metrics["win_rate"] / max(base["win_rate"], 0.001)
            if metrics["total_cases"]
            else 0.0
        ),
        "expected_pnl": metrics["avg_pnl_pct"],
        "missing_count": missing_count,
        **diversity_metrics(matching),
    })
    return metrics


def _build_combination_hash(name: str, user_id: UUID) -> str:
    return hashlib.sha256(f"{name}|{user_id}".encode()).hexdigest()[:32]


def _normalize_rule_value(v: Any) -> str:
    """Canonical string for a rule value — floats stripped of trailing zeros."""
    if isinstance(v, float):
        return f"{v:.8f}".rstrip("0").rstrip(".")
    if isinstance(v, int):
        return str(v)
    return str(v) if v is not None else ""


def _canonical_rule_str(rule: dict) -> str:
    indicator = rule.get("indicator") or rule.get("field") or rule.get("item") or ""
    operator = str(rule.get("operator") or "")
    value = _normalize_rule_value(rule.get("value"))
    return f"{indicator}|{operator}|{value}"


def _build_canonical_rules_hash(rules: list, user_id: UUID) -> str:
    """Stable hash based on sorted, normalised rules — independent of run_id.

    Sorting ensures {A ∧ B} and {B ∧ A} produce the same hash.
    Normalisation ensures 0.2 and 0.20 produce the same hash.
    """
    canonical = sorted(_canonical_rule_str(r) for r in rules)
    payload = "||".join(canonical) + f"|{user_id}"
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


def _confidence_level_from_count(n: int) -> str:
    if n == 0:
        return "NO_DATA"
    if n < 30:
        return "LOW"
    elif n < 100:
        return "MEDIUM"
    return "HIGH"


# ---------------------------------------------------------------------------
# CounterfactualCombinationMiner
# ---------------------------------------------------------------------------

class CounterfactualCombinationMiner:
    """Evaluates pre-defined seed rule sets against shadow trade history."""

    async def mine_seeds(
        self,
        db: AsyncSession,
        user_id: UUID,
        run_id: UUID,
        lookback_days: int,
        base_metrics: dict,
        discovery_start: datetime,
        discovery_end: datetime,
        validation_start: datetime,
        validation_end: datetime,
    ) -> List[dict]:
        logger.info(
            "[CFMiner] Starting seed mining for user=%s run=%s", user_id, run_id
        )

        # Load discovery and validation trades once
        disc_trades = await _load_trades_for_window(db, user_id, discovery_start, discovery_end)
        val_trades = await _load_trades_for_window(db, user_id, validation_start, validation_end)

        base_win_rate = base_metrics.get("base_win_rate", 0.0)
        safe_base_wr = max(base_win_rate, 0.001)

        results = []

        for seed in COUNTERFACTUAL_SEEDS:
            try:
                combination_hash = _build_combination_hash(seed["name"], user_id)
                rules = seed["rules"]

                # Discovery window evaluation
                disc_matching = [t for t in disc_trades if _evaluate_rules(t["features"], rules)]
                disc_m = _compute_metrics_from_trades(disc_matching)

                # Validation window evaluation
                val_matching = [t for t in val_trades if _evaluate_rules(t["features"], rules)]
                val_m = _compute_metrics_from_trades(val_matching)

                # Overall (disc + val combined for champion_score)
                all_matching = disc_matching + val_matching
                overall_m = _compute_metrics_from_trades(all_matching)

                lift_vs_base = overall_m["win_rate"] / safe_base_wr

                # Degradation: how much win_rate dropped from discovery to validation
                degradation_pct = 0.0
                if disc_m["total_cases"] > 0 and val_m["total_cases"] > 0:
                    if disc_m["win_rate"] > 0:
                        degradation_pct = (
                            (disc_m["win_rate"] - val_m["win_rate"]) / disc_m["win_rate"]
                        ) * 100.0

                from .profile_suggestion_service import (
                    calculate_champion_score,
                    detect_overfit_risk,
                )

                overfit_risk = detect_overfit_risk(
                    disc_m, val_m,
                    total_cases=overall_m["total_cases"],
                    n_rules=len(rules),
                )

                champion_metrics = dict(overall_m)
                champion_metrics["degradation_pct"] = degradation_pct
                champion_score = calculate_champion_score(
                    type("M", (), champion_metrics)(),
                    type("B", (), base_metrics)(),
                )

                confidence_level = _confidence_level_from_count(overall_m["total_cases"])

                comb = ProfileRuleCombination(
                    user_id=user_id,
                    run_id=run_id,
                    combination_hash=combination_hash,
                    combination_type="counterfactual_seed",
                    setup_family=seed.get("family"),
                    suggested_name=seed["name"],
                    rules_json=rules,
                    total_cases=overall_m["total_cases"],
                    wins=overall_m["wins"],
                    losses=overall_m["losses"],
                    timeouts=overall_m["timeouts"],
                    win_rate=overall_m["win_rate"],
                    loss_rate=overall_m["loss_rate"],
                    avg_pnl_pct=overall_m["avg_pnl_pct"],
                    avg_holding_seconds=overall_m["avg_holding_seconds"],
                    avg_winner_holding_seconds=overall_m["avg_winner_holding_seconds"],
                    avg_mae_pct=overall_m["avg_mae_pct"],
                    avg_mfe_pct=overall_m["avg_mfe_pct"],
                    tp_15m_rate=overall_m["tp_15m_rate"],
                    tp_30m_rate=overall_m["tp_30m_rate"],
                    tp_60m_rate=overall_m["tp_60m_rate"],
                    lift_vs_base=lift_vs_base,
                    champion_score=champion_score,
                    confidence_level=confidence_level,
                    degradation_pct=degradation_pct,
                    overfit_risk=overfit_risk,
                    discovery_metrics_json={
                        "total_cases": disc_m["total_cases"],
                        "wins": disc_m["wins"],
                        "losses": disc_m["losses"],
                        "win_rate": disc_m["win_rate"],
                        "avg_pnl_pct": disc_m["avg_pnl_pct"],
                        "tp_30m_rate": disc_m["tp_30m_rate"],
                    },
                    validation_metrics_json={
                        "total_cases": val_m["total_cases"],
                        "wins": val_m["wins"],
                        "losses": val_m["losses"],
                        "win_rate": val_m["win_rate"],
                        "avg_pnl_pct": val_m["avg_pnl_pct"],
                        "tp_30m_rate": val_m["tp_30m_rate"],
                    },
                    status="discovered",
                )
                db.add(comb)
                await db.flush()

                result_dict = {
                    "id": comb.id,
                    "name": seed["name"],
                    "family": seed.get("family"),
                    "combination_hash": combination_hash,
                    "rules": rules,
                    "champion_score": champion_score,
                    "confidence_level": confidence_level,
                    "overfit_risk": overfit_risk,
                    **overall_m,
                }
                results.append(result_dict)

            except Exception as exc:
                logger.warning("[CFMiner] Seed %s failed: %s", seed.get("name"), exc)
                continue

        logger.info("[CFMiner] Mined %d seeds for run=%s", len(results), run_id)
        return results


# ---------------------------------------------------------------------------
# DynamicCombinationGenerator
# ---------------------------------------------------------------------------

class DynamicCombinationGenerator:
    """Builds rule combinations from top-performing indicator buckets."""

    async def generate(
        self,
        db: AsyncSession,
        user_id: UUID,
        run_id: UUID,
        base_metrics: dict,
        indicator_stats: List[dict],
        discovery_start: datetime,
        discovery_end: datetime,
        validation_start: datetime,
        validation_end: datetime,
        max_combinations: int = 500,
    ) -> List[dict]:
        logger.info(
            "[DynComb] Starting dynamic combination generation for user=%s run=%s",
            user_id, run_id,
        )

        # Filter to winning_indicator with MEDIUM or HIGH confidence
        winning = [
            s for s in indicator_stats
            if s.get("role_detected") == "winning_indicator"
            and s.get("confidence_level") in ("MEDIUM", "HIGH")
        ]

        if len(winning) < 2:
            logger.info("[DynComb] Not enough winning indicators to combine.")
            return []

        # Top 20 by lift_vs_base
        winning.sort(key=lambda x: x.get("lift_vs_base", 0), reverse=True)
        top_winning = winning[:20]

        # Load discovery trades
        disc_trades = await _load_trades_for_window(db, user_id, discovery_start, discovery_end)
        val_trades = await _load_trades_for_window(db, user_id, validation_start, validation_end)

        base_win_rate = base_metrics.get("base_win_rate", 0.0)
        safe_base_wr = max(base_win_rate, 0.001)

        from .profile_suggestion_service import calculate_champion_score, detect_overfit_risk

        saved = 0
        results = []
        # In-memory dedup guard: same canonical rules within this run → skip
        _seen_hashes: set = set()

        # Generate combinations of size 2, 3, 4
        for size in (2, 3, 4):
            if saved >= max_combinations:
                break
            for combo in itertools.combinations(top_winning, size):
                if saved >= max_combinations:
                    break

                # Check for contradictions
                if _has_contradictions(combo):
                    continue

                # Build rules from bucket definitions
                rules = _build_rules_from_buckets(combo)

                # Reject combinations that contain bearish signals as long entry conditions
                if _is_semantically_bearish(rules):
                    continue

                combo_name = "_AND_".join(b["bucket_label"] for b in combo)
                # Canonical hash: stable across runs, based on sorted+normalised rules
                combination_hash = _build_canonical_rules_hash(rules, user_id)
                if combination_hash in _seen_hashes:
                    continue
                _seen_hashes.add(combination_hash)

                matching, disc_missing = _match_trades(disc_trades, rules)
                m = _window_metrics(
                    matching,
                    disc_trades,
                    discovery_start,
                    discovery_end,
                    disc_missing,
                )

                if m["total_cases"] < 5:
                    continue

                lift_vs_base = m["win_rate"] / safe_base_wr
                val_matching, val_missing = _match_trades(val_trades, rules)
                val_m = _window_metrics(
                    val_matching,
                    val_trades,
                    validation_start,
                    validation_end,
                    val_missing,
                )
                from .profile_validation_service import classify_validation
                validation = classify_validation(
                    discovery_metrics=m,
                    validation_metrics=val_m,
                    discovery_start=discovery_start,
                    discovery_end=discovery_end,
                    validation_start=validation_start,
                    validation_end=validation_end,
                    missing_count=disc_missing + val_missing,
                )
                val_m.update(validation)
                from .algorithm_governance_service import source_profile_attribution
                source_profiles, source_profile_ids = source_profile_attribution(
                    matching + val_matching
                )

                champion_metrics = dict(m)
                champion_metrics["degradation_pct"] = 0.0
                overfit_risk = detect_overfit_risk(
                    m, val_m,
                    total_cases=m["total_cases"] + val_m["total_cases"],
                    n_rules=len(rules),
                )
                champion_score = calculate_champion_score(
                    type("M", (), champion_metrics)(),
                    type("B", (), base_metrics)(),
                )

                confidence_level = _confidence_level_from_count(m["total_cases"])

                try:
                    comb = ProfileRuleCombination(
                        user_id=user_id,
                        run_id=run_id,
                        combination_hash=combination_hash,
                        combination_type="counterfactual_dynamic",
                        setup_family=None,
                        suggested_name=combo_name[:120],
                        rules_json=rules,
                        source_profiles=source_profiles,
                        source_profile_ids=source_profile_ids,
                        total_cases=m["total_cases"],
                        wins=m["wins"],
                        losses=m["losses"],
                        timeouts=m["timeouts"],
                        win_rate=m["win_rate"],
                        loss_rate=m["loss_rate"],
                        avg_pnl_pct=m["avg_pnl_pct"],
                        avg_holding_seconds=m["avg_holding_seconds"],
                        avg_winner_holding_seconds=m["avg_winner_holding_seconds"],
                        avg_mae_pct=m["avg_mae_pct"],
                        avg_mfe_pct=m["avg_mfe_pct"],
                        tp_15m_rate=m["tp_15m_rate"],
                        tp_30m_rate=m["tp_30m_rate"],
                        tp_60m_rate=m["tp_60m_rate"],
                        lift_vs_base=lift_vs_base,
                        champion_score=champion_score,
                        confidence_level=confidence_level,
                        degradation_pct=0.0,
                        overfit_risk=overfit_risk,
                        discovery_metrics_json=m,
                        validation_metrics_json=val_m,
                        status=validation["actionability_status"],
                    )
                    db.add(comb)
                    await db.flush()
                    saved += 1

                    results.append({
                        "id": comb.id,
                        "name": combo_name,
                        "combination_hash": combination_hash,
                        "rules": rules,
                        "source_profiles": source_profiles,
                        "source_profile_ids": source_profile_ids,
                        "champion_score": champion_score,
                        "confidence_level": confidence_level,
                        "overfit_risk": overfit_risk,
                        "discovery_metrics": m,
                        "validation_metrics": val_m,
                        **validation,
                        **m,
                    })
                except Exception as exc:
                    logger.warning("[DynComb] Could not save combination %s: %s", combo_name, exc)
                    continue

        results.sort(key=lambda x: x.get("champion_score", 0), reverse=True)
        logger.info("[DynComb] Generated %d dynamic combinations for run=%s", len(results), run_id)
        return results


# ---------------------------------------------------------------------------
# Contradiction detection helpers
# ---------------------------------------------------------------------------

# Indicator/operator/value triples that are semantically bearish and must not
# appear as long entry conditions in a trend-following long strategy.
_BEARISH_LONG_SIGNALS: list = [
    ("ema50_gt_ema200", "==", False),
    ("ema9_gt_ema21", "==", False),
]


def _is_semantically_bearish(rules: list) -> bool:
    """Return True if any rule matches a known bearish-for-long signal."""
    for rule in rules:
        ind = rule.get("indicator")
        op = rule.get("operator")
        val = rule.get("value")
        for b_ind, b_op, b_val in _BEARISH_LONG_SIGNALS:
            if ind == b_ind and op == b_op and val == b_val:
                return True
    return False


def _has_contradictions(combo: tuple) -> bool:
    """Return True if this combination of bucket stats is internally contradictory."""
    indicators_seen: Dict[str, list] = {}
    for bucket in combo:
        ind = bucket.get("indicator", "")
        indicators_seen.setdefault(ind, []).append(bucket)

    for ind, buckets in indicators_seen.items():
        if len(buckets) < 2:
            continue

        # Boolean contradiction: true AND false for same indicator
        texts = [b.get("value_text") for b in buckets]
        if "true" in texts and "false" in texts:
            return True

        # Range contradiction: check if any two buckets have non-overlapping ranges
        ranges = []
        for b in buckets:
            rmin = b.get("range_min")
            rmax = b.get("range_max")
            if rmin is not None or rmax is not None:
                ranges.append((rmin, rmax))

        if len(ranges) >= 2:
            for i, (amin, amax) in enumerate(ranges):
                for j, (bmin, bmax) in enumerate(ranges):
                    if i >= j:
                        continue
                    # Check non-overlap: a is entirely below b
                    if amax is not None and bmin is not None and amax <= bmin:
                        return True
                    # Check non-overlap: b is entirely below a
                    if bmax is not None and amin is not None and bmax <= amin:
                        return True

    return False


def _build_rules_from_buckets(combo: tuple) -> list:
    """Convert a tuple of indicator bucket dicts into a list of rule dicts."""
    rules = []
    for bucket in combo:
        ind = bucket.get("indicator", "")
        value_text = bucket.get("value_text")
        range_min = bucket.get("range_min")
        range_max = bucket.get("range_max")

        if value_text == "true":
            rules.append({"indicator": ind, "operator": "==", "value": True})
        elif value_text == "false":
            rules.append({"indicator": ind, "operator": "==", "value": False})
        elif value_text and value_text.startswith(">0"):
            rules.append({"indicator": ind, "operator": ">", "value": 0})
        elif value_text and value_text.startswith("<=0"):
            rules.append({"indicator": ind, "operator": "<=", "value": 0})
        else:
            if range_min is not None:
                rules.append({"indicator": ind, "operator": ">=", "value": range_min})
            if range_max is not None:
                rules.append({"indicator": ind, "operator": "<", "value": range_max})

    return rules
