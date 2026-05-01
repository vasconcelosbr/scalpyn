"""Confidence-weighted score engine.

Pipeline (per Phase 1 spec):

    1. Critical-indicator gate: every member of ``CRITICAL_INDICATORS``
       (``rsi``, ``adx``, ``macd``, ``ema50``) must be ``VALID`` or
       ``DEGRADED``. Otherwise → ``REJECTED`` with reason ``critical_gate``.
    2. Global-confidence gate: average envelope confidence across all
       usable indicators must be ≥ 0.6. Otherwise → ``REJECTED`` with
       reason ``confidence_gate``.
    3. Confidence-weighted score (direct formulation):

           score = sum(rule.points * env.confidence  for matched rules)
                 / sum(rule.points                  for all considered rules)
                 * 100

       This is the formulation called out in the spec — every matched
       rule contributes its full point value scaled by the freshness/
       quality of its underlying envelope, and the denominator is the
       sum of all considered rule points so the result stays bounded to
       ``[0, 100]`` regardless of which rules matched.
    4. ``score_confidence``: average envelope confidence across rules
       that matched (used as a quality signal alongside the score).
    5. ``can_trade`` flag: True only when neither gate rejected AND
       ``score >= can_trade_threshold`` (default 65) AND
       ``score_confidence >= min_global_confidence``.

The engine reads scoring rules in the same shape used by the legacy
``ScoreEngine`` (``scoring_rules`` / ``rules`` lists with
``indicator``/``operator``/``value``/``points``/``category``) so the
existing config produced by ``config_service`` can drive both pipelines
unchanged. Category labels are preserved on each matched rule for
debugging and surface-level reporting but no longer drive the bounded
total — the direct formulation makes the score additive across
categories without needing per-category normalisation.
"""

from __future__ import annotations

import logging
import operator as op
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional

from .envelope import IndicatorEnvelope, IndicatorStatus
from .validation import CRITICAL_INDICATORS

logger = logging.getLogger(__name__)


_OPERATORS = {
    "<=": op.le,
    ">=": op.ge,
    "<": op.lt,
    ">": op.gt,
    "=": op.eq,
    "==": op.eq,
    "!=": op.ne,
}

_VALID_CATEGORIES = ("liquidity", "market_structure", "momentum", "signal")


@dataclass
class ScoreResult:
    """Confidence-weighted score outcome."""

    score: float
    score_confidence: float
    can_trade: bool
    rejected: bool
    rejection_reason: Optional[str]
    components: Dict[str, float] = field(default_factory=dict)
    matched_rules: List[Dict[str, Any]] = field(default_factory=list)
    global_confidence: float = 0.0
    valid_indicators: int = 0
    total_indicators: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "score": round(self.score, 4),
            "score_confidence": round(self.score_confidence, 4),
            "can_trade": self.can_trade,
            "rejected": self.rejected,
            "rejection_reason": self.rejection_reason,
            "components": {k: round(v, 4) for k, v in self.components.items()},
            "matched_rules": list(self.matched_rules),
            "global_confidence": round(self.global_confidence, 4),
            "valid_indicators": self.valid_indicators,
            "total_indicators": self.total_indicators,
        }


def _resolve_category(rule: Mapping[str, Any]) -> str:
    cat = rule.get("category")
    if isinstance(cat, str):
        normalized = cat.strip().lower().replace(" ", "_")
        if normalized in _VALID_CATEGORIES:
            return normalized
    return "signal"


def _evaluate_rule(rule: Mapping[str, Any], env: IndicatorEnvelope) -> bool:
    operator = (rule.get("operator") or "").strip()
    target = rule.get("value")
    actual = env.value

    if operator == "ema9>ema50>ema200":
        return bool(actual)

    fn = _OPERATORS.get(operator)
    if fn is None:
        return False

    if isinstance(actual, bool) or isinstance(target, bool):
        try:
            return fn(actual, target)
        except TypeError:
            return False

    try:
        return fn(float(actual), float(target))
    except (TypeError, ValueError):
        try:
            return fn(actual, target)
        except TypeError:
            return False


def _global_confidence(envelopes: Mapping[str, IndicatorEnvelope]) -> tuple[float, int, int]:
    usable = [e for e in envelopes.values() if e.is_usable]
    total = len(envelopes)
    if not usable:
        return 0.0, 0, total
    avg = sum(e.confidence for e in usable) / len(usable)
    return round(avg, 4), len(usable), total


