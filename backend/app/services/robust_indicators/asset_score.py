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
from typing import Mapping, Optional

from .compute import envelope_indicators
from .score import calculate_score_with_confidence

logger = logging.getLogger(__name__)


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
    """Score a single asset through the robust engine.

    Returns a dict with the canonical asset score fields, or ``None`` when
    the engine rejects the indicators or cannot produce a numeric score::

        {
            "score":              float,
            "score_confidence":   float,
            "global_confidence":  float,
            "matched_rules":      list,
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

    if result.rejected or result.score is None:
        return None

    score = float(result.score)
    payload: dict = {
        "score": round(score, 4),
        "score_confidence": float(result.score_confidence),
        "global_confidence": float(result.global_confidence),
        "matched_rules": list(getattr(result, "matched_rules", []) or []),
    }

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
    "robust_futures_direction_bias",
]
