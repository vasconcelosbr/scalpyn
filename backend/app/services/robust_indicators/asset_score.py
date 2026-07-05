"""High-level helper for scoring a single asset through the robust engine.

Wraps ``envelope_indicators`` + ``calculate_score_with_confidence`` and (for
futures) derives the LONG / SHORT score split + direction tag from a small
deterministic bias function over the indicator envelopes — no dependency on
the legacy ``futures_pipeline_scorer``.

The returned dict shape is intentionally close to the asset row so callers
can splat the result onto an asset dict / DB row.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping, Optional

from .compute import envelope_indicators
from .score import calculate_score_with_confidence

logger = logging.getLogger(__name__)

_COMPONENT_CATEGORIES = ("liquidity", "market_structure", "momentum", "signal")
_COMPONENT_FIELD_BY_CATEGORY = {
    "liquidity": "liquidity_score",
    "market_structure": "market_structure_score",
    "momentum": "momentum_score",
    "signal": "signal_score",
}


def _component_category(rule: Mapping[str, Any]) -> str:
    raw = rule.get("category")
    if isinstance(raw, str):
        normalized = raw.strip().lower().replace(" ", "_")
        if normalized in _COMPONENT_CATEGORIES:
            return normalized
    return "signal"


def normalize_component_scores(
    scoring_rules: list | tuple,
    components: Mapping[str, Any] | None,
) -> dict[str, Optional[float]]:
    """Normalize robust rule-point buckets to legacy 0-100 component scores.

    The robust engine stores category components as awarded rule points. The
    legacy ``alpha_scores`` columns and L3 profile conditions expect bounded
    component score fields. This helper keeps that conversion deterministic and
    shared by ``compute_scores`` and the live pipeline.
    """
    denominators: dict[str, float] = {c: 0.0 for c in _COMPONENT_CATEGORIES}
    for rule in scoring_rules or []:
        if not isinstance(rule, Mapping):
            continue
        try:
            points = float(rule.get("points") or 0)
        except (TypeError, ValueError):
            points = 0.0
        if points <= 0:
            continue
        denominators[_component_category(rule)] += points

    numerators: dict[str, float] = {c: 0.0 for c in _COMPONENT_CATEGORIES}
    for category, value in (components or {}).items():
        if category not in numerators:
            continue
        try:
            numerators[category] = float(value or 0)
        except (TypeError, ValueError):
            numerators[category] = 0.0

    scores: dict[str, Optional[float]] = {}
    for category in _COMPONENT_CATEGORIES:
        denominator = denominators[category]
        if denominator <= 0:
            scores[category] = None
            continue
        score = (numerators[category] / denominator) * 100.0
        scores[category] = round(max(0.0, min(100.0, score)), 4)
    return scores


def score_component_fields(
    component_scores: Mapping[str, Optional[float]],
) -> dict[str, Optional[float]]:
    return {
        field: component_scores.get(category)
        for category, field in _COMPONENT_FIELD_BY_CATEGORY.items()
    }


def robust_futures_direction_bias(indicators: Mapping[str, object]) -> float:
    """Direction bias in ``[-1.0, +1.0]`` derived from indicator envelopes.

    Positive values lean LONG, negative lean SHORT, ``0.0`` is neutral.
    Independent of any legacy ``score_long`` / ``score_short`` /
    ``confidence_score`` columns.

    Inputs (each contributes a single vote when present and well-typed):

      * ``ema9_gt_ema50``      (bool)  — short-term trend
      * ``ema50_gt_ema200``    (bool)  — long-term trend
      * ``macd_histogram``     (float) — momentum impulse sign
      * ``rsi``                (float) — overbought/oversold zone
    """
    if not isinstance(indicators, Mapping):
        return 0.0

    votes: list[float] = []

    def _get(name):
        v = indicators.get(name)
        if isinstance(v, dict):
            v = v.get("value")
        return v

    e9_50 = _get("ema9_gt_ema50")
    if e9_50 is True:
        votes.append(+1.0)
    elif e9_50 is False:
        votes.append(-1.0)

    e50_200 = _get("ema50_gt_ema200")
    if e50_200 is True:
        votes.append(+1.0)
    elif e50_200 is False:
        votes.append(-1.0)

    macd_hist = _get("macd_histogram")
    try:
        if macd_hist is not None:
            mh = float(macd_hist)
            if mh > 0:
                votes.append(+1.0)
            elif mh < 0:
                votes.append(-1.0)
    except (TypeError, ValueError):
        pass

    rsi = _get("rsi")
    try:
        if rsi is not None:
            r = float(rsi)
            if r > 55.0:
                votes.append(+1.0)
            elif r < 45.0:
                votes.append(-1.0)
    except (TypeError, ValueError):
        pass

    if not votes:
        return 0.0
    bias = sum(votes) / len(votes)
    return max(-1.0, min(1.0, bias))


def _direction_tag(bias: float) -> str:
    if bias >= 0.1:
        return "LONG"
    if bias <= -0.1:
        return "SHORT"
    return "NEUTRAL"


def compute_asset_score(
    symbol: str,
    indicators: Mapping[str, object],
    rules: list,
    *,
    is_futures: bool = False,
    flow_source_hint: Optional[str] = None,
) -> Optional[dict]:
    """Score a single asset through the robust engine (deterministic, Task #211).

    Returns a dict with the canonical asset score fields, or ``None`` only
    when the indicator dict is empty or the engine raises.  Partial data
    (e.g. only EMA available, RSI/ADX/MACD missing) produces a partial
    score proportional to the rules that could be evaluated::

        {
            "score":              float,
            "score_confidence":   float,
            "global_confidence":  float,
            "matched_rules":      list,
            "evaluated_rule_ids": list[str],
            # futures-only:
            "score_long":         float,
            "score_short":        float,
            "confidence_score":   float,
            "futures_direction":  "LONG" | "SHORT" | "NEUTRAL",
            "futures_bias":       float,
        }
    """
    if not indicators:
        return None

    try:
        envelopes = envelope_indicators(
            symbol,
            dict(indicators),
            flow_source_hint=flow_source_hint
            or (indicators.get("taker_source") if isinstance(indicators, Mapping) else None),
        )
        result = calculate_score_with_confidence(envelopes, rules)
    except Exception as exc:
        logger.debug("compute_asset_score: robust score failed for %s: %s", symbol, exc)
        return None

    if result.score is None:
        return None

    score = float(result.score)
    component_scores = normalize_component_scores(
        rules, getattr(result, "components", {}) or {}
    )
    payload: dict = {
        "score": round(score, 4),
        "score_confidence": float(result.score_confidence),
        "global_confidence": float(result.global_confidence),
        "components": dict(getattr(result, "components", {}) or {}),
        "component_scores": component_scores,
        "matched_rules": list(getattr(result, "matched_rules", []) or []),
        "evaluated_rule_ids": list(getattr(result, "evaluated_rule_ids", []) or []),
    }
    payload.update(score_component_fields(component_scores))

    if is_futures:
        bias = robust_futures_direction_bias(indicators)
        long_mult = 1.0 - max(0.0, -bias)
        short_mult = 1.0 - max(0.0, bias)
        payload["score_long"] = round(max(0.0, min(100.0, score * long_mult)), 2)
        payload["score_short"] = round(max(0.0, min(100.0, score * short_mult)), 2)
        payload["confidence_score"] = round(score, 2)
        payload["futures_direction"] = _direction_tag(bias)
        payload["futures_bias"] = bias

    return payload


__all__ = [
    "compute_asset_score",
    "normalize_component_scores",
    "robust_futures_direction_bias",
    "score_component_fields",
]
