"""Score Engine — calculates Alpha Score dynamically from config rules and weights."""

from copy import deepcopy
import logging
import math
import operator as op
from typing import Dict, Any, List, Optional, Set, Union

# ── Display labels per indicator name ─────────────────────────────────────────
_IND_LABELS: Dict[str, str] = {
    "rsi": "RSI", "volume_spike": "Vol Spike",
    "taker_ratio": "Taker Ratio (buy/(buy+sell))",
    "buy_pressure": "Buy Pressure (buy/(buy+sell))",
    "taker_buy_volume": "Taker Buy Vol", "taker_sell_volume": "Taker Sell Vol",
    "adx": "ADX", "macd_histogram": "MACD Hist", "macd": "MACD",
    "macd_signal": "MACD Signal", "ema9_gt_ema50": "EMA 9>50",
    "ema50_gt_ema200": "EMA 50>200", "ema_full_alignment": "EMA Trend",
    "ema_trend": "EMA Trend", "adx_acceleration": "ADX Accel",
    "di_trend": "DI Trend", "di_plus": "DI+", "di_minus": "DI-",
    "spread_pct": "Spread%", "orderbook_depth_usdt": "Book Depth",
    "obv": "OBV", "vwap_distance_pct": "VWAP%", "stoch_k": "Stoch%K",
    "stoch_d": "Stoch%D", "bb_width": "BB Width", "volume_24h": "Vol 24h",
    "zscore": "Z-Score", "psar_trend": "PSAR", "atr_pct": "ATR%", "atr": "ATR",
    "ema9_distance_pct": "EMA9 Dist%", "volume_delta": "Vol Delta",
}

# ── Category per indicator name ────────────────────────────────────────────────
# taker_ratio  = buy / (buy + sell)  → [0, 1]  → lives in "liquidity" (threshold > 0.5)
# buy_pressure = buy / (buy + sell)  → [0, 1]  → lives in "liquidity" (threshold > 0.5)
# Both fields carry the same "Buy Volume Ratio" value since #82 (was buy/sell before).
_IND_CATEGORY: Dict[str, str] = {
    "volume_spike": "liquidity", "volume_24h": "liquidity",
    "spread_pct": "liquidity", "orderbook_depth_usdt": "liquidity",
    "obv": "liquidity",
    "buy_pressure": "liquidity",          # buy/(buy+sell), [0, 1]
    "taker_ratio":  "liquidity",          # buy/(buy+sell), [0, 1] — moved from "signal" in #82
    "taker_buy_volume": "liquidity",
    "taker_sell_volume": "liquidity",
    "adx": "market_structure", "ema_trend": "market_structure",
    "atr": "market_structure", "atr_pct": "market_structure",
    "psar_trend": "market_structure", "bb_width": "market_structure",
    "di_plus": "market_structure", "di_minus": "market_structure",
    "di_trend": "market_structure",
    "rsi": "momentum", "macd": "momentum", "macd_signal": "momentum",
    "macd_histogram": "momentum", "stoch_k": "momentum",
    "stoch_d": "momentum", "zscore": "momentum", "vwap_distance_pct": "momentum",
    "ema9_distance_pct": "momentum",
    # taker_ratio used to live in "signal" with the legacy buy/sell formula;
    # since #82 it is buy/(buy+sell) ∈ [0, 1] and lives in "liquidity"
    # (see entry above). Do not re-add it here.
    "adx_acceleration": "signal", "volume_delta": "signal",
    "funding_rate": "signal", "ema9_gt_ema50": "signal",
    "ema50_gt_ema200": "signal", "ema_full_alignment": "signal",
}

logger = logging.getLogger(__name__)
_VALID_CATEGORIES: Set[str] = {"liquidity", "market_structure", "momentum", "signal", "other"}


def _normalize_category(category: Any) -> Optional[str]:
    if not isinstance(category, str):
        return None
    normalized = category.strip().lower().replace(" ", "_")
    return normalized if normalized in _VALID_CATEGORIES else None


def resolve_rule_category(rule: Dict[str, Any]) -> str:
    normalized = _normalize_category(rule.get("category"))
    if normalized:
        return normalized
    return _IND_CATEGORY.get(rule.get("indicator", ""), "other")


def _values_match(left: Any, right: Any) -> bool:
    if left == right:
        return True
    try:
        return math.isclose(float(left), float(right), rel_tol=1e-9, abs_tol=1e-9)
    except (TypeError, ValueError):
        return False


