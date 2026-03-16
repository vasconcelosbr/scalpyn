"""Score Engine — calculates Alpha Score dynamically from config rules and weights."""

import logging
import operator as op
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

OPERATORS = {
    "<=": op.le,
    ">=": op.ge,
    "<": op.lt,
    ">": op.gt,
    "=": op.eq,
    "!=": op.ne,
}


class ScoreEngine:
    """Calculates Alpha Score using weights and scoring rules from config."""

    def __init__(self, score_config: Dict[str, Any]):
        self.config = score_config
        self.weights = score_config.get("weights", {
            "liquidity": 25, "market_structure": 25, "momentum": 25, "signal": 25
        })
        self.rules = score_config.get("scoring_rules", [])
        self.thresholds = score_config.get("thresholds", {
            "strong_buy": 80, "buy": 65, "neutral": 40
        })

    def compute_alpha_score(self, indicators: Dict[str, Any]) -> Dict[str, Any]:
        """Compute the composite Alpha Score from indicator values.

        Returns dict with: total_score, classification, component scores, matched rules.
        """
        if not indicators:
            return {"total_score": 0, "classification": "no_data", "components": {}}

        # Evaluate all scoring rules to get raw points per category
        liquidity_pts = self._evaluate_category_rules(indicators, "liquidity")
        market_structure_pts = self._evaluate_category_rules(indicators, "market_structure")
        momentum_pts = self._evaluate_category_rules(indicators, "momentum")
        signal_pts = self._evaluate_category_rules(indicators, "signal")

        # Normalize each category to 0-100 scale
        liquidity_score = min(100, liquidity_pts)
        market_structure_score = min(100, market_structure_pts)
        momentum_score = min(100, momentum_pts)
        signal_score = min(100, signal_pts)

        # Apply weights (weights should sum to 100)
        w = self.weights
        total_weight = w.get("liquidity", 25) + w.get("market_structure", 25) + w.get("momentum", 25) + w.get("signal", 25)
        if total_weight == 0:
            total_weight = 100

        total_score = (
            liquidity_score * w.get("liquidity", 25) +
            market_structure_score * w.get("market_structure", 25) +
            momentum_score * w.get("momentum", 25) +
            signal_score * w.get("signal", 25)
        ) / total_weight

        total_score = round(min(100, max(0, total_score)), 2)

        # Classification
        classification = self._classify(total_score)

        return {
            "total_score": total_score,
            "classification": classification,
            "components": {
                "liquidity_score": round(liquidity_score, 2),
                "market_structure_score": round(market_structure_score, 2),
                "momentum_score": round(momentum_score, 2),
                "signal_score": round(signal_score, 2),
            },
            "matched_rules": self._get_matched_rules(indicators),
        }

    def _evaluate_category_rules(self, indicators: Dict[str, Any], category: str) -> float:
        """Evaluate scoring rules and sum points.

        Rules are mapped to categories by indicator type:
        - liquidity: volume_spike, volume_24h, spread_pct, obv
        - market_structure: adx, ema_trend, atr, psar_trend, bb_width
        - momentum: rsi, macd, macd_signal, stoch_k, zscore, vwap_distance_pct
        - signal: taker_ratio, adx_acceleration, volume_delta, funding_rate
        """
        category_indicators = {
            "liquidity": {"volume_spike", "volume_24h", "spread_pct", "obv", "taker_ratio"},
            "market_structure": {"adx", "ema_trend", "atr", "atr_pct", "psar_trend", "bb_width", "di_plus", "di_minus"},
            "momentum": {"rsi", "macd", "macd_signal", "macd_histogram", "stoch_k", "stoch_d", "zscore", "vwap_distance_pct"},
            "signal": {"adx_acceleration", "volume_delta", "funding_rate", "ema9_gt_ema50", "ema50_gt_ema200", "ema_full_alignment"},
        }

        relevant_indicators = category_indicators.get(category, set())
        points = 0.0

        for rule in self.rules:
            indicator_name = rule.get("indicator", "")
            if indicator_name not in relevant_indicators and not self._is_derived_indicator(indicator_name, relevant_indicators):
                continue

            matched = self._evaluate_rule(rule, indicators)
            if matched:
                points += rule.get("points", 0)

        return points

    def _is_derived_indicator(self, indicator: str, category_set: set) -> bool:
        """Check if a derived indicator belongs to a category."""
        derived_map = {
            "ema_trend": {"ema9_gt_ema50", "ema50_gt_ema200", "ema_full_alignment"},
        }
        mapped = derived_map.get(indicator, set())
        return bool(mapped & category_set)

    def _evaluate_rule(self, rule: Dict[str, Any], indicators: Dict[str, Any]) -> bool:
        """Evaluate a single scoring rule against indicator values."""
        indicator_name = rule.get("indicator", "")
        operator_str = rule.get("operator", "")
        target_value = rule.get("value")

        # Special EMA trend operators
        if operator_str == "ema9>ema50>ema200":
            return bool(indicators.get("ema_full_alignment", False))
        elif operator_str == "ema9>ema50":
            return bool(indicators.get("ema9_gt_ema50", False))
        elif operator_str == "ema50>ema200":
            return bool(indicators.get("ema50_gt_ema200", False))
        elif operator_str == "ema9<ema50":
            return not bool(indicators.get("ema9_gt_ema50", True))

        # ADX acceleration operators
        if operator_str == ">prev+" and indicator_name == "adx_acceleration":
            accel = indicators.get("adx_acceleration")
            if accel is None:
                return False
            return accel > (target_value or 0)
        elif operator_str == ">prev" and indicator_name == "adx_acceleration":
            accel = indicators.get("adx_acceleration")
            if accel is None:
                return False
            return accel > 0

        # String equality (e.g., macd_signal = "positive")
        if operator_str == "=" and isinstance(target_value, str):
            return indicators.get(indicator_name) == target_value

        # Standard numeric operators
        actual_value = indicators.get(indicator_name)
        if actual_value is None or target_value is None:
            return False

        try:
            actual_value = float(actual_value)
            target_value = float(target_value)
        except (ValueError, TypeError):
            return False

        op_func = OPERATORS.get(operator_str)
        if op_func:
            return op_func(actual_value, target_value)

        # Between operator
        if operator_str == "between":
            min_val = rule.get("min", 0)
            max_val = rule.get("max", 100)
            return min_val <= actual_value <= max_val

        return False

    def _get_matched_rules(self, indicators: Dict[str, Any]) -> List[str]:
        """Return list of rule IDs that matched."""
        matched = []
        for rule in self.rules:
            if self._evaluate_rule(rule, indicators):
                matched.append(rule.get("id", "unknown"))
        return matched

    def _classify(self, score: float) -> str:
        if score >= self.thresholds.get("strong_buy", 80):
            return "strong_buy"
        elif score >= self.thresholds.get("buy", 65):
            return "buy"
        elif score >= self.thresholds.get("neutral", 40):
            return "neutral"
        else:
            return "avoid"