def calculate_score_with_confidence(
    envelopes: Mapping[str, IndicatorEnvelope],
    scoring_rules: Optional[Iterable[Mapping[str, Any]]] = None,
    *,
    weights: Optional[Mapping[str, float]] = None,  # accepted for compat; unused in direct mode
    min_global_confidence: float = 0.60,
    can_trade_threshold: float = 65.0,
) -> ScoreResult:
    """Run the confidence-weighted score pipeline (direct formulation).

    Args:
        envelopes:               Mapping of indicator name -> envelope.
        scoring_rules:           Iterable of legacy-shape rules.
        weights:                 Accepted for API compatibility with the
                                 legacy engine; ignored by the direct
                                 confidence-weighted formulation.
        min_global_confidence:   Confidence gate threshold (also enforces
                                 ``score_confidence >= threshold`` for
                                 ``can_trade``).
        can_trade_threshold:     Score threshold below which ``can_trade``
                                 stays False even when neither gate
                                 rejected.
    """
    rules = list(scoring_rules or [])
    del weights  # explicitly unused — direct formulation has no per-cat weights

    global_conf, valid_n, total_n = _global_confidence(envelopes)

    base = ScoreResult(
        score=0.0,
        score_confidence=0.0,
        can_trade=False,
        rejected=False,
        rejection_reason=None,
        components={c: 0.0 for c in _VALID_CATEGORIES},
        matched_rules=[],
        global_confidence=global_conf,
        valid_indicators=valid_n,
        total_indicators=total_n,
    )

    # ── 1. Critical-indicator gate ──────────────────────────────────────────
    missing_critical = [
        name for name in CRITICAL_INDICATORS
        if not (envelopes.get(name) and envelopes[name].is_usable)
    ]
    if missing_critical:
        base.rejected = True
        base.rejection_reason = (
            f"critical_gate:missing={sorted(missing_critical)}"
        )
        return base

    # ── 2. Global-confidence gate ───────────────────────────────────────────
    if global_conf < min_global_confidence:
        base.rejected = True
        base.rejection_reason = (
            f"confidence_gate:{global_conf:.3f}<{min_global_confidence:.3f}"
        )
        return base

    # ── 3. Direct confidence-weighted scoring ───────────────────────────────
    matched: List[Dict[str, Any]] = []
    confidences_used: List[float] = []
    components: Dict[str, float] = {c: 0.0 for c in _VALID_CATEGORIES}

    weighted_numerator = 0.0      # Σ points * confidence (matched rules)
    points_denominator = 0.0      # Σ points (all considered rules)

    for rule in rules:
        name = (rule.get("indicator") or "").strip().lower()
        if not name:
            continue
        try:
            points = float(rule.get("points") or 0)
        except (TypeError, ValueError):
            points = 0.0
        if points <= 0:
            continue
        # Every considered rule contributes to the denominator so the
        # bounded score reflects total achievable points, not just the
        # matched subset.
        points_denominator += points

        category = _resolve_category(rule)
        env = envelopes.get(name)
        if env is None or not env.is_usable:
            continue

        if _evaluate_rule(rule, env):
            weighted = points * env.confidence
            weighted_numerator += weighted
            components[category] += weighted
            confidences_used.append(env.confidence)
            matched.append({
                "rule_id": rule.get("id"),
                "indicator": name,
                "operator": rule.get("operator"),
                "value": rule.get("value"),
                "points": points,
                "weighted_points": round(weighted, 4),
                "confidence": env.confidence,
                "category": category,
            })

    if points_denominator > 0:
        score = (weighted_numerator / points_denominator) * 100.0
    else:
        score = 0.0
    score = max(0.0, min(100.0, score))

    score_conf = (
        sum(confidences_used) / len(confidences_used)
        if confidences_used else global_conf
    )

    can_trade = (
        score >= can_trade_threshold
        and score_conf >= min_global_confidence
    )

    return ScoreResult(
        score=round(score, 2),
        score_confidence=round(score_conf, 4),
        can_trade=can_trade,
        rejected=False,
        rejection_reason=None,
        components={k: round(v, 4) for k, v in components.items()},
        matched_rules=matched,
        global_confidence=global_conf,
        valid_indicators=valid_n,
        total_indicators=total_n,
    )


__all__ = ["ScoreResult", "calculate_score_with_confidence"]
