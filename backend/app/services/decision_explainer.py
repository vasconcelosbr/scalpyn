"""
Decision Explainer — Full explainability for every trading decision.

Records and formats the complete decision trace:
  - Market regime detected (with confidence and evidence)
  - Skill selected (with candidates and scores)
  - Score breakdown (each rule's contribution)
  - Risk blocks evaluated (pass/fail)
  - Final decision (BUY/HOLD/REJECT with reason)

This module replaces the opaque "rejected — RSI < 45" with a rich,
human-readable explanation of WHY the decision was made.

Author: Market Skills Engine v1
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

from .market_regime_engine import MarketRegime, RegimeSignal
from .skill_profiles import SkillProfile
from .skill_selector import SkillSelection

logger = logging.getLogger(__name__)


# ── Score Contributor ─────────────────────────────────────────────────────────

@dataclass
class ScoreContributor:
    """A single scoring rule's contribution to the total score."""
    indicator: str
    label: str
    category: str           # momentum, market_structure, liquidity, signal
    operator: str
    threshold: Any
    current_value: Any
    points_awarded: float
    points_possible: float
    passed: bool
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "indicator": self.indicator,
            "label": self.label,
            "category": self.category,
            "operator": self.operator,
            "threshold": self.threshold,
            "current_value": self.current_value,
            "points_awarded": self.points_awarded,
            "points_possible": self.points_possible,
            "passed": self.passed,
            "note": self.note,
        }


# ── Risk Block Result ─────────────────────────────────────────────────────────

@dataclass
class RiskBlockResult:
    """Result of a risk-only block evaluation."""
    name: str
    indicator: str
    operator: str
    threshold: Any
    current_value: Any
    blocked: bool
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "indicator": self.indicator,
            "operator": self.operator,
            "threshold": self.threshold,
            "current_value": self.current_value,
            "blocked": self.blocked,
            "reason": self.reason,
        }


# ── Decision Explanation ──────────────────────────────────────────────────────

@dataclass
class DecisionExplanation:
    """Complete explanation of a trading decision."""
    symbol: str
    decision: str                           # STRONG_BUY, BUY, HOLD, REJECT, BLOCKED
    total_score: float = 0.0
    max_possible_score: float = 0.0

    # Regime
    regime: Optional[MarketRegime] = None
    regime_confidence: float = 0.0
    regime_details: str = ""

    # Skill
    skill_key: str = ""
    skill_name: str = ""
    skill_selection_reason: str = ""
    skill_candidates: List[Dict[str, Any]] = field(default_factory=list)

    # Score breakdown
    contributors: List[ScoreContributor] = field(default_factory=list)
    category_scores: Dict[str, float] = field(default_factory=dict)

    # Risk blocks
    risk_blocks: List[RiskBlockResult] = field(default_factory=list)
    risk_blocked: bool = False
    risk_block_reason: str = ""

    # Decision reasoning
    reason: str = ""
    confidence: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "decision": self.decision,
            "total_score": round(self.total_score, 1),
            "max_possible_score": round(self.max_possible_score, 1),
            "score_pct": round(
                (self.total_score / self.max_possible_score * 100)
                if self.max_possible_score > 0 else 0,
                1,
            ),
            "regime": {
                "value": self.regime.value if self.regime else "UNKNOWN",
                "confidence": round(self.regime_confidence, 2),
                "details": self.regime_details,
            },
            "skill": {
                "key": self.skill_key,
                "name": self.skill_name,
                "selection_reason": self.skill_selection_reason,
                "candidates": self.skill_candidates,
            },
            "contributors": [c.to_dict() for c in self.contributors],
            "category_scores": {
                k: round(v, 1) for k, v in self.category_scores.items()
            },
            "risk_blocks": [b.to_dict() for b in self.risk_blocks],
            "risk_blocked": self.risk_blocked,
            "risk_block_reason": self.risk_block_reason,
            "reason": self.reason,
            "confidence": round(self.confidence, 2),
        }

    def summary(self) -> str:
        """Human-readable one-line summary."""
        regime_str = self.regime.value if self.regime else "?"
        top_contributors = sorted(
            [c for c in self.contributors if c.points_awarded > 0],
            key=lambda c: c.points_awarded,
            reverse=True,
        )[:3]
        top_str = ", ".join(
            f"{c.indicator}={c.current_value} (+{c.points_awarded:.0f})"
            for c in top_contributors
        )
        return (
            f"[{regime_str}] {self.skill_name} → {self.decision} "
            f"(score={self.total_score:.0f}/{self.max_possible_score:.0f}) "
            f"Top: {top_str}"
        )


