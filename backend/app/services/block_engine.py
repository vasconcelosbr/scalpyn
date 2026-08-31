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

from .indicator_validity import RuleStatus, SkipReason, is_valid, log_skipped, unwrap_envelope_value
from .l3_gate_runtime_policy import KNOWN_UNIMPLEMENTED_INDICATORS
from .rule_engine import RuleEngine
from .block_rule_compiler import compile_block_rule

logger = logging.getLogger(__name__)

_CMP = {
    "<=": _op.le, ">=": _op.ge, "<": _op.lt, ">": _op.gt, "=": _op.eq, "!=": _op.ne,
}


def _entry_trigger_identifier(condition: Dict[str, Any]) -> str:
    """Return an auditable identifier even when UI-authored rules omit id."""
    return str(
        condition.get("id")
        or condition.get("indicator")
        or condition.get("field")
        or condition.get("left")
        or "?"
    )


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
    if SkipReason.RULE_DISABLED_MISSING_INDICATOR.value in seen:
        return SkipReason.RULE_DISABLED_MISSING_INDICATOR.value
    if SkipReason.INDICATOR_NOT_IMPLEMENTED.value in seen:
        return SkipReason.INDICATOR_NOT_IMPLEMENTED.value
    if SkipReason.ZERO_NOT_ALLOWED.value in seen:
        return SkipReason.ZERO_NOT_ALLOWED.value
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

    def __init__(
        self,
        block_config: Dict[str, Any],
        *,
        condition_status_capture: bool = True,
        zero_is_value: bool = False,
        and_skipped_policy: str = "legacy",
        missing_indicator_policy: str = "warn",
        legacy_range_compiler_enabled: bool = False,
    ):
        self.config = block_config
        self.blocks = block_config.get("blocks", [])
        self.condition_status_capture = bool(condition_status_capture)
        self.zero_is_value = bool(zero_is_value)
        self.and_skipped_policy = and_skipped_policy
        self.missing_indicator_policy = missing_indicator_policy
        self.legacy_range_compiler_enabled = bool(legacy_range_compiler_enabled)
        self.rule_engine = RuleEngine(
            zero_is_value=self.zero_is_value,
            missing_indicator_policy=missing_indicator_policy,
        )

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
        rule_audits: List[Dict[str, Any]] = []

        for configured_block in self.blocks:
            compilation = compile_block_rule(
                configured_block,
                legacy_range_enabled=self.legacy_range_compiler_enabled,
            )
            block = compilation["compiled"]
            block_id = block.get("id", "?")
            block_name = block.get("name", block_id)
            rule_audit: Dict[str, Any] = {
                "id": block_id,
                "name": block_name,
                "enabled": bool(block.get("enabled", True)),
                "logic": str(block.get("logic", "AND")).upper(),
                "timeframe": block.get("timeframe"),
                "evaluated": False,
                "matched": False,
                "status": "DISABLED",
                "conditions": [],
                "audit_contract_version": compilation["contract_version"],
                "normalization_version": compilation["normalization_version"],
                "normalization": compilation["normalization"],
                "configured_rule": compilation["configured"],
                "compiled_rule": compilation["compiled"],
                "operational_effect": compilation["operational_effect"],
            }
            if not block.get("enabled", True):
                rule_audits.append(rule_audit)
                continue

            if block.get("conditions"):
                status, reason, condition_audits = self._evaluate_block_group(
                    block, indicators
                )
                rule_audit.update(
                    {
                        "evaluated": status != RuleStatus.SKIPPED,
                        "matched": status == RuleStatus.PASS,
                        "status": (
                            "DISABLED"
                            if reason
                            == SkipReason.RULE_DISABLED_MISSING_INDICATOR.value
                            else status.value
                        ),
                        "reason_code": reason or None,
                        "conditions": condition_audits,
                    }
                )
                if status == RuleStatus.PASS:
                    triggered.append(block_name)
                    details[block_id] = reason
                elif status == RuleStatus.SKIPPED:
                    skipped.append(block_name)
                    skipped_details[block_id] = reason
                rule_audits.append(rule_audit)
                continue

            block_type = block.get("type", "threshold")
            indicator = block.get("indicator", "")

            # Legacy string-condition blocks (e.g. "ema9<ema50") don't use a
            # single named indicator field — they parse their own DSL inside
            # `_evaluate_string_condition`. Skip the indicator-validity gate
            # for them so they retain their existing behaviour. The DSL
            # evaluator already short-circuits to False on missing operands.
            if block_type == "condition":
                is_triggered = self._evaluate_string_condition(block, indicators)
                rule_audit.update(
                    {
                        "evaluated": True,
                        "matched": is_triggered,
                        "status": (
                            RuleStatus.PASS.value
                            if is_triggered
                            else RuleStatus.FAIL.value
                        ),
                        "conditions": [
                            {
                                "type": "condition",
                                "expression": block.get("condition"),
                                "result": is_triggered,
                                "status": (
                                    RuleStatus.PASS.value
                                    if is_triggered
                                    else RuleStatus.FAIL.value
                                ),
                            }
                        ],
                    }
                )
                if is_triggered:
                    triggered.append(block_name)
                    details[block_id] = f"Condition '{block.get('condition')}' matched"
                rule_audits.append(rule_audit)
                continue

            actual = unwrap_envelope_value(indicators.get(indicator))

            valid, skip_reason = is_valid(
                actual, indicator, zero_is_value=self.zero_is_value
            )
            if (
                not valid
                and skip_reason == SkipReason.INDICATOR_NOT_AVAILABLE
                and indicator in KNOWN_UNIMPLEMENTED_INDICATORS
            ):
                skip_reason = (
                    SkipReason.RULE_DISABLED_MISSING_INDICATOR
                    if self.missing_indicator_policy == "disable_rule"
                    else SkipReason.INDICATOR_NOT_IMPLEMENTED
                )
            if not valid:
                reason_value = (skip_reason or SkipReason.INDICATOR_NOT_AVAILABLE).value
                skipped.append(block_name)
                skipped_details[block_id] = reason_value
                log_skipped(indicator, actual, skip_reason or SkipReason.INDICATOR_NOT_AVAILABLE)
                rule_audit.update(
                    {
                        "status": (
                            "DISABLED"
                            if reason_value
                            == SkipReason.RULE_DISABLED_MISSING_INDICATOR.value
                            else RuleStatus.SKIPPED.value
                        ),
                        "conditions": [
                            self._legacy_condition_audit(
                                block,
                                actual,
                                result=None,
                                status=RuleStatus.SKIPPED,
                                reason_code=reason_value,
                            )
                        ],
                    }
                )
                rule_audits.append(rule_audit)
                continue

            try:
                actual = float(actual)
            except (ValueError, TypeError):
                # Non-numeric value on a non-condition block: treat as SKIPPED
                # so we never block on data we cannot interpret.
                skipped.append(block_name)
                skipped_details[block_id] = SkipReason.INDICATOR_INVALID_VALUE.value
                log_skipped(indicator, actual, SkipReason.INDICATOR_INVALID_VALUE)
                rule_audit.update(
                    {
                        "status": RuleStatus.SKIPPED.value,
                        "conditions": [
                            self._legacy_condition_audit(
                                block,
                                actual,
                                result=None,
                                status=RuleStatus.SKIPPED,
                                reason_code=SkipReason.INDICATOR_INVALID_VALUE.value,
                            )
                        ],
                    }
                )
                rule_audits.append(rule_audit)
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
            rule_audit.update(
                {
                    "evaluated": True,
                    "matched": is_triggered,
                    "status": (
                        RuleStatus.PASS.value
                        if is_triggered
                        else RuleStatus.FAIL.value
                    ),
                    "conditions": [
                        self._legacy_condition_audit(
                            block,
                            actual,
                            result=not is_triggered,
                            status=(
                                RuleStatus.FAIL
                                if is_triggered
                                else RuleStatus.PASS
                            ),
                        )
                    ],
                }
            )
            rule_audits.append(rule_audit)

        triggered_names = set(triggered)
        for audit in rule_audits:
            status = audit.get("status")
            condition_matched = (
                None if status in {RuleStatus.SKIPPED.value, "DISABLED"}
                else bool(audit.get("matched"))
            )
            audit["condition_matched"] = condition_matched
            audit["rule_matched"] = condition_matched
            audit["blocked"] = audit.get("name") in triggered_names
        return {
            "blocked": len(triggered) > 0,
            "triggered_blocks": triggered,
            "skipped_blocks": skipped,
            "details": details,
            "skipped_details": skipped_details,
            "configured": bool(self.blocks),
            "evaluated_blocks": [
                audit["name"] for audit in rule_audits if audit["evaluated"]
            ],
            "matched_blocks": triggered,
            "blocked_by": triggered,
            "rules": rule_audits if self.condition_status_capture else [],
            "condition_status_capture": self.condition_status_capture,
            "and_skipped_policy": self.and_skipped_policy,
            "missing_indicator_policy": self.missing_indicator_policy,
            "block_rule_audit_contract_version": "block_rule_audit_v2",
            "legacy_range_compiler_enabled": self.legacy_range_compiler_enabled,
        }

    def _evaluate_block_group(
        self, block: Dict[str, Any], indicators: Dict[str, Any]
    ) -> tuple[RuleStatus, str, List[Dict[str, Any]]]:
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
            indicator = str(
                condition.get("indicator")
                or condition.get("field")
                or condition.get("left")
                or ""
            )
            if (
                status == RuleStatus.SKIPPED
                and detail.get("reason")
                == SkipReason.INDICATOR_NOT_AVAILABLE.value
                and indicator in KNOWN_UNIMPLEMENTED_INDICATORS
            ):
                detail["reason"] = (
                    SkipReason.RULE_DISABLED_MISSING_INDICATOR.value
                    if self.missing_indicator_policy == "disable_rule"
                    else SkipReason.INDICATOR_NOT_IMPLEMENTED.value
                )
                if self.missing_indicator_policy == "warn":
                    logger.warning(
                        "[L3_BLOCK_RULE] indicator_not_implemented indicator=%s rule_id=%s",
                        indicator,
                        block.get("id"),
                    )
            evaluated.append((status, detail, condition))

        condition_audits = [
            self._group_condition_audit(condition, detail, status)
            for status, detail, condition in evaluated
        ]

        if not evaluated:
            return RuleStatus.FAIL, "", condition_audits

        if logic == "OR":
            decided = [item for item in evaluated if item[0] != RuleStatus.SKIPPED]
            if not decided:
                return (
                    RuleStatus.SKIPPED,
                    _aggregate_skip_reason(evaluated),
                    condition_audits,
                )
            is_triggered = any(status == RuleStatus.PASS for status, _, _ in decided)
        else:  # AND
            if any(status == RuleStatus.SKIPPED for status, _, _ in evaluated):
                if self.and_skipped_policy == "not_satisfied":
                    return RuleStatus.FAIL, "", condition_audits
                return (
                    RuleStatus.SKIPPED,
                    _aggregate_skip_reason(evaluated),
                    condition_audits,
                )
            is_triggered = all(status == RuleStatus.PASS for status, _, _ in evaluated)

        if not is_triggered:
            return RuleStatus.FAIL, "", condition_audits

        matched_conditions = [
            self._describe_group_condition(condition, detail)
            for status, detail, condition in evaluated
            if status == RuleStatus.PASS
        ]
        return (
            RuleStatus.PASS,
            "Matched: " + "; ".join(matched_conditions),
            condition_audits,
        )

    @staticmethod
    def _group_condition_audit(
        condition: Dict[str, Any], detail: Dict[str, Any], status: RuleStatus
    ) -> Dict[str, Any]:
        expected: Any
        if condition.get("operator") == "between":
            expected = {"min": condition.get("min"), "max": condition.get("max")}
        elif condition.get("type") == "comparison":
            expected = detail.get("target")
        else:
            expected = condition.get("value", detail.get("target"))
        return {
            "indicator": condition.get("indicator"),
            "left": condition.get("left"),
            "right": condition.get("right"),
            "operator": condition.get("operator"),
            "expected": expected,
            "actual": detail.get("actual"),
            "result": status == RuleStatus.PASS,
            "status": status.value,
            "reason_code": detail.get("reason"),
        }

    @staticmethod
    def _legacy_condition_audit(
        block: Dict[str, Any],
        actual: Any,
        *,
        result: Any,
        status: RuleStatus,
        reason_code: Optional[str] = None,
    ) -> Dict[str, Any]:
        expected: Any = block.get("value")
        if block.get("type") == "range":
            expected = {"min": block.get("min", 0), "max": block.get("max", 100)}
        return {
            "indicator": block.get("indicator"),
            "operator": block.get("operator"),
            "expected": expected,
            "actual": actual,
            "result": result,
            "status": status.value,
            "reason_code": reason_code,
        }

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
        # Unwrap envelope so a payload like ``{"value": True, "status": "VALID"}``
        # is interpreted by its boolean value, not as the truthiness of a dict
        # (which would always be True and silently break the EMA-cross logic).
        ema_flag = unwrap_envelope_value(indicators.get("ema9_gt_ema50"))
        if condition == "ema9<ema50":
            return not bool(ema_flag if ema_flag is not None else True)
        elif condition == "ema9>ema50":
            return bool(ema_flag if ema_flag is not None else False)
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
            cond_id = _entry_trigger_identifier(cond)
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
            cond_id = _entry_trigger_identifier(cond)
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
