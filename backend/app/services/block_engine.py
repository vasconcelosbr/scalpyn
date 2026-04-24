"""Block Engine — evaluates blocking conditions that prevent trade execution.

Also evaluates entry_triggers (conditions absorbed from the former Signal Rules)
that must pass for a trade to be considered.

Indicator validity policy:
  A block whose indicator is missing, NaN, or implausible (e.g.
  ``taker_ratio == 0``) is marked SKIPPED and never contributes to the
  blocked decision. Missing data must NEVER block a trade — that would
  produce false negatives driven by data gaps rather than by signal.
"""

import logging
import operator as _op
from typing import Dict, Any, List, Optional

from .indicator_validity import RuleStatus, SkipReason, is_valid, log_skipped
from .rule_engine import RuleEngine

logger = logging.getLogger(__name__)

_CMP = {
    "<=": _op.le, ">=": _op.ge, "<": _op.lt, ">": _op.gt, "=": _op.eq, "!=": _op.ne,
}


def _aggregate_skip_reason(evaluated: List[tuple]) -> str:
    """Pick the most informative skip reason from a list of evaluated conditions.

    "indicator_invalid_value" wins over "indicator_not_available" so we
    never lose the fact that an indicator was actually present but
    implausible (e.g. taker_ratio == 0).
    """
    seen: List[str] = []
    for status, detail, _ in evaluated:
        if status != RuleStatus.SKIPPED:
            continue
        reason = detail.get("reason") if isinstance(detail, dict) else None
        if reason:
            seen.append(reason)
    if SkipReason.INDICATOR_INVALID_VALUE.value in seen:
        return SkipReason.INDICATOR_INVALID_VALUE.value
    if seen:
        return seen[0]
    return SkipReason.INDICATOR_NOT_AVAILABLE.value


