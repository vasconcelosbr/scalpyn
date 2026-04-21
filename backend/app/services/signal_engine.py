"""Signal Engine — evaluates trading entry conditions based on config rules."""

import logging
from typing import Dict, Any, List

from .rule_engine import RuleEngine

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
                "signal": True/False,
                "direction": "long"/"short"/None,
                "matched": [...],
                "failed_required": [...],
            }
        """
        if not indicators or not self.conditions:
            return {"signal": False, "direction": None, "matched": [], "failed_required": []}

        # Inject alpha_score into indicators for evaluation
        eval_data = {**indicators, "alpha_score": alpha_score}

        enabled_conditions = [c for c in self.conditions if c.get("enabled", True)]
        required_conditions = [c for c in enabled_conditions if c.get("required", False)]
        optional_conditions = [c for c in enabled_conditions if not c.get("required", False)]

        matched = []
        failed_required = []

        # Evaluate required conditions — ALL must pass
        for cond in required_conditions:
            if self._evaluate_condition(cond, eval_data):
                matched.append(cond.get("id", "?"))
            else:
                failed_required.append(cond.get("id", "?"))

        # If any required condition fails, no signal
        if failed_required:
            return {
                "signal": False,
                "direction": None,
                "matched": matched,
                "failed_required": failed_required,
            }

        # Evaluate optional conditions based on logic mode
        optional_matched = []
        for cond in optional_conditions:
            if self._evaluate_condition(cond, eval_data):
                optional_matched.append(cond.get("id", "?"))

        matched.extend(optional_matched)

        if self.logic == "AND":
            signal = len(optional_matched) == len(optional_conditions)
        elif self.logic == "OR":
            signal = len(optional_matched) > 0 or len(optional_conditions) == 0
        else:
            signal = False

        # Determine direction from indicator context
        direction = self._infer_direction(eval_data)

        return {
            "signal": signal,
            "direction": direction,
            "matched": matched,
            "failed_required": failed_required,
        }

    def _evaluate_condition(self, cond: Dict[str, Any], data: Dict[str, Any]) -> bool:
        passed, _ = self.rule_engine.evaluate_condition(cond, data, field_key="indicator")
        return passed

    def _infer_direction(self, data: Dict[str, Any]) -> str:
        """Infer trade direction from indicators."""
        rsi = data.get("rsi")
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