# ── Decision Explainer ───────────────────────────────────────────────────────

class DecisionExplainer:
    """
    Evaluates an asset using a skill profile and produces a full explanation.

    This is the main entry point for the Market Skills Engine evaluation.
    It replaces the old binary veto pipeline with weighted scoring.
    """

    def evaluate(
        self,
        symbol: str,
        indicators: Dict[str, Any],
        skill: SkillProfile,
        regime_signal: RegimeSignal,
        skill_selection: Optional[SkillSelection] = None,
    ) -> DecisionExplanation:
        """
        Evaluate an asset against a skill profile and return a full explanation.

        Steps:
          1. Check risk-only block rules (operational vetos)
          2. Score against all skill scoring rules
          3. Classify decision based on thresholds
          4. Build complete explanation
        """
        explanation = DecisionExplanation(
            symbol=symbol,
            regime=regime_signal.regime,
            regime_confidence=regime_signal.confidence,
            regime_details=regime_signal.details,
            skill_key=skill.skill_key,
            skill_name=skill.name,
        )

        if skill_selection:
            explanation.skill_selection_reason = skill_selection.selection_reason
            explanation.skill_candidates = skill_selection.candidates

        # 1. Risk-only block rules
        risk_blocked, risk_reason, risk_results = self._evaluate_risk_blocks(
            indicators, skill.block_rules,
        )
        explanation.risk_blocks = risk_results
        explanation.risk_blocked = risk_blocked
        explanation.risk_block_reason = risk_reason

        if risk_blocked:
            explanation.decision = "BLOCKED"
            explanation.reason = f"Risk block: {risk_reason}"
            explanation.confidence = 1.0
            return explanation

        # 2. Score against all rules
        contributors, total_score, max_score, category_scores = self._evaluate_scoring(
            indicators, skill.scoring_rules,
        )
        explanation.contributors = contributors
        explanation.total_score = total_score
        explanation.max_possible_score = max_score
        explanation.category_scores = category_scores

        # 3. Classify decision
        decision = skill.classify_score(total_score)
        explanation.decision = decision
        explanation.confidence = min(1.0, total_score / max(max_score, 1))

        # 4. Build reason
        top_positive = sorted(
            [c for c in contributors if c.points_awarded > 0],
            key=lambda c: c.points_awarded,
            reverse=True,
        )[:3]
        top_negative = sorted(
            [c for c in contributors if c.points_awarded < 0],
            key=lambda c: c.points_awarded,
        )[:2]

        reasons = []
        if top_positive:
            reasons.append(
                "Forças: " + ", ".join(f"{c.label} (+{c.points_awarded:.0f})" for c in top_positive)
            )
        if top_negative:
            reasons.append(
                "Fraquezas: " + ", ".join(f"{c.label} ({c.points_awarded:.0f})" for c in top_negative)
            )

        threshold_used = skill.scoring_thresholds.get("buy", 60)
        if decision in ("BUY", "STRONG_BUY"):
            reasons.append(f"Score {total_score:.0f} ≥ threshold {threshold_used}")
        else:
            reasons.append(f"Score {total_score:.0f} < threshold {threshold_used}")

        explanation.reason = " | ".join(reasons)

        return explanation

    def _evaluate_risk_blocks(
        self,
        indicators: Dict[str, Any],
        block_rules: List[Dict[str, Any]],
    ) -> tuple:
        """
        Evaluate risk-only block rules.
        Returns (blocked, reason, results).
        """
        results: List[RiskBlockResult] = []
        blocked = False
        block_reason = ""

        for rule in block_rules:
            if rule.get("block_type") != "risk":
                continue

            indicator_name = rule.get("indicator", "")
            operator = rule.get("operator", "")
            threshold = rule.get("value")
            current = indicators.get(indicator_name)

            if current is None:
                # Missing data → skip (never block on missing)
                results.append(RiskBlockResult(
                    name=rule.get("name", indicator_name),
                    indicator=indicator_name,
                    operator=operator,
                    threshold=threshold,
                    current_value=None,
                    blocked=False,
                    reason="Dados indisponíveis — não bloqueia",
                ))
                continue

            try:
                current_float = float(current)
                threshold_float = float(threshold) if threshold is not None else 0

                condition_met = False
                if operator == "<":
                    condition_met = current_float < threshold_float
                elif operator == ">":
                    condition_met = current_float > threshold_float
                elif operator == "<=":
                    condition_met = current_float <= threshold_float
                elif operator == ">=":
                    condition_met = current_float >= threshold_float
                elif operator == "=":
                    condition_met = str(current) == str(threshold)

                result = RiskBlockResult(
                    name=rule.get("name", indicator_name),
                    indicator=indicator_name,
                    operator=operator,
                    threshold=threshold,
                    current_value=current_float,
                    blocked=condition_met,
                    reason=rule.get("reason", ""),
                )
                results.append(result)

                if condition_met:
                    blocked = True
                    block_reason = f"{rule.get('name', indicator_name)}: {rule.get('reason', '')}"

            except (TypeError, ValueError):
                results.append(RiskBlockResult(
                    name=rule.get("name", indicator_name),
                    indicator=indicator_name,
                    operator=operator,
                    threshold=threshold,
                    current_value=current,
                    blocked=False,
                    reason="Erro na conversão do valor",
                ))

        return blocked, block_reason, results

    def _evaluate_scoring(
        self,
        indicators: Dict[str, Any],
        scoring_rules: List[Dict[str, Any]],
    ) -> tuple:
        """
        Score against all rules.
        Returns (contributors, total_score, max_possible, category_scores).
        """
        contributors: List[ScoreContributor] = []
        total_score = 0.0
        max_positive = 0.0  # sum of all positive possible points
        category_scores: Dict[str, float] = {}

        for rule in scoring_rules:
            indicator_name = rule.get("indicator", "")
            operator = rule.get("operator", "")
            threshold = rule.get("value")
            points = float(rule.get("points", 0))
            category = rule.get("category", "other")
            label = rule.get("label", indicator_name)
            note = rule.get("note", "")

            if points > 0:
                max_positive += points

            current = indicators.get(indicator_name)

            if current is None:
                # Missing data → 0 points (neither reward nor penalize)
                contributors.append(ScoreContributor(
                    indicator=indicator_name,
                    label=label,
                    category=category,
                    operator=operator,
                    threshold=threshold,
                    current_value=None,
                    points_awarded=0,
                    points_possible=points,
                    passed=False,
                    note="Dados indisponíveis",
                ))
                continue

            try:
                passed = self._check_condition(current, operator, threshold)
                awarded = points if passed else 0.0

                contributors.append(ScoreContributor(
                    indicator=indicator_name,
                    label=label,
                    category=category,
                    operator=operator,
                    threshold=threshold,
                    current_value=current,
                    points_awarded=awarded,
                    points_possible=points,
                    passed=passed,
                    note=note,
                ))

                total_score += awarded
                if awarded != 0:
                    category_scores[category] = category_scores.get(category, 0) + awarded

            except Exception:
                contributors.append(ScoreContributor(
                    indicator=indicator_name,
                    label=label,
                    category=category,
                    operator=operator,
                    threshold=threshold,
                    current_value=current,
                    points_awarded=0,
                    points_possible=points,
                    passed=False,
                    note="Erro na avaliação",
                ))

        return contributors, total_score, max_positive, category_scores

    @staticmethod
    def _check_condition(current: Any, operator: str, threshold: Any) -> bool:
        """Check if a condition is met."""
        try:
            if operator == "between":
                # threshold = [min, max]
                if isinstance(threshold, (list, tuple)) and len(threshold) == 2:
                    val = float(current)
                    return float(threshold[0]) <= val <= float(threshold[1])
                return False

            if operator == "=":
                return str(current).lower() == str(threshold).lower()

            val = float(current)
            thr = float(threshold)

            if operator == "<":
                return val < thr
            elif operator == ">":
                return val > thr
            elif operator == "<=":
                return val <= thr
            elif operator == ">=":
                return val >= thr
            elif operator == "!=":
                return val != thr
        except (TypeError, ValueError):
            pass
        return False


# ── Convenience function ──────────────────────────────────────────────────────

def explain_decision(
    symbol: str,
    indicators: Dict[str, Any],
    skill: SkillProfile,
    regime_signal: RegimeSignal,
    skill_selection: Optional[SkillSelection] = None,
) -> DecisionExplanation:
    """
    Convenience function for evaluating and explaining a decision.

    Usage:
        explanation = explain_decision(symbol, indicators, skill, regime_signal)
        print(explanation.summary())
        print(explanation.decision)  # "BUY", "HOLD", "REJECT", "BLOCKED"
    """
    explainer = DecisionExplainer()
    return explainer.evaluate(symbol, indicators, skill, regime_signal, skill_selection)
