"""Block Engine — evaluates blocking conditions that prevent trade execution.

Also evaluates entry_triggers (conditions absorbed from the former Signal Rules)
that must pass for a trade to be considered.
"""

import logging
import operator as _op
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

_CMP = {
    "<=": _op.le, ">=": _op.ge, "<": _op.lt, ">": _op.gt, "=": _op.eq, "!=": _op.ne,
}


class BlockEngine:
    """Evaluates blocking conditions from config. If ANY enabled block triggers, trade is blocked."""

    def __init__(self, block_config: Dict[str, Any]):
        self.config = block_config
        self.blocks = block_config.get("blocks", [])

    def evaluate(self, indicators: Dict[str, Any]) -> Dict[str, Any]:
        """Check all block conditions.

        Returns:
            {
                "blocked": True/False,
                "triggered_blocks": [list of triggered block names],
                "details": {block_id: reason}
            }
        """
        if not indicators:
            return {"blocked": True, "triggered_blocks": ["no_data"], "details": {"no_data": "No indicator data available"}}

        triggered = []
        details = {}

        for block in self.blocks:
            if not block.get("enabled", True):
                continue

            block_id = block.get("id", "?")
            block_name = block.get("name", block_id)
            block_type = block.get("type", "threshold")
            indicator = block.get("indicator", "")

            actual = indicators.get(indicator)
            if actual is None:
                continue

            try:
                actual = float(actual)
            except (ValueError, TypeError):
                # Handle string-based conditions
                if block_type == "condition":
                    if self._evaluate_string_condition(block, indicators):
                        triggered.append(block_name)
                        details[block_id] = f"Condition '{block.get('condition')}' matched"
                continue

            is_triggered = False
            reason = ""

            if block_type == "range":
                min_val = block.get("min", 0)
                max_val = block.get("max", 100)
                # Block if OUTSIDE the acceptable range
                if actual < min_val or actual > max_val:
                    is_triggered = True
                    reason = f"{indicator}={actual:.2f} outside range [{min_val}, {max_val}]"

            elif block_type == "threshold":
                operator_str = block.get("operator", ">")
                value = block.get("value", 0)
                # Block if condition is NOT met (threshold is the minimum requirement)
                if operator_str == ">" and actual <= value:
                    is_triggered = True
                    reason = f"{indicator}={actual:.2f} not > {value}"
                elif operator_str == ">=" and actual < value:
                    is_triggered = True
                    reason = f"{indicator}={actual:.2f} not >= {value}"
                elif operator_str == "<" and actual >= value:
                    is_triggered = True
                    reason = f"{indicator}={actual:.2f} not < {value}"
                elif operator_str == "<=" and actual > value:
                    is_triggered = True
                    reason = f"{indicator}={actual:.2f} not <= {value}"

            if is_triggered:
                triggered.append(block_name)
                details[block_id] = reason

        return {
            "blocked": len(triggered) > 0,
            "triggered_blocks": triggered,
            "details": details,
        }

    def _evaluate_string_condition(self, block: Dict, indicators: Dict) -> bool:
        condition = block.get("condition", "")
        if condition == "ema9<ema50":
            return not bool(indicators.get("ema9_gt_ema50", True))
        elif condition == "ema9>ema50":
            return bool(indicators.get("ema9_gt_ema50", False))
        return False

    # ── Entry Triggers (absorbed from Signal Rules) ───────────────────────────

    def evaluate_entry(self, indicators: Dict[str, Any], alpha_score: float = 0.0) -> Dict[str, Any]:
        """Evaluate entry trigger conditions (absorbed from former Signal Rules).

        Entry triggers must ALL pass (required) or satisfy the configured logic
        (optional) for the trade to be allowed. This is the positive gate, in
        contrast to `evaluate()` which is the negative (blocking) gate.

        Returns:
            {
                "allowed": bool,
                "matched": list[str],
                "failed_required": list[str],
            }
        """
        entry_triggers = self.config.get("entry_triggers", [])
        if not entry_triggers:
            # No entry triggers configured → allow by default
            return {"allowed": True, "matched": [], "failed_required": []}

        logic = self.config.get("entry_logic", "AND")
        eval_data = {**indicators, "alpha_score": alpha_score}

        enabled = [t for t in entry_triggers if t.get("enabled", True)]
        required = [t for t in enabled if t.get("required", False)]
        optional = [t for t in enabled if not t.get("required", False)]

        matched: list = []
        failed_required: list = []

        for cond in required:
            if self._eval_trigger(cond, eval_data):
                matched.append(cond.get("id", "?"))
            else:
                failed_required.append(cond.get("id", "?"))

        if failed_required:
            return {"allowed": False, "matched": matched, "failed_required": failed_required}

        optional_matched: list = []
        for cond in optional:
            if self._eval_trigger(cond, eval_data):
                optional_matched.append(cond.get("id", "?"))

        matched.extend(optional_matched)

        if not optional:
            allowed = True
        elif logic == "OR":
            allowed = len(optional_matched) > 0
        else:
            allowed = len(optional_matched) > 0  # AND: at least one optional

        return {"allowed": allowed, "matched": matched, "failed_required": []}

    def _eval_trigger(self, cond: Dict[str, Any], data: Dict[str, Any]) -> bool:
        indicator = cond.get("indicator", "")
        operator_str = cond.get("operator", "")
        target = cond.get("value")

        actual = data.get(indicator)
        if actual is None:
            return False

        # Handle "between" operator (range check)
        if operator_str == "between":
            try:
                actual_f = float(actual)
                min_val = float(cond.get("min", 0))
                max_val = float(cond.get("max", 0))
                if min_val > max_val:
                    logger.warning("between trigger %s: min (%s) > max (%s), swapping",
                                   indicator, min_val, max_val)
                    min_val, max_val = max_val, min_val
                return min_val <= actual_f <= max_val
            except (ValueError, TypeError):
                return False

        if isinstance(target, str):
            if operator_str == "=":
                return str(actual) == target
            if operator_str == "!=":
                return str(actual) != target
            return False

        try:
            actual_f = float(actual)
            target_f = float(target) if target is not None else 0.0
        except (ValueError, TypeError):
            return False

        op_func = _CMP.get(operator_str)
        return op_func(actual_f, target_f) if op_func else False