def _condition_matches_rule(condition: Dict[str, Any], rule: Dict[str, Any]) -> bool:
    field = condition.get("field") or condition.get("indicator")
    if field != rule.get("indicator"):
        return False

    condition_operator = condition.get("operator")
    rule_operator = rule.get("operator")
    if condition_operator and rule_operator and condition_operator != rule_operator:
        return False

    if rule_operator == "between":
        return (
            _values_match(condition.get("min"), rule.get("min"))
            and _values_match(condition.get("max"), rule.get("max"))
        )

    return _values_match(condition.get("value"), rule.get("value"))


def resolve_profile_scoring_rules(
    global_rules: List[Dict[str, Any]],
    profile_config: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Resolve which global score rules should apply to a profile.

    Priority order (first non-empty wins):
    1. ``scoring.selected_rule_ids`` — explicit list of rule IDs chosen in the
       Scoring tab of the profile editor (new contract, decoupled from Filters).
    2. ``filters.conditions[].rule_id`` — legacy coupling where filter conditions
       referenced global rules; kept for backward compatibility.
    3. Free-form field/operator/value match against global rules — for even
       older profiles that pre-date rule IDs in conditions.
    4. Full global rule set — fallback when no match is found.
    """
    if not global_rules:
        return []

    if not profile_config:
        return list(global_rules)

    # ── 1. New: scoring.selected_rule_ids ────────────────────────────────────
    scoring_section = profile_config.get("scoring") or {}
    selected_ids_new = scoring_section.get("selected_rule_ids") or []
    if selected_ids_new:
        selected = [
            rule for rule in global_rules
            if str(rule.get("id")) in {str(rid) for rid in selected_ids_new}
        ]
        if selected:
            return selected

    # ── 2. Legacy: filters.conditions[].rule_id ───────────────────────────────
    conditions = ((profile_config.get("filters") or {}).get("conditions") or [])

    selected_rule_ids = {
        str(cond.get("rule_id"))
        for cond in conditions
        if cond.get("rule_id")
    }
    if selected_rule_ids:
        selected = [
            rule for rule in global_rules
            if str(rule.get("id")) in selected_rule_ids
        ]
        if selected:
            return selected

    if not conditions:
        return list(global_rules)

    # ── 3. Legacy: field/operator/value matching ──────────────────────────────
    matched_rules: List[Dict[str, Any]] = []
    seen_ids: Set[str] = set()
    for cond in conditions:
        for rule in global_rules:
            rule_id = str(rule.get("id"))
            if rule_id in seen_ids:
                continue
            if _condition_matches_rule(cond, rule):
                matched_rules.append(rule)
                seen_ids.add(rule_id)
                break

    return matched_rules or list(global_rules)


def merge_score_config(
    global_config: Dict[str, Any],
    profile_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Merge global score config (/settings/score) with profile scoring weights.

    Rules always come from the global config.  Weights come from the profile's
    ``config.scoring.weights`` when the profile has scoring enabled; otherwise
    the global weights are used.

    This ensures every watchlist score respects the user-configured rules while
    honouring per-profile weight customisations (Alpha Score Weights).
    """
    merged = deepcopy(global_config)
    global_rules = (
        merged.get("scoring_rules")
        or merged.get("rules")
        or []
    )
    merged["scoring_rules"] = resolve_profile_scoring_rules(global_rules, profile_config)

    if not profile_config:
        return merged

    scoring_section = profile_config.get("scoring") or {}

    # Only apply profile weights when scoring is explicitly enabled
    if scoring_section.get("enabled") is False:
        return merged

    profile_weights = scoring_section.get("weights")
    if profile_weights and isinstance(profile_weights, dict):
        merged["weights"] = profile_weights

    return merged


def hydrate_profile_scoring(
    profile_config: Optional[Dict[str, Any]],
    global_score_config: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    if not profile_config:
        return profile_config

    hydrated = deepcopy(profile_config)
    scoring_section = dict(hydrated.get("scoring") or {})
    merged = merge_score_config(global_score_config or {}, hydrated)

    scoring_section["weights"] = merged.get("weights", scoring_section.get("weights", {}))
    scoring_section["rules"] = merged.get("scoring_rules") or merged.get("rules") or []
    scoring_section["thresholds"] = merged.get(
        "thresholds",
        scoring_section.get("thresholds", {}),
    )

    if "enabled" in (profile_config.get("scoring") or {}):
        scoring_section["enabled"] = (profile_config.get("scoring") or {}).get("enabled")

    hydrated["scoring"] = scoring_section
    return hydrated


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

    def __init__(self, score_config: Dict[str, Any], min_confidence: float = 0.5):
        self.config = score_config
        self.weights = score_config.get("weights", {
            "liquidity": 35, "market_structure": 25, "momentum": 25, "signal": 15
        })
        # Accept both "scoring_rules" (global config key) and "rules" (profile scoring key)
        self.rules = (
            score_config.get("scoring_rules")
            or score_config.get("rules")
            or []
        )
        self.thresholds = score_config.get("thresholds", {
            "strong_buy": 80, "buy": 65, "neutral": 40
        })
        self.min_confidence = min_confidence  # Minimum confidence to use an indicator

    def _extract_value_and_confidence(
        self, indicators: Dict[str, Any], indicator_name: str
    ) -> tuple[Optional[Any], float, bool]:
        """Extract value, confidence, and validity from indicator data.

        Supports both raw indicator dicts and IndicatorEnvelope objects.

        Returns:
            (value, confidence, valid) tuple
        """
        indicator_data = indicators.get(indicator_name)

        if indicator_data is None:
            return (None, 0.0, False)

        # Check if it's an IndicatorEnvelope (dict with 'value', 'confidence', 'valid' keys)
        if isinstance(indicator_data, dict) and 'confidence' in indicator_data:
            return (
                indicator_data.get('value'),
                float(indicator_data.get('confidence', 0.0)),
                bool(indicator_data.get('valid', False))
            )

        # Raw indicator value (legacy path)
        return (indicator_data, 1.0, True)

    def _is_confidence_weighted_mode(self, indicators: Dict[str, Any]) -> bool:
        """Detect if indicators dict contains IndicatorEnvelope objects."""
        if not indicators:
            return False
        # Check first indicator for envelope structure
        first_value = next(iter(indicators.values()), None)
        return isinstance(first_value, dict) and 'confidence' in first_value

    def compute_alpha_score(
        self,
        indicators: Dict[str, Any],
        use_confidence_weighting: bool = False
    ) -> Dict[str, Any]:
        """Compute the composite Alpha Score from indicator values.

        Supports both raw indicators (legacy) and IndicatorEnvelope dicts.
        Auto-detects mode or can be explicitly set via use_confidence_weighting.

        Args:
            indicators: Dict of indicator_name -> value or IndicatorEnvelope dict
            use_confidence_weighting: Force confidence weighting mode (overrides auto-detect)

        Returns dict with: total_score, classification, component scores,
        category_summaries (debug/drilldown), matched rules, confidence_metrics.
        """
        if not indicators:
            return {"total_score": 0, "classification": "no_data", "components": {}}

        # Auto-detect or use explicit flag
        confidence_mode = use_confidence_weighting or self._is_confidence_weighted_mode(indicators)

        category_summaries = {
            category: self._evaluate_category_rules(indicators, category, confidence_mode)
            for category in ("liquidity", "market_structure", "momentum", "signal")
        }

        # Apply weights only to categories that have active positive scoring rules.
        # Categories with only penalty rules have positive_possible=0 and are excluded.
        w = self.weights
        weighted_categories = [
            category for category, summary in category_summaries.items()
            if summary["positive_possible"] > 0
        ]
        total_weight = sum(w.get(category, 25) for category in weighted_categories)

        # Compute weighted total from unrounded final_score values to avoid precision loss;
        # round only the final output and the per-component display fields.
        if total_weight > 0:
            total_score = sum(
                category_summaries[category]["final_score"] * 100 * w.get(category, 25)
                for category in weighted_categories
            ) / total_weight
        else:
            total_score = 0.0

        # Per-component scores (display only — not used in weighted aggregation above).
        component_scores = {
            category: round(summary["final_score"] * 100, 2)
            for category, summary in category_summaries.items()
        }

        total_score = round(min(100, max(0, total_score)), 2)

        # Classification
        classification = self._classify(total_score) if total_weight > 0 else "no_data"

        # Confidence metrics (only in confidence mode)
        confidence_metrics = None
        if confidence_mode:
            confidence_metrics = self._compute_confidence_metrics(category_summaries)

        result = {
            "total_score": total_score,
            "classification": classification,
            "components": {
                "liquidity_score": component_scores["liquidity"],
                "market_structure_score": component_scores["market_structure"],
                "momentum_score": component_scores["momentum"],
                "signal_score": component_scores["signal"],
            },
            "category_summaries": category_summaries,
            "matched_rules": self._get_matched_rules(indicators, confidence_mode),
            "confidence_weighted": confidence_mode,
        }

        if confidence_metrics:
            result["confidence_metrics"] = confidence_metrics

        return result

    def _compute_confidence_metrics(self, category_summaries: Dict[str, Any]) -> Dict[str, Any]:
        """Compute overall confidence metrics from category summaries."""
        total_weight = sum(self.weights.get(cat, 25) for cat in category_summaries.keys())
        if total_weight == 0:
            return {"overall_confidence": 0.0, "category_confidences": {}}

        # Weighted average of category confidences
        weighted_conf = sum(
            category_summaries[cat].get("avg_confidence", 1.0) * self.weights.get(cat, 25)
            for cat in category_summaries.keys()
        ) / total_weight

        return {
            "overall_confidence": round(weighted_conf, 4),
            "category_confidences": {
                cat: round(summary.get("avg_confidence", 1.0), 4)
                for cat, summary in category_summaries.items()
            },
            "low_confidence_rules": sum(
                summary.get("low_confidence_count", 0)
                for summary in category_summaries.values()
            ),
        }

    def _evaluate_category_rules(
        self, indicators: Dict[str, Any], category: str, confidence_mode: bool = False
    ) -> Dict[str, Any]:
        """Evaluate scoring rules and summarise earned vs possible points.

        Uses three separate accumulators to correctly handle penalty rules
        (negative points):

        - positive_possible : sum of points for positive rules only
                              (denominator / maximum achievable).
        - earned_positive   : sum of positive-rule points that matched.
        - penalties         : sum of negative-rule points that fired (≤ 0).

        Formula (after guards):
            raw = (earned_positive + penalties) / positive_possible
            category_score = clamp(raw, 0.0, 1.0)

        A category with no positive rules always scores 0.0 — penalty-only
        categories have no defined maximum so they cannot contribute a
        meaningful percentage score.

        Each rule is assigned to exactly ONE category based on _IND_CATEGORY
        (the canonical mapping).  This prevents double-counting — e.g. an
        ``ema_trend`` rule counts only in ``market_structure``, never also in
        ``signal``.

        Rules are mapped to categories by indicator type:
        - liquidity:        volume_spike, volume_24h, spread_pct, orderbook_depth_usdt, obv,
                            buy_pressure (buy/(buy+sell), 0-1), taker_ratio (buy/(buy+sell), 0-1),
                            taker_buy_volume, taker_sell_volume
        - market_structure: adx, ema_trend, atr, atr_pct, psar_trend, bb_width, di_plus, di_minus, di_trend
        - momentum:         rsi, macd, macd_signal, macd_histogram, stoch_k, stoch_d, zscore, vwap_distance_pct
        - signal:           adx_acceleration, volume_delta, funding_rate,
                            ema9_gt_ema50, ema50_gt_ema200, ema_full_alignment

        buy_pressure  = buy / (buy + sell)  → [0, 1],  equilibrium = 0.5
        taker_ratio   = buy / (buy + sell)  → [0, 1],  equilibrium = 0.5  (#82: was buy/sell)
        """
        EPS = 1e-9
        positive_possible = 0.0
        earned_positive   = 0.0
        penalties         = 0.0
        rules_passed      = 0
        rules_failed      = 0

        # Confidence tracking (only in confidence mode)
        confidence_sum = 0.0
        confidence_count = 0
        low_confidence_count = 0

        for rule in self.rules:
            # Use _IND_CATEGORY as the single source of truth for which
            # category an indicator belongs to.  Default to "other" so that
            # unknown indicators are never silently dropped into a wrong bucket.
            rule_category = resolve_rule_category(rule)
            if rule_category != category:
                continue

            points = float(rule.get("points") if rule.get("points") is not None else 0)

            # In confidence mode, check indicator confidence and validity
            confidence = 1.0
            if confidence_mode:
                indicator_name = rule.get("indicator", "")
                _, confidence, valid = self._extract_value_and_confidence(indicators, indicator_name)

                # Skip rules with low confidence or invalid indicators
                if not valid or confidence < self.min_confidence:
                    logger.debug(
                        f"[score] Skipping rule for {indicator_name} in {category}: "
                        f"confidence={confidence:.2f}, valid={valid}"
                    )
                    low_confidence_count += 1
                    continue

                confidence_sum += confidence
                confidence_count += 1

            matched = self._evaluate_rule(rule, indicators, confidence_mode)

            if points > 0:
                # Apply confidence multiplier to points in confidence mode
                effective_points = points * confidence if confidence_mode else points
                positive_possible += effective_points
                if matched:
                    earned_positive += effective_points
                    rules_passed += 1
                else:
                    rules_failed += 1
            elif points < 0:
                # Penalties also get confidence multiplier
                effective_points = points * confidence if confidence_mode else points
                if matched:
                    penalties += effective_points  # already negative → subtracts from numerator
                    rules_passed += 1
                else:
                    rules_failed += 1
            # pts == 0: no effect on any accumulator

        # ── Invariant checks on raw accumulators (before clamping) ──────────
        if earned_positive > positive_possible + 1e-6:
            logger.warning("[score] earned_positive overflow",
                           extra={"category": category,
                                  "earned": earned_positive,
                                  "possible": positive_possible})
        if penalties > 1e-6:
            logger.warning("[score] penalties anomaly — positive value",
                           extra={"category": category, "penalties": penalties})

        # ── Guards (in this exact order) ─────────────────────────────────
        positive_possible = max(0.0, positive_possible)             # float drift
        earned_positive   = min(earned_positive, positive_possible)  # earned ≤ max
        penalties         = min(penalties, 0.0)                     # never increases score

        # ── Score calculation ─────────────────────────────────────────────
        if positive_possible <= EPS:
            # No positive rules means no defined maximum; score 0.
            category_score = 0.0
        else:
            raw = (earned_positive + penalties) / positive_possible
            category_score = max(0.0, min(1.0, raw))  # double clamp

        logger.debug(
            "[score] category=%s positive_possible=%.4f earned=%.4f "
            "penalties=%.4f score=%.4f",
            category, positive_possible, earned_positive, penalties, category_score,
        )

        result = {
            "positive_possible": float(positive_possible),
            "earned_positive":   float(earned_positive),
            "penalties":         float(penalties),
            "final_score":       float(category_score),
            "rules_passed":      int(rules_passed),
            "rules_failed":      int(rules_failed),
        }

        # Add confidence metrics if in confidence mode
        if confidence_mode:
            avg_confidence = confidence_sum / confidence_count if confidence_count > 0 else 1.0
            result["avg_confidence"] = float(avg_confidence)
            result["low_confidence_count"] = int(low_confidence_count)

        return result

    def _evaluate_rule(
        self, rule: Dict[str, Any], indicators: Dict[str, Any], confidence_mode: bool = False
    ) -> bool:
        """Evaluate a single scoring rule against indicator values.

        Args:
            rule: Rule dict with indicator, operator, value
            indicators: Dict of indicators (raw values or envelopes)
            confidence_mode: If True, extract values from envelopes

        Returns:
            True if rule matches, False otherwise
        """
        indicator_name = rule.get("indicator", "")
        operator_str = rule.get("operator", "")
        target_value = rule.get("value")

        # Extract actual value (handles both raw and envelope modes)
        def get_indicator_value(name: str) -> Any:
            if confidence_mode:
                value, _, valid = self._extract_value_and_confidence(indicators, name)
                return value if valid else None
            return indicators.get(name)

        # Special EMA trend operators
        if operator_str == "ema9>ema50>ema200":
            return bool(get_indicator_value("ema_full_alignment") or False)
        elif operator_str == "ema9>ema50":
            return bool(get_indicator_value("ema9_gt_ema50") or False)
        elif operator_str == "ema50>ema200":
            return bool(get_indicator_value("ema50_gt_ema200") or False)
        elif operator_str == "ema9<ema50":
            return not bool(get_indicator_value("ema9_gt_ema50") or True)

        # DI directional comparison: DI+ > DI- (real trend confirmation, not just DI+ > 0)
        if operator_str == "di+>di-":
            di_plus = get_indicator_value("di_plus")
            di_minus = get_indicator_value("di_minus")
            if di_plus is None or di_minus is None:
                return False
            try:
                return float(di_plus) > float(di_minus)
            except (TypeError, ValueError):
                return False
        if operator_str == "di->di+":
            di_plus = get_indicator_value("di_plus")
            di_minus = get_indicator_value("di_minus")
            if di_plus is None or di_minus is None:
                return False
            try:
                return float(di_minus) > float(di_plus)
            except (TypeError, ValueError):
                return False

        # ADX acceleration operators
        if operator_str == ">prev+" and indicator_name == "adx_acceleration":
            accel = get_indicator_value("adx_acceleration")
            if accel is None:
                return False
            return accel > (target_value or 0)
        elif operator_str == ">prev" and indicator_name == "adx_acceleration":
            accel = get_indicator_value("adx_acceleration")
            if accel is None:
                return False
            return accel > 0

        # String equality (e.g., macd_signal = "positive")
        if operator_str == "=" and isinstance(target_value, str):
            return get_indicator_value(indicator_name) == target_value

        # Standard numeric operators
        actual_value = get_indicator_value(indicator_name)
        if actual_value is None:
            return False

        # Between operator — uses min/max fields instead of value
        if operator_str == "between":
            min_val = rule.get("min", 0)
            max_val = rule.get("max", 100)
            try:
                return float(min_val) <= float(actual_value) <= float(max_val)
            except (TypeError, ValueError):
                return False

        if target_value is None:
            return False

        try:
            actual_value = float(actual_value)
            target_value = float(target_value)
        except (ValueError, TypeError):
            return False

        op_func = OPERATORS.get(operator_str)
        if op_func:
            return op_func(actual_value, target_value)

        return False

    def _get_matched_rules(
        self, indicators: Dict[str, Any], confidence_mode: bool = False
    ) -> List[str]:
        """Return list of rule IDs that matched."""
        matched = []
        for rule in self.rules:
            if self._evaluate_rule(rule, indicators, confidence_mode):
                matched.append(rule.get("id", "unknown"))
        return matched

    def get_full_breakdown(self, indicators: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Return per-rule detailed breakdown for transparency/drilldown UI.

        Each element:
          id, indicator, label, operator, target_value, min, max,
          actual_value, passed, points_awarded, points_possible,
          condition_text, category
        """
        result = []
        for rule in self.rules:
            indicator = rule.get("indicator", "")
            operator_str = rule.get("operator", "")
            target = rule.get("value")
            pts = float(rule.get("points", 0))

            # ── Resolve actual_value and human-readable condition text ──
            lbl = _IND_LABELS.get(indicator, indicator.upper())

            if operator_str == "ema9>ema50>ema200":
                actual: Any = bool(indicators.get("ema_full_alignment", False))
                cond = "EMA 9>50>200"
            elif operator_str == "ema9>ema50":
                actual = bool(indicators.get("ema9_gt_ema50", False))
                cond = "EMA 9>50"
            elif operator_str == "ema9<ema50":
                actual = not bool(indicators.get("ema9_gt_ema50", True))
                cond = "EMA 9<50"
            elif operator_str == "ema50>ema200":
                actual = bool(indicators.get("ema50_gt_ema200", False))
                cond = "EMA 50>200"
            elif operator_str == "di+>di-":
                actual = indicators.get("di_plus")
                cond = "DI+ > DI-"
            elif operator_str == "di->di+":
                actual = indicators.get("di_minus")
                cond = "DI- > DI+"
            elif operator_str == "between":
                actual = indicators.get(indicator)
                mn, mx = rule.get("min", 0), rule.get("max", 100)
                cond = f"{lbl} {mn}–{mx}"
            else:
                actual = indicators.get(indicator)
                cond = f"{lbl} {operator_str} {target}" if target is not None else f"{lbl} {operator_str}"

            passed = self._evaluate_rule(rule, indicators)
            result.append({
                "id": rule.get("id", f"{indicator}_{operator_str}"),
                "indicator": indicator,
                "label": lbl,
                "operator": operator_str,
                "target_value": target,
                "min": rule.get("min"),
                "max": rule.get("max"),
                "actual_value": actual if not isinstance(actual, dict) else None,
                "passed": passed,
                "points_awarded": float(pts) if passed else 0.0,
                "points_possible": float(pts),
                "type": "positive" if pts > 0 else ("penalty" if pts < 0 else "neutral"),
                "condition_text": cond,
                "category": resolve_rule_category(rule),
            })

        # Sort: positive rules first (descending by points), then penalty rules
        # (ascending by magnitude — most negative first).  Preserves deterministic
        # ordering across environments and simplifies per-category drilldown debug.
        positive_rules = [r for r in result if r["type"] == "positive"]
        penalty_rules  = [r for r in result if r["type"] == "penalty"]
        positive_rules.sort(key=lambda r: -(r["points_possible"] or 0))
        penalty_rules.sort(key=lambda r:  (r["points_possible"] or 0))  # most-negative first
        return positive_rules + penalty_rules

    def _classify(self, score: float) -> str:
        if score >= self.thresholds.get("strong_buy", 80):
            return "strong_buy"
        elif score >= self.thresholds.get("buy", 65):
            return "buy"
        elif score >= self.thresholds.get("neutral", 40):
            return "neutral"
        else:
            return "avoid"
