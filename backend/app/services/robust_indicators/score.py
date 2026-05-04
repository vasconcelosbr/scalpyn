"""Deterministic score engine (Task #211).

Pipeline:

    1. Critical-indicator advisory: missing members of
       ``CRITICAL_INDICATORS`` are logged but do NOT reject the score.
       Assets with partial data receive a partial score proportional
       to the rules that could be evaluated.
    2. Global-confidence advisory: when the average envelope confidence
       is below ``min_global_confidence`` the score is still computed
       but ``can_trade`` stays False.
    3. Deterministic scoring (no confidence weighting):

           score = sum(rule.points  for matched rules)
                 / sum(rule.points  for all enabled positive rules)
                 * 100

       Every matched rule contributes its full configured point value.
       Confidence is tracked per rule for observability but does NOT
       multiply into the numerator or denominator. The denominator
       includes every enabled positive rule regardless of match,
       confidence, or data availability, keeping the score bounded
       to ``[0, 100]`` and comparable across assets.
    4. ``score_confidence``: average envelope confidence across rules
       that matched (used as a quality signal alongside the score).
    5. ``can_trade`` flag: True only when
       ``score >= can_trade_threshold`` (default 65) AND
       ``score_confidence >= min_global_confidence`` AND
       ``global_confidence >= min_global_confidence``.

    Per-rule breakdown contract (``matched_rules`` entries):
       - ``awarded_points``: full configured points (= ``points``)
       - ``confidence``: envelope confidence (metadata only)
       - ``data_available``: True (always True for matched rules;
         unmatched rules are not in ``matched_rules``)

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

_BOOL_TREND_OPS = frozenset({"ema50>ema200", "ema9>ema50", "ema9>ema50>ema200"})

_OPERATOR_ENVELOPE_REMAP: Dict[str, str] = {
    "ema50>ema200": "ema50_gt_ema200",
    "ema9>ema50": "ema9_gt_ema50",
    "ema9<ema50": "ema9_gt_ema50",
    "ema9>ema50>ema200": "ema_full_alignment",
    "di+>di-": "di_plus",
    "di->di+": "di_plus",
}


@dataclass
class ScoreResult:
    """Deterministic score outcome (Task #211)."""

    score: float
    score_confidence: float
    can_trade: bool
    rejected: bool
    rejection_reason: Optional[str]
    components: Dict[str, float] = field(default_factory=dict)
    matched_rules: List[Dict[str, Any]] = field(default_factory=list)
    evaluated_rule_ids: List[str] = field(default_factory=list)
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
            "evaluated_rule_ids": list(self.evaluated_rule_ids),
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


def _evaluate_rule(
    rule: Mapping[str, Any],
    env: IndicatorEnvelope,
    envelopes: Optional[Mapping[str, IndicatorEnvelope]] = None,
) -> bool:
    operator_str = (rule.get("operator") or "").strip()
    target = rule.get("value")
    actual = env.value

    if operator_str in _BOOL_TREND_OPS:
        return bool(actual)

    if operator_str == "ema9<ema50":
        return not bool(actual)

    if operator_str in ("di+>di-", "di->di+") and envelopes:
        di_plus_env = envelopes.get("di_plus")
        di_minus_env = envelopes.get("di_minus")
        if not (di_plus_env and di_plus_env.is_usable
                and di_minus_env and di_minus_env.is_usable):
            return False
        try:
            if operator_str == "di+>di-":
                return float(di_plus_env.value) > float(di_minus_env.value)
            return float(di_minus_env.value) > float(di_plus_env.value)
        except (TypeError, ValueError):
            return False

    if operator_str == "between":
        min_val = rule.get("min", 0)
        max_val = rule.get("max", 100)
        try:
            return float(min_val) <= float(actual) <= float(max_val)
        except (TypeError, ValueError):
            return False

    ind_name = (rule.get("indicator") or "").strip().lower()
    if operator_str == ">prev+" and ind_name == "adx_acceleration":
        try:
            return float(actual) > float(target or 0)
        except (TypeError, ValueError):
            return False
    if operator_str == ">prev" and ind_name == "adx_acceleration":
        try:
            return float(actual) > 0
        except (TypeError, ValueError):
            return False

    fn = _OPERATORS.get(operator_str)
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
    weights: Optional[Mapping[str, float]] = None,
    min_global_confidence: float = 0.60,
    can_trade_threshold: float = 65.0,
) -> ScoreResult:
    """Run the deterministic score pipeline (Task #211).

    Args:
        envelopes:               Mapping of indicator name -> envelope.
        scoring_rules:           Iterable of legacy-shape rules.
        weights:                 Accepted for API compatibility with the
                                 legacy engine; ignored by the deterministic
                                 formulation.
        min_global_confidence:   Confidence threshold enforced on
                                 ``score_confidence`` for ``can_trade``.
        can_trade_threshold:     Score threshold below which ``can_trade``
                                 stays False.
    """
    rules = list(scoring_rules or [])
    del weights

    global_conf, valid_n, total_n = _global_confidence(envelopes)

    # ── 1. Critical-indicator advisory (no longer rejects) ────────────────
    missing_critical = [
        name for name in CRITICAL_INDICATORS
        if not (envelopes.get(name) and envelopes[name].is_usable)
    ]
    if missing_critical:
        logger.info(
            "critical indicators missing: %s — computing partial score",
            sorted(missing_critical),
        )

    # ── 2. Global-confidence advisory (no longer rejects) ─────────────────
    if global_conf < min_global_confidence:
        logger.info(
            "global confidence %.3f below threshold %.3f — "
            "computing score with can_trade=False",
            global_conf, min_global_confidence,
        )

    # ── 3. Deterministic scoring (Task #211) ─────────────────────────────
    # Score is fully deterministic: matched rules award their full configured
    # points.  Confidence is tracked per rule for observability and can_trade
    # gating but does NOT multiply into the numerator or denominator.
    matched: List[Dict[str, Any]] = []
    evaluated_rule_ids: List[str] = []
    confidences_used: List[float] = []
    components: Dict[str, float] = {c: 0.0 for c in _VALID_CATEGORIES}

    raw_numerator = 0.0
    points_denominator = 0.0

    for rule in rules:
        name = (rule.get("indicator") or "").strip().lower()
        rule_operator = (rule.get("operator") or "").strip()
        if not name:
            continue
        try:
            points = float(rule.get("points") or 0)
        except (TypeError, ValueError):
            points = 0.0
        if points <= 0:
            continue
        points_denominator += points

        category = _resolve_category(rule)
        resolved_name = _OPERATOR_ENVELOPE_REMAP.get(rule_operator, name)
        env = envelopes.get(resolved_name)
        if env is None or not env.is_usable:
            continue

        rule_id = rule.get("id") or f"{name}_{rule_operator}"
        evaluated_rule_ids.append(str(rule_id))

        if _evaluate_rule(rule, env, envelopes):
            raw_numerator += points
            components[category] += points
            confidences_used.append(env.confidence)
            matched.append({
                "rule_id": rule_id,
                "indicator": name,
                "operator": rule.get("operator"),
                "value": rule.get("value"),
                "points": points,
                "awarded_points": points,
                "confidence": env.confidence,
                "data_available": True,
                "category": category,
            })

    if points_denominator > 0:
        score = (raw_numerator / points_denominator) * 100.0
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
        and global_conf >= min_global_confidence
    )

    return ScoreResult(
        score=round(score, 2),
        score_confidence=round(score_conf, 4),
        can_trade=can_trade,
        rejected=False,
        rejection_reason=None,
        components={k: round(v, 4) for k, v in components.items()},
        matched_rules=matched,
        evaluated_rule_ids=evaluated_rule_ids,
        global_confidence=global_conf,
        valid_indicators=valid_n,
        total_indicators=total_n,
    )


__all__ = ["ScoreResult", "calculate_score_with_confidence"]
