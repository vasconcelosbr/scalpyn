"""Rule Engine — generic condition evaluator for dynamic rule processing."""

import logging
import operator as op
from typing import Dict, Any, List, Optional, Union
from enum import Enum

logger = logging.getLogger(__name__)


class Operator(Enum):
    """Supported comparison operators."""
    EQ = "=="
    NE = "!="
    GT = ">"
    GTE = ">="
    LT = "<"
    LTE = "<="
    BETWEEN = "between"
    IN = "in"
    NOT_IN = "not_in"
    CONTAINS = "contains"
    IS_TRUE = "is_true"
    IS_FALSE = "is_false"


OPERATOR_MAP = {
    "==": op.eq,
    "=": op.eq,
    "!=": op.ne,
    ">": op.gt,
    ">=": op.ge,
    "<": op.lt,
    "<=": op.le,
}


class RuleEngine:
    """
    Generic rule evaluation engine.
    
    Supports:
    - Numeric comparisons (>, <, >=, <=, ==, !=)
    - Boolean checks (is_true, is_false)
    - Range checks (between)
    - Set membership (in, not_in)
    - AND/OR logic groups
    - Nested condition groups
    
    Usage:
        engine = RuleEngine()
        result = engine.evaluate(conditions, data, logic="AND")
    """
    
    def __init__(self):
        self.last_evaluation_details = []
    
    def evaluate(
        self,
        conditions: List[Dict[str, Any]],
        data: Dict[str, Any],
        logic: str = "AND"
    ) -> Dict[str, Any]:
        """
        Evaluate a list of conditions against data.
        
        Args:
            conditions: List of condition dicts with {field, operator, value}
            data: Dict of field values to evaluate against
            logic: "AND" or "OR" - how to combine condition results
            
        Returns:
            {
                "passed": bool,
                "matched": List[str],  # Matched condition IDs/fields
                "failed": List[str],   # Failed condition IDs/fields
                "details": List[Dict]  # Detailed evaluation results
            }
        """
        if not conditions:
            return {"passed": True, "matched": [], "failed": [], "details": []}
        
        self.last_evaluation_details = []
        matched = []
        failed = []
        details = []
        
        for cond in conditions:
            # Handle nested groups
            if "group" in cond:
                group_result = self.evaluate(
                    cond.get("conditions", []),
                    data,
                    cond.get("logic", "AND")
                )
                result = group_result["passed"]
                detail = {
                    "type": "group",
                    "logic": cond.get("logic", "AND"),
                    "passed": result,
                    "nested": group_result
                }
            else:
                result, detail = self._evaluate_single_condition(cond, data)
            
            details.append(detail)
            cond_id = cond.get("id", cond.get("field") or cond.get("left") or "unknown")
            
            if result:
                matched.append(cond_id)
            else:
                failed.append(cond_id)
        
        self.last_evaluation_details = details
        
        # Apply logic
        if logic.upper() == "AND":
            passed = len(failed) == 0
        elif logic.upper() == "OR":
            passed = len(matched) > 0
        else:
            passed = len(failed) == 0  # Default to AND
        
        return {
            "passed": passed,
            "matched": matched,
            "failed": failed,
            "details": details
        }

    def evaluate_condition(
        self,
        condition: Dict[str, Any],
        data: Dict[str, Any],
        field_key: str = "field",
    ) -> tuple[bool, Dict[str, Any]]:
        """Evaluate a single condition with optional field alias support."""
        normalized = dict(condition)
        if (
            field_key != "field"
            and "field" not in normalized
            and normalized.get("type") != "comparison"
        ):
            normalized["field"] = normalized.get(field_key, "")
        return self._evaluate_single_condition(normalized, data)
    
    def _evaluate_single_condition(
        self,
        condition: Dict[str, Any],
        data: Dict[str, Any]
    ) -> tuple:
        """
        Evaluate a single condition.
        
        Returns:
            (passed: bool, detail: dict)
        """
        condition_type = (condition.get("type") or "threshold").lower()
        field = condition.get("field", "")
        operator_str = condition.get("operator", "==")
        if condition_type == "comparison":
            left_field = condition.get("left", "")
            right_field = condition.get("right", "")
            actual_value = self._get_nested_value(data, left_field)
            target_value = self._get_nested_value(data, right_field)
            detail = {
                "type": "comparison",
                "left": left_field,
                "right": right_field,
                "operator": operator_str,
                "target": target_value,
                "actual": actual_value,
                "passed": False,
            }
            if actual_value is None or target_value is None:
                detail["reason"] = "operand_not_found"
                return False, detail
        else:
            target_value = condition.get("value")

            # Get actual value from data (support nested fields with dot notation)
            actual_value = self._get_nested_value(data, field)

            detail = {
                "field": field,
                "operator": operator_str,
                "target": target_value,
                "actual": actual_value,
                "passed": False
            }

            # Handle missing data
            if actual_value is None:
                detail["reason"] = "field_not_found"
                return False, detail
        
        # Evaluate based on operator
        try:
            result = self._apply_operator(operator_str, actual_value, target_value, condition)
            detail["passed"] = result
            return result, detail
        except Exception as e:
            detail["reason"] = f"evaluation_error: {str(e)}"
            return False, detail
    
    def _get_nested_value(self, data: Dict[str, Any], field: str) -> Any:
        """Get value from nested dict using dot notation (e.g., 'indicators.rsi')."""
        if "." not in field:
            return data.get(field)
        
        parts = field.split(".")
        value = data
        for part in parts:
            if isinstance(value, dict):
                value = value.get(part)
            else:
                return None
        return value
    
    def _apply_operator(
        self,
        operator_str: str,
        actual: Any,
        target: Any,
        condition: Dict[str, Any]
    ) -> bool:
        """Apply comparison operator."""
        
        # Boolean operators
        if operator_str == "is_true":
            return bool(actual) is True
        if operator_str == "is_false":
            return bool(actual) is False
        
        # Between operator
        if operator_str == "between":
            min_val = condition.get("min", float("-inf"))
            max_val = condition.get("max", float("inf"))
            try:
                actual_num = float(actual)
                return min_val <= actual_num <= max_val
            except (ValueError, TypeError):
                return False
        
        # Set membership
        if operator_str == "in":
            return actual in (target if isinstance(target, (list, tuple)) else [target])
        if operator_str == "not_in":
            return actual not in (target if isinstance(target, (list, tuple)) else [target])
        
        # Contains (for strings)
        if operator_str == "contains":
            return str(target) in str(actual) if actual and target else False
        
        # String equality (case-insensitive option)
        if isinstance(actual, str) and isinstance(target, str):
            if operator_str in ("==", "="):
                return actual.lower() == target.lower()
            if operator_str == "!=":
                return actual.lower() != target.lower()
        
        # Numeric comparisons
        op_func = OPERATOR_MAP.get(operator_str)
        if op_func:
            try:
                actual_num = float(actual) if not isinstance(actual, bool) else actual
                target_num = float(target) if not isinstance(target, bool) else target
                return op_func(actual_num, target_num)
            except (ValueError, TypeError):
                # Fall back to direct comparison
                return op_func(actual, target)
        
        # Unknown operator - default to equality
        return actual == target
    
    def filter_assets(
        self,
        assets: List[Dict[str, Any]],
        conditions: List[Dict[str, Any]],
        logic: str = "AND"
    ) -> List[Dict[str, Any]]:
        """
        Filter a list of assets based on conditions.
        
        Args:
            assets: List of asset dicts
            conditions: Filter conditions
            logic: "AND" or "OR"
            
        Returns:
            Filtered list of assets that pass all/any conditions
        """
        if not conditions:
            return assets
        
        filtered = []
        for asset in assets:
            result = self.evaluate(conditions, asset, logic)
            if result["passed"]:
                # Attach evaluation metadata
                asset["_filter_matched"] = result["matched"]
                filtered.append(asset)
        
        return filtered


# Singleton instance for convenience
rule_engine = RuleEngine()
