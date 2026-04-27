"""Signal Engine — evaluates trading entry conditions based on config rules.

Missing-data policy (mirrors BlockEngine.evaluate_entry):
  A condition whose indicator is absent, NaN, or implausible is marked
  SKIPPED and never contributes to the signal decision.  Missing data
  must NEVER block a signal — that would produce false negatives driven
  by data gaps rather than by actual indicator state.

  - Required SKIPPED  → goes to ``skipped`` list, NOT ``failed_required``
  - Optional SKIPPED  → excluded from the AND/OR tally entirely
  - All SKIPPED       → signal is allowed (no data = no veto)
"""

import logging
from typing import Dict, Any, List

from .rule_engine import RuleEngine
from .indicator_validity import RuleStatus

logger = logging.getLogger(__name__)


class SignalEngine:
    """Evaluates trading signals (entry conditions) based on signal config."""

    def __init__(self, signal_config: Dict[str, Any]):
        self.config = signal_config
        self.logic = signal_config.get("logic", "AND")
        self.conditions = signal_config.get("conditions", [])
        self.rule_engine = RuleEngine()

    def evaluate(self, indicators: Dict[str, Any], alpha_score: float) -> Dict[str, Any]:
        """Evaluate all signal conditions.

        Returns:
            {
                "signal":          True/False,
                "direction":       "long"/"short"/None,
                "matched":         [...],
                "failed_required": [...],
                "skipped":         [...],
            }
        """
        if not indicators or not self.conditions:
            return {
                "signal": False,
                "direction": None,
                "matched": [],
                "failed_required": [],
                "skipped": [],
            }

        eval_data = {**indicators, "alpha_score": alpha_score}

        enabled_conditions  = [c for c in self.conditions if c.get("enabled", True)]
        required_conditions = [c for c in enabled_conditions if     c.get("required", False)]
        optional_conditions = [c for c in enabled_conditions if not c.get("required", False)]

        matched:         List[str] = []
        failed_required: List[str] = []
        skipped:         List[str] = []

        # ── Required conditions: ALL must pass; SKIPPED are quarantined ──────
        for cond in required_conditions:
            status = self._evaluate_condition_status(cond, eval_data)
            cond_id = cond.get("id", "?")
            if status == RuleStatus.PASS:
                matched.append(cond_id)
            elif status == RuleStatus.SKIPPED:
                skipped.append(cond_id)
            else:
                failed_required.append(cond_id)

        if failed_required:
            return {
                "signal":          False,
                "direction":       None,
                "matched":         matched,
                "failed_required": failed_required,
                "skipped":         skipped,
            }

        # ── Optional conditions: AND/OR over decidable results only ──────────
        optional_matched:  List[str] = []
        optional_decided:  int = 0

        for cond in optional_conditions:
            status = self._evaluate_condition_status(cond, eval_data)
            cond_id = cond.get("id", "?")
            if status == RuleStatus.PASS:
                optional_matched.append(cond_id)
                optional_decided += 1
            elif status == RuleStatus.FAIL:
                optional_decided += 1
            else:
                skipped.append(cond_id)

        matched.extend(optional_matched)

        if not optional_conditions or optional_decided == 0:
            # No optional conditions configured, or every optional was SKIPPED:
            # missing data must not block a signal.
            signal = True
        elif self.logic == "OR":
            signal = len(optional_matched) > 0
        else:
            # AND: every decidable optional condition must pass.
            signal = len(optional_matched) == optional_decided

        direction = self._infer_direction(eval_data)

        return {
            "signal":          signal,
            "direction":       direction,
            "matched":         matched,
            "failed_required": failed_required,
            "skipped":         skipped,
        }

    def _evaluate_condition_status(
        self, cond: Dict[str, Any], data: Dict[str, Any]
    ) -> RuleStatus:
        """Return tristate RuleStatus (PASS / FAIL / SKIPPED) for one condition."""
        status, _ = self.rule_engine.evaluate_condition_status(
            cond, data, field_key="indicator"
        )
        return status

    def _infer_direction(self, data: Dict[str, Any]) -> str:
        """Infer trade direction from indicators."""
        rsi         = data.get("rsi")
        macd_signal = data.get("macd_signal")
        ema_aligned = data.get("ema_full_alignment") or data.get("ema9_gt_ema50")

        bullish_signals = 0
        if rsi is not None and rsi < 50:
            bullish_signals += 1
        if macd_signal == "positive":
            bullish_signals += 1
        if ema_aligned:
            bullish_signals += 1

        return "long" if bullish_signals >= 2 else "short" if bullish_signals == 0 else "long"
