"""Score Engine — thin adapter that routes ``compute_score`` through the
robust scoring engine.

After the Phase 4 cleanup the legacy 4-bucket weighted-total math was
removed. This module keeps the original ``ScoreEngine`` shape (constructor
+ ``compute_score`` + ``get_full_breakdown`` + ``_classify``) so the API,
``ProfileEngine``, and ``spot_scanner`` callers don't have to be rewritten,
but every call to ``compute_score`` is delegated to
``app.services.robust_indicators.compute_asset_score``.

What was removed:
  * ``_evaluate_category_rules`` — the per-bucket positive/penalty math.
  * The 4-bucket weighted-total formula in ``compute_score``.
  * Reading ``self.weights`` (kept on the instance for back-compat but no
    longer drives scoring).

What was kept (observability primitives, no scoring math):
  * ``_evaluate_rule`` / ``_get_matched_rules`` / ``get_full_breakdown`` —
    used by the drilldown UI to show "did this rule's indicator condition
    match?". These never did scoring math themselves.
  * ``_classify`` — threshold-band classification for the robust score.
  * ``merge_score_config`` / ``hydrate_profile_scoring`` /
    ``resolve_profile_scoring_rules`` / ``resolve_rule_category`` — config
    helpers used by the API layer; unchanged.
"""

