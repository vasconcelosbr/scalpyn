"""
Skill Selector — Automatic trading strategy selection based on market regime.

The SkillSelector bridges MarketRegimeEngine and SkillProfiles:
  1. Receives the current regime signal
  2. Evaluates which skills have affinity for this regime
  3. Ranks skills by: regime affinity match + historical performance
  4. Returns the optimal skill for the current market conditions

Supports two modes:
  - AI Adaptive (default): automatic selection based on regime + performance
  - Manual: user selects a fixed skill regardless of regime

Author: Market Skills Engine v1
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .market_regime_engine import MarketRegime, RegimeSignal
from .skill_profiles import SkillProfile, load_user_skills, get_skill_template

logger = logging.getLogger(__name__)


# ── Selection Result ──────────────────────────────────────────────────────────

@dataclass
class SkillSelection:
    """Result of skill selection."""
    selected_skill: SkillProfile
    regime: RegimeSignal
    selection_mode: str = "ai_adaptive"     # ai_adaptive | manual
    selection_reason: str = ""
    candidates: List[Dict[str, Any]] = field(default_factory=list)  # all scored candidates
    confidence: float = 1.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "selected_skill": self.selected_skill.skill_key,
            "skill_name": self.selected_skill.name,
            "regime": self.regime.regime.value,
            "regime_confidence": self.regime.confidence,
            "selection_mode": self.selection_mode,
            "selection_reason": self.selection_reason,
            "candidates": self.candidates,
            "confidence": round(self.confidence, 3),
        }


# ── Default Regime→Skill Mapping ─────────────────────────────────────────────

DEFAULT_REGIME_SKILL_MAP: Dict[MarketRegime, List[str]] = {
    MarketRegime.SIDEWAYS:         ["mean_reversion", "scalping"],
    MarketRegime.TRENDING_BULL:    ["trend_following", "breakout_hunter"],
    MarketRegime.TRENDING_BEAR:    ["swing_trading"],     # defensive / short bias
    MarketRegime.BREAKOUT:         ["breakout_hunter", "trend_following"],
    MarketRegime.HIGH_VOLATILITY:  ["scalping", "mean_reversion"],
    MarketRegime.LOW_VOLATILITY:   ["swing_trading", "mean_reversion"],
}


# ── SkillSelector ─────────────────────────────────────────────────────────────

class SkillSelector:
    """
    Selects the optimal trading skill for the current market regime.

    AI Adaptive mode:
      1. Gets available skills (from DB or templates)
      2. Scores each skill based on:
         a. Regime affinity match (does this skill suit the current regime?)
         b. Historical performance in this regime (from performance_history)
         c. Default mapping priority
      3. Returns the highest-scoring skill

    Manual mode:
      Returns the user-specified skill, ignoring regime.
    """

    def __init__(self, mode: str = "ai_adaptive", manual_skill_key: Optional[str] = None):
        self.mode = mode
        self.manual_skill_key = manual_skill_key

    async def select(
        self,
        regime_signal: RegimeSignal,
        db: AsyncSession,
        user_id: str,
    ) -> SkillSelection:
        """Select the optimal skill for the current regime and user."""

        if self.mode == "manual" and self.manual_skill_key:
            return await self._select_manual(regime_signal, db, user_id)

        return await self._select_adaptive(regime_signal, db, user_id)

    async def _select_adaptive(
        self,
        regime_signal: RegimeSignal,
        db: AsyncSession,
        user_id: str,
    ) -> SkillSelection:
        """AI Adaptive: score all skills and pick the best."""
        skills = await load_user_skills(db, user_id)
        regime = regime_signal.regime

        if not skills:
            # Absolute fallback: use mean_reversion
            fallback = get_skill_template("mean_reversion")
            return SkillSelection(
                selected_skill=fallback,
                regime=regime_signal,
                selection_reason="No skills available — using mean_reversion fallback",
                confidence=0.3,
            )

        # Score each skill
        candidates: List[Tuple[str, float, SkillProfile, str]] = []

        for skill_key, skill in skills.items():
            score, reason = self._score_skill(skill, regime, regime_signal.confidence)
            candidates.append((skill_key, score, skill, reason))

        # Sort by score (descending)
        candidates.sort(key=lambda x: x[1], reverse=True)

        best_key, best_score, best_skill, best_reason = candidates[0]

        # Build candidates list for explainability
        candidate_list = [
            {
                "skill_key": key,
                "skill_name": skill.name,
                "score": round(score, 1),
                "reason": reason,
                "selected": key == best_key,
            }
            for key, score, skill, reason in candidates
        ]

        logger.info(
            "[SkillSelector] Regime=%s → Selected=%s (score=%.1f, reason=%s)",
            regime.value, best_key, best_score, best_reason,
        )

        return SkillSelection(
            selected_skill=best_skill,
            regime=regime_signal,
            selection_mode="ai_adaptive",
            selection_reason=best_reason,
            candidates=candidate_list,
            confidence=min(1.0, best_score / 100.0),
        )

    async def _select_manual(
        self,
        regime_signal: RegimeSignal,
        db: AsyncSession,
        user_id: str,
    ) -> SkillSelection:
        """Manual: return the user-specified skill."""
        skills = await load_user_skills(db, user_id)
        skill = skills.get(self.manual_skill_key)

        if not skill:
            # Try from templates
            skill = get_skill_template(self.manual_skill_key)

        if not skill:
            # Absolute fallback
            skill = get_skill_template("mean_reversion")
            logger.warning(
                "[SkillSelector] Manual skill '%s' not found, using mean_reversion",
                self.manual_skill_key,
            )

        return SkillSelection(
            selected_skill=skill,
            regime=regime_signal,
            selection_mode="manual",
            selection_reason=f"Manual selection: {skill.name}",
            confidence=1.0,
        )

    def _score_skill(
        self,
        skill: SkillProfile,
        regime: MarketRegime,
        regime_confidence: float,
    ) -> Tuple[float, str]:
        """
        Score a skill for the current regime.

        Scoring components:
          - Regime affinity match: 0-50 points
          - Default mapping priority: 0-30 points
          - Historical performance: 0-20 points
        """
        score = 0.0
        reasons: List[str] = []

        # 1. Regime Affinity Match (0-50 points)
        if regime in skill.regime_affinity:
            affinity_score = 40 + (regime_confidence * 10)
            score += affinity_score
            reasons.append(f"regime_affinity={affinity_score:.0f}")
        else:
            # Partial credit if regime is "close" to an affinity regime
            partial = self._partial_affinity(skill, regime)
            if partial > 0:
                score += partial
                reasons.append(f"partial_affinity={partial:.0f}")

        # 2. Default Mapping Priority (0-30 points)
        default_skills = DEFAULT_REGIME_SKILL_MAP.get(regime, [])
        if skill.skill_key in default_skills:
            idx = default_skills.index(skill.skill_key)
            mapping_score = 30 - (idx * 10)  # 30 for primary, 20 for secondary
            score += max(mapping_score, 0)
            reasons.append(f"default_mapping={mapping_score:.0f}")

        # 3. Historical Performance (0-20 points)
        perf = skill.performance_history.get(regime.value, {})
        if perf:
            ev = float(perf.get("ev", 0))
            wr = float(perf.get("wr", 0))
            n = int(perf.get("n", 0))

            if n >= 10:  # minimum sample size
                if ev > 0 and wr > 0.5:
                    perf_score = min(20, ev * 10 + (wr - 0.5) * 20)
                    score += perf_score
                    reasons.append(f"perf_ev={ev:.2f}%_wr={wr:.1%}")
                elif ev < 0:
                    penalty = max(-15, ev * 5)
                    score += penalty
                    reasons.append(f"perf_penalty={penalty:.0f}")

        return score, " | ".join(reasons) if reasons else "no_signals"

    @staticmethod
    def _partial_affinity(skill: SkillProfile, regime: MarketRegime) -> float:
        """
        Gives partial credit for "close" regimes.
        E.g., a Trend Following skill gets partial credit in BREAKOUT.
        """
        _CLOSE_REGIMES: Dict[MarketRegime, List[MarketRegime]] = {
            MarketRegime.TRENDING_BULL: [MarketRegime.BREAKOUT, MarketRegime.LOW_VOLATILITY],
            MarketRegime.BREAKOUT: [MarketRegime.TRENDING_BULL, MarketRegime.HIGH_VOLATILITY],
            MarketRegime.SIDEWAYS: [MarketRegime.LOW_VOLATILITY],
            MarketRegime.HIGH_VOLATILITY: [MarketRegime.BREAKOUT],
            MarketRegime.LOW_VOLATILITY: [MarketRegime.SIDEWAYS, MarketRegime.TRENDING_BULL],
            MarketRegime.TRENDING_BEAR: [],
        }

        for affinity_regime in skill.regime_affinity:
            close_to = _CLOSE_REGIMES.get(affinity_regime, [])
            if regime in close_to:
                return 15.0  # partial credit
        return 0.0


# ── Convenience function ──────────────────────────────────────────────────────

async def select_skill_for_regime(
    regime_signal: RegimeSignal,
    db: AsyncSession,
    user_id: str,
    mode: str = "ai_adaptive",
    manual_skill_key: Optional[str] = None,
) -> SkillSelection:
    """
    Convenience function for selecting a skill.

    Usage:
        from .skill_selector import select_skill_for_regime
        selection = await select_skill_for_regime(regime_signal, db, user_id)
        skill = selection.selected_skill
    """
    selector = SkillSelector(mode=mode, manual_skill_key=manual_skill_key)
    return await selector.select(regime_signal, db, user_id)
