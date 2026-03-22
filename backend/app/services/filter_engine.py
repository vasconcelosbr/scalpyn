"""Filter Engine — binary pre-filter layer applied before score computation.

All enabled filters must pass (AND logic by default) for an asset to proceed
to scoring. This replaces the old Signal conditions as a lightweight gate.
"""

import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

OPERATORS = {
    "<=": lambda a, b: a <= b,
    ">=": lambda a, b: a >= b,
    "<":  lambda a, b: a < b,
    ">":  lambda a, b: a > b,
    "=":  lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}

DEFAULT_FILTERS_CONFIG: Dict[str, Any] = {
    "enabled": True,
    "logic": "AND",
    "filters": [
        {
            "id": "f_min_volume",
            "name": "Minimum 24h Volume",
            "enabled": True,
            "indicator": "volume_24h",
            "operator": ">=",
            "value": 1_000_000,
        },
        {
            "id": "f_min_adx",
            "name": "Minimum Trend Strength (ADX)",
            "enabled": True,
            "indicator": "adx",
            "operator": ">=",
            "value": 20,
        },
        {
            "id": "f_max_spread",
            "name": "Maximum Spread %",
            "enabled": True,
            "indicator": "spread_pct",
            "operator": "<=",
            "value": 0.5,
        },
    ],
}


class FilterEngine:
    """Binary pre-filter: enabled filters must pass before an asset enters scoring."""

    def __init__(self, filters_config: Dict[str, Any]):
        self.config = filters_config
        self.enabled = filters_config.get("enabled", True)
        self.logic = filters_config.get("logic", "AND")
        self.filters = filters_config.get("filters", [])

    def evaluate(self, indicators: Dict[str, Any]) -> Dict[str, Any]:
        """
        Returns:
            {
                "passed": bool,
                "failed_filters": list[str],   # names of failed filters
                "details": dict[str, str],      # filter_id → reason string
            }
        """
        if not self.enabled:
            return {"passed": True, "failed_filters": [], "details": {}}

        if not indicators:
            return {
                "passed": False,
                "failed_filters": ["no_data"],
                "details": {"no_data": "No indicator data available"},
            }

        enabled_filters = [f for f in self.filters if f.get("enabled", True)]
        failed: list = []
        details: dict = {}

        for filt in enabled_filters:
            filt_id = filt.get("id", "?")
            filt_name = filt.get("name", filt_id)
            indicator = filt.get("indicator", "")
            operator_str = filt.get("operator", ">=")
            threshold = filt.get("value")

            actual = indicators.get(indicator)
            if actual is None:
                # Missing data — skip (don't fail on missing indicators)
                continue

            try:
                actual_f = float(actual)
                threshold_f = float(threshold) if threshold is not None else 0.0
            except (ValueError, TypeError):
                continue

            op_func = OPERATORS.get(operator_str)
            if op_func and not op_func(actual_f, threshold_f):
                failed.append(filt_name)
                details[filt_id] = f"{indicator}={actual_f} fails {operator_str} {threshold_f}"

        if self.logic == "OR":
            # Passes if at least one filter passes
            passed = len(failed) < len(enabled_filters)
        else:
            # AND: all must pass
            passed = len(failed) == 0

        return {"passed": passed, "failed_filters": failed, "details": details}