from copy import deepcopy
import logging
import math
import operator as op
from typing import Dict, Any, List, Optional, Set

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
_IND_CATEGORY: Dict[str, str] = {
    "volume_spike": "liquidity", "volume_24h": "liquidity",
    "spread_pct": "liquidity", "orderbook_depth_usdt": "liquidity",
    "obv": "liquidity",
    "buy_pressure": "liquidity",
    "taker_ratio":  "liquidity",
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
       Scoring tab of the profile editor.
    2. ``filters.conditions[].rule_id`` — legacy coupling where filter
       conditions referenced global rules; kept for backward compatibility.
    3. Free-form field/operator/value match against global rules.
    4. Full global rule set — fallback when no match is found.
    """
    if not global_rules:
        return []

    if not profile_config:
        return list(global_rules)

    scoring_section = profile_config.get("scoring") or {}
    selected_ids_new = scoring_section.get("selected_rule_ids") or []
    if selected_ids_new:
        selected = [
            rule for rule in global_rules
            if str(rule.get("id")) in {str(rid) for rid in selected_ids_new}
        ]
        if selected:
            return selected

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
    """Merge global score config with profile scoring weights."""
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


_LEGACY_BUCKETS = ("liquidity", "market_structure", "momentum", "signal")


class ScoreEngine:
    """Thin adapter — every ``compute_score`` call routes through the robust
    confidence-weighted engine. The legacy 4-bucket math was removed in
    Phase 4.

    The instance still accepts the legacy config shape (``weights`` /
    ``scoring_rules`` / ``rules`` / ``thresholds``) so existing callers
    don't break, but only ``rules`` and ``thresholds`` are read at score
    time. ``weights`` is retained on the instance for backward compatibility
    only (the robust engine has no per-category weighting).
    """

    def __init__(self, score_config: Dict[str, Any]):
        self.config = score_config or {}
        self.weights = self.config.get("weights", {
            "liquidity": 35, "market_structure": 25, "momentum": 25, "signal": 15
        })
        # Accept both "scoring_rules" (global config key) and "rules" (profile scoring key)
        self.rules = (
            self.config.get("scoring_rules")
            or self.config.get("rules")
            or []
        )
        self.thresholds = self.config.get("thresholds", {
            "strong_buy": 80, "buy": 65, "neutral": 40
        })

    def compute_score(self, indicators: Dict[str, Any]) -> Dict[str, Any]:
        """Compute the Alpha Score via the robust engine, shaped like the
        legacy response so callers (API, profile_engine, spot_scanner)
        keep working.

        Returns dict with: ``total_score``, ``classification``,
        ``components`` (legacy bucket keys + ``engine``/confidence
        breadcrumbs), ``category_summaries``, ``matched_rules``.
        """
        if not indicators:
            return self._empty_response("no_data")

        # Local import keeps the module bootable even if the robust package
        # is being patched in tests.
        from .robust_indicators import compute_asset_score

        symbol = str(indicators.get("symbol") or "ADAPTER")
        flow_hint = (
            indicators.get("taker_source")
            if isinstance(indicators, dict) else None
        )
        try:
            payload = compute_asset_score(
                symbol,
                indicators,
                self.rules,
                is_futures=False,
                flow_source_hint=flow_hint,
            )
        except Exception as exc:
            logger.debug("ScoreEngine.compute_score: robust path failed: %s", exc)
            payload = None

        # Robust matched rules carry IDs (and confidence weighting). The
        # legacy ``_get_matched_rules`` is still useful as a fallback for
        # observability when the robust engine rejects (e.g. sparse test
        # fixtures with no envelopes) — it tells the UI which rule
        # *conditions* matched even when no score could be produced.
        if payload is None:
            return self._empty_response(
                "no_data",
                matched_rules=self._get_matched_rules(indicators),
            )

        total_score = float(payload.get("score", 0.0))
        return {
            "total_score": round(total_score, 2),
            "classification": self._classify(total_score),
            "components": {
                # Legacy bucket keys preserved for response-shape compatibility.
                # The robust engine works at the indicator level (no per-bucket
                # math), so these are reported as ``0.0`` and the real signal
                # lives in ``engine`` + the confidence fields below.
                "liquidity_score": 0.0,
                "market_structure_score": 0.0,
                "momentum_score": 0.0,
                "signal_score": 0.0,
                "engine": "robust",
                "score_confidence": float(payload.get("score_confidence", 0.0)),
                "global_confidence": float(payload.get("global_confidence", 0.0)),
            },
            "category_summaries": {},
            "matched_rules": [
                m.get("rule_id") if isinstance(m, dict) else m
                for m in (payload.get("matched_rules") or [])
            ],
        }

    def _empty_response(
        self,
        classification: str,
        *,
        matched_rules: Optional[List[Any]] = None,
    ) -> Dict[str, Any]:
        return {
            "total_score": 0.0,
            "classification": classification,
            "components": {
                "liquidity_score": 0.0,
                "market_structure_score": 0.0,
                "momentum_score": 0.0,
                "signal_score": 0.0,
                "engine": "robust",
                "score_confidence": 0.0,
                "global_confidence": 0.0,
            },
            "category_summaries": {},
            "matched_rules": list(matched_rules or []),
        }

    # ── Observability primitives (rule pass/fail evaluation) ──────────────────
    # These never did scoring math themselves — they're used by the
    # drilldown UI to show whether each rule's indicator condition matched.
    # Kept verbatim from the pre-Phase-4 implementation so the drilldown
    # response shape is unchanged.

    def _evaluate_rule(self, rule: Dict[str, Any], indicators: Dict[str, Any]) -> bool:
        """Evaluate a single scoring rule against indicator values."""
        indicator_name = rule.get("indicator", "")
        operator_str = rule.get("operator", "")
        target_value = rule.get("value")

        def get_indicator_value(name: str) -> Any:
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

        # DI directional comparison
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

        # String equality
        if operator_str == "=" and isinstance(target_value, str):
            return get_indicator_value(indicator_name) == target_value

        # Standard numeric operators
        actual_value = get_indicator_value(indicator_name)
        if actual_value is None:
            return False

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

    def _get_matched_rules(self, indicators: Dict[str, Any]) -> List[str]:
        """Return list of rule IDs whose indicator conditions matched."""
        matched = []
        for rule in self.rules:
            if self._evaluate_rule(rule, indicators):
                matched.append(rule.get("id", "unknown"))
        return matched

    def get_full_breakdown(self, indicators: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Return per-rule detailed breakdown for transparency/drilldown UI.

        ``points_awarded`` here reflects whether the rule's *condition*
        matched the indicator value — it's NOT a score contribution under
        the robust engine (which weights matched points by indicator
        confidence). UI uses this for the rule-level pass/fail panel.
        """
        result = []
        for rule in self.rules:
            indicator = rule.get("indicator", "")
            operator_str = rule.get("operator", "")
            target = rule.get("value")
            pts = float(rule.get("points", 0))

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

        positive_rules = [r for r in result if r["type"] == "positive"]
        penalty_rules  = [r for r in result if r["type"] == "penalty"]
        positive_rules.sort(key=lambda r: -(r["points_possible"] or 0))
        penalty_rules.sort(key=lambda r:  (r["points_possible"] or 0))
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