class BlockEngine:
    """Evaluates blocking conditions from config. If ANY enabled block triggers, trade is blocked.

    SKIPPED blocks (those whose indicator is missing/invalid) NEVER count
    toward the blocked decision — they are reported separately so callers
    can surface them in traces and logs.
    """

    def __init__(self, block_config: Dict[str, Any]):
        self.config = block_config
        self.blocks = block_config.get("blocks", [])
        self.rule_engine = RuleEngine()

    def evaluate(self, indicators: Dict[str, Any]) -> Dict[str, Any]:
        """Check all block conditions.

        Returns:
            {
                "blocked": True/False,
                "triggered_blocks": [list of triggered block names],
                "skipped_blocks":   [list of names skipped for missing data],
                "details":          {block_id: reason for triggered blocks},
                "skipped_details":  {block_id: reason for skipped blocks},
            }
        """
        # Missing indicator data must NEVER block trades. Per the SKIPPED
        # policy every indicator-driven block whose input is unavailable
        # is reported as SKIPPED instead of triggered. We normalise an
        # empty payload to an empty dict and let the per-block validity
        # checks below mark each block as SKIPPED with the proper reason.
        if indicators is None:
            indicators = {}

        triggered: List[str] = []
        skipped: List[str] = []
        details: Dict[str, str] = {}
        skipped_details: Dict[str, str] = {}

        for block in self.blocks:
            if not block.get("enabled", True):
                continue

            block_id = block.get("id", "?")
            block_name = block.get("name", block_id)

            if block.get("conditions"):
                status, reason = self._evaluate_block_group(block, indicators)
                if status == RuleStatus.PASS:
                    triggered.append(block_name)
                    details[block_id] = reason
                elif status == RuleStatus.SKIPPED:
                    skipped.append(block_name)
                    skipped_details[block_id] = reason
                continue

            block_type = block.get("type", "threshold")
            indicator = block.get("indicator", "")

            # Legacy string-condition blocks (e.g. "ema9<ema50") don't use a
            # single named indicator field — they parse their own DSL inside
            # `_evaluate_string_condition`. Skip the indicator-validity gate
            # for them so they retain their existing behaviour. The DSL
            # evaluator already short-circuits to False on missing operands.
            if block_type == "condition":
                if self._evaluate_string_condition(block, indicators):
                    triggered.append(block_name)
                    details[block_id] = f"Condition '{block.get('condition')}' matched"
                continue

            actual = indicators.get(indicator)

            valid, skip_reason = is_valid(actual, indicator)
            if not valid:
                reason_value = (skip_reason or SkipReason.INDICATOR_NOT_AVAILABLE).value
                skipped.append(block_name)
                skipped_details[block_id] = reason_value
                log_skipped(indicator, actual, skip_reason or SkipReason.INDICATOR_NOT_AVAILABLE)
                continue

            try:
                actual = float(actual)
            except (ValueError, TypeError):
                # Non-numeric value on a non-condition block: treat as SKIPPED
                # so we never block on data we cannot interpret.
                skipped.append(block_name)
                skipped_details[block_id] = SkipReason.INDICATOR_INVALID_VALUE.value
                log_skipped(indicator, actual, SkipReason.INDICATOR_INVALID_VALUE)
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
            "skipped_blocks": skipped,
            "details": details,
            "skipped_details": skipped_details,
        }

    def _evaluate_block_group(
        self, block: Dict[str, Any], indicators: Dict[str, Any]
    ) -> tuple[RuleStatus, str]:
        """Evaluate a grouped block (multiple conditions joined by AND/OR).

        Returns a tristate:
          - PASS:    the block triggered (asset should be blocked).
          - FAIL:    the block did NOT trigger (asset is fine).
          - SKIPPED: the block could not be decided due to missing data.

        Semantics for missing data:
          - AND group: any SKIPPED condition makes the whole group SKIPPED
            (we cannot prove all conditions hold without that input).
          - OR group: SKIPPED conditions are ignored; the group is decided
            by the remaining conditions. If ALL conditions are SKIPPED the
            group is SKIPPED.
        """
        logic = str(block.get("logic", "AND")).upper()
        conditions = block.get("conditions", [])
        evaluated: List[tuple[RuleStatus, Dict[str, Any], Dict[str, Any]]] = []

        for condition in conditions:
            status, detail = self.rule_engine.evaluate_condition_status(
                condition, indicators, field_key="indicator"
            )
            evaluated.append((status, detail, condition))

        if not evaluated:
            return RuleStatus.FAIL, ""

        if logic == "OR":
            decided = [item for item in evaluated if item[0] != RuleStatus.SKIPPED]
            if not decided:
                return RuleStatus.SKIPPED, _aggregate_skip_reason(evaluated)
            is_triggered = any(status == RuleStatus.PASS for status, _, _ in decided)
        else:  # AND
            if any(status == RuleStatus.SKIPPED for status, _, _ in evaluated):
                return RuleStatus.SKIPPED, _aggregate_skip_reason(evaluated)
            is_triggered = all(status == RuleStatus.PASS for status, _, _ in evaluated)

        if not is_triggered:
            return RuleStatus.FAIL, ""

        matched_conditions = [
            self._describe_group_condition(condition, detail)
            for status, detail, condition in evaluated
            if status == RuleStatus.PASS
        ]
        return RuleStatus.PASS, "Matched: " + "; ".join(matched_conditions)

    @staticmethod
    def _describe_group_condition(condition: Dict[str, Any], detail: Dict[str, Any]) -> str:
        if condition.get("type") == "comparison":
            return f"{condition.get('left')} {condition.get('operator')} {condition.get('right')}"
        return (
            f"{condition.get('indicator')} {condition.get('operator')} "
            f"{condition.get('value', detail.get('target'))}"
        )

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

        SKIPPED triggers (missing/invalid indicator) are reported separately
        and never block entry. Required triggers that are SKIPPED do not go
        into ``failed_required``; optional SKIPPED triggers are excluded from
        the AND/OR tally so the remaining conditions decide. If every enabled
        trigger is SKIPPED, entry is still allowed — missing data must not
        block trades.

        Returns:
            {
                "allowed": bool,
                "matched": list[str],
                "failed_required": list[str],
                "skipped":          list[str],
            }
        """
        raw_entry_triggers = self.config.get("entry_triggers", [])
        if isinstance(raw_entry_triggers, dict):
            entry_triggers = raw_entry_triggers.get("conditions", [])
            logic = raw_entry_triggers.get("logic", self.config.get("entry_logic", "AND"))
        else:
            entry_triggers = raw_entry_triggers
            logic = self.config.get("entry_logic", "AND")
        if not entry_triggers:
            # No entry triggers configured → allow by default
            return {"allowed": True, "matched": [], "failed_required": [], "skipped": []}

        eval_data = {**indicators, "alpha_score": alpha_score}

        enabled = [t for t in entry_triggers if t.get("enabled", True)]
        required = [t for t in enabled if t.get("required", False)]
        optional = [t for t in enabled if not t.get("required", False)]

        matched: list = []
        failed_required: list = []
        skipped: list = []

        for cond in required:
            status = self._eval_trigger_status(cond, eval_data)
            cond_id = cond.get("id", "?")
            if status == RuleStatus.PASS:
                matched.append(cond_id)
            elif status == RuleStatus.SKIPPED:
                skipped.append(cond_id)
            else:
                failed_required.append(cond_id)

        if failed_required:
            return {
                "allowed": False,
                "matched": matched,
                "failed_required": failed_required,
                "skipped": skipped,
            }

        optional_matched: list = []
        optional_decided = 0
        for cond in optional:
            status = self._eval_trigger_status(cond, eval_data)
            cond_id = cond.get("id", "?")
            if status == RuleStatus.PASS:
                optional_matched.append(cond_id)
                optional_decided += 1
            elif status == RuleStatus.FAIL:
                optional_decided += 1
            else:
                skipped.append(cond_id)

        matched.extend(optional_matched)

        if not optional or optional_decided == 0:
            # No optional triggers OR every optional trigger was SKIPPED:
            # entry is allowed because missing data must not block trades.
            allowed = True
        elif logic == "OR":
            allowed = len(optional_matched) > 0
        else:
            # AND semantics across the optional group, ignoring SKIPPED ones:
            # every decidable optional condition must PASS.
            allowed = len(optional_matched) == optional_decided

        return {
            "allowed": allowed,
            "matched": matched,
            "failed_required": [],
            "skipped": skipped,
        }

    def _eval_trigger(self, cond: Dict[str, Any], data: Dict[str, Any]) -> bool:
        passed, _ = self.rule_engine.evaluate_condition(cond, data, field_key="indicator")
        return passed

    def _eval_trigger_status(self, cond: Dict[str, Any], data: Dict[str, Any]) -> RuleStatus:
        status, _ = self.rule_engine.evaluate_condition_status(cond, data, field_key="indicator")
        return status
