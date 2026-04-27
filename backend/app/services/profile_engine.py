"""Profile Engine — orchestrates profile-based filtering, scoring, and signal generation.

Execution order per asset:
    1. Block Rules  (veto — skip processing if blocked)
    2. Filters      (structural L1 gate)
    3. Scoring      (Alpha Score with profile weights)
    4. Signals      (entry conditions)
    5. Entry Triggers (final positive gate)

Indicator data is cached per ``{symbol}:{timeframe}`` key so that conditions
across all sections sharing the same timeframe reuse a single indicator lookup.
"""

import logging
from collections import defaultdict
from typing import Dict, Any, List, Optional

from .block_engine import BlockEngine
from .rule_engine import RuleEngine
from .score_engine import ScoreEngine
from .signal_engine import SignalEngine

logger = logging.getLogger(__name__)


# Default weights when no profile is specified
DEFAULT_WEIGHTS = {
    "liquidity": 25,
    "market_structure": 25,
    "momentum": 25,
    "signal": 25
}

# Valid timeframe choices (used for grouping)
VALID_TIMEFRAMES = frozenset({"1m", "3m", "5m", "15m", "1h"})


# ── Indicator Cache ───────────────────────────────────────────────────────────

class IndicatorCache:
    """In-memory cache keyed by ``{symbol}:{timeframe}``.

    Prevents redundant indicator lookups when multiple conditions across
    Filters / Signals / Block Rules / Entry Triggers reference the same
    timeframe for the same symbol.

    Usage::

        cache = IndicatorCache()
        cache.put("BTC_USDT", "5m", {"rsi": 42.5, "adx": 28.0})
        data = cache.get("BTC_USDT", "5m")
    """

    def __init__(self) -> None:
        self._store: Dict[str, Dict[str, Any]] = {}

    @staticmethod
    def _key(symbol: str, timeframe: str) -> str:
        return f"{symbol}:{timeframe}"

    def get(self, symbol: str, timeframe: str) -> Optional[Dict[str, Any]]:
        return self._store.get(self._key(symbol, timeframe))

    def put(self, symbol: str, timeframe: str, indicators: Dict[str, Any]) -> None:
        self._store[self._key(symbol, timeframe)] = indicators

    def has(self, symbol: str, timeframe: str) -> bool:
        return self._key(symbol, timeframe) in self._store

    def clear(self) -> None:
        self._store.clear()

    @property
    def size(self) -> int:
        return len(self._store)


# ── Timeframe grouping helper ────────────────────────────────────────────────

def _collect_required_timeframes(profile_config: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """Group all conditions across sections by their effective timeframe.

    Returns a mapping ``timeframe → [condition, ...]`` where each condition
    is annotated with its ``_section`` origin (for logging).

    Example return::

        {
            "5m":  [{"field": "rsi", "operator": "<", "value": 30, "_section": "filters"}, ...],
            "15m": [{"field": "rsi", "operator": ">", "value": 50, "_section": "block_rules"}, ...],
            "1m":  [{"indicator": "volume_spike", "operator": ">=", "value": 2, "_section": "entry_triggers"}],
        }
    """
    default_tf = profile_config.get("default_timeframe", "5m")
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    # Filters & Signals use "field" key; Block Rules & Entry Triggers use "indicator"
    section_specs = [
        ("filters",        profile_config.get("filters", {}).get("conditions", [])),
        ("signals",        profile_config.get("signals", {}).get("conditions", [])),
        ("block_rules",    profile_config.get("block_rules", {}).get("blocks", [])),
        ("entry_triggers", profile_config.get("entry_triggers", {}).get("conditions", [])),
    ]

    for section_name, conditions in section_specs:
        for cond in conditions:
            tf = cond.get("timeframe") or default_tf
            if tf not in VALID_TIMEFRAMES:
                tf = default_tf
            grouped[tf].append({**cond, "_section": section_name})

    return dict(grouped)


# ── Structured condition log helper ──────────────────────────────────────────

def _log_condition_eval(
    symbol: str,
    section: str,
    indicator: str,
    timeframe: str,
    period: Optional[int],
    actual_value: Any,
    operator_str: str,
    target_value: Any,
    result: bool,
) -> None:
    """Emit a structured log line for every condition evaluation."""
    logger.debug(
        "condition_eval | symbol=%s section=%s indicator=%s timeframe=%s "
        "period=%s value=%s condition=%s %s result=%s",
        symbol, section, indicator, timeframe,
        period or "-", actual_value, operator_str, target_value, result,
    )


class ProfileEngine:
    """
    Profile Engine — Strategy Definition Layer

    Orchestrates (in order):
    1. Block Rules   (veto — absolute block)
    2. Filter Layer  (L1 structural gate)
    3. Scoring       (Alpha Score with profile weights)
    4. Signal Engine (entry conditions)
    5. Entry Triggers (final positive gate)

    A Profile is NOT just a filter — it's a FULL STRATEGY CONFIGURATION.
    """

    def __init__(self, profile_config: Optional[Dict[str, Any]] = None):
        """
        Initialize Profile Engine with optional profile configuration.

        Args:
            profile_config: Full profile config dict with filters, scoring, signals
        """
        self.profile = profile_config or {}
        self.rule_engine = RuleEngine()
        self.default_timeframe = self.profile.get("default_timeframe", "5m")

        # Extract config sections
        self.filters_config = self.profile.get("filters", {})
        self.scoring_config = self.profile.get("scoring", {})
        # Signal conditions may be stored under 'entry_triggers' OR 'signals'
        self.signals_config = (
            self.profile.get("entry_triggers") or
            self.profile.get("signals") or
            {}
        )
        self.block_rules_config = self.profile.get("block_rules", {})
        self.entry_triggers_config = self.profile.get("entry_triggers", {})

        # Pre-compute timeframe grouping for all conditions
        self._tf_groups = _collect_required_timeframes(self.profile)

        # Per-run indicator cache (cleared between process_watchlist calls)
        self._indicator_cache = IndicatorCache()

        # Initialize engines with profile config
        self._init_engines()

    def _init_engines(self):
        """Initialize Score, Signal, and Block engines with profile config."""
        # Build score config from profile weights — use "scoring_rules" key for ScoreEngine
        weights = self.scoring_config.get("weights", DEFAULT_WEIGHTS)
        score_config = {
            "weights": weights,
            # Profile stores scoring rules under "rules" — ScoreEngine accepts both keys
            "scoring_rules": (
                self.scoring_config.get("scoring_rules")
                or self.scoring_config.get("rules")
                or []
            ),
            "thresholds": self.scoring_config.get("thresholds", {
                "strong_buy": 80,
                "buy": 65,
                "neutral": 40
            })
        }
        self.score_engine = ScoreEngine(score_config)

        # Build signal config from profile
        signal_config = {
            "logic": self.signals_config.get("logic", "AND"),
            "conditions": self._convert_signal_conditions(
                self.signals_config.get("conditions", [])
            )
        }
        self.signal_engine = SignalEngine(signal_config)

        # Block engine
        self.block_engine = BlockEngine({
            **self.block_rules_config,
            "entry_triggers": self.entry_triggers_config.get("conditions", []),
            "entry_logic": self.entry_triggers_config.get("logic", "AND"),
        })

    def _convert_signal_conditions(self, conditions: List[Dict]) -> List[Dict]:
        """Convert profile signal conditions to SignalEngine format."""
        converted = []
        for cond in conditions:
            entry: Dict[str, Any] = {
                "id": cond.get("id", cond.get("field", "unknown")),
                "indicator": cond.get("field", ""),
                "operator": cond.get("operator", "=="),
                "value": cond.get("value"),
                "required": cond.get("required", False),
                "enabled": cond.get("enabled", True),
            }
            # Preserve per-indicator timeframe/period if set
            if cond.get("timeframe"):
                entry["timeframe"] = cond["timeframe"]
            if cond.get("period") is not None:
                entry["period"] = cond["period"]
            converted.append(entry)
        return converted

    # ── Indicator cache population ────────────────────────────────────────────

    def _populate_cache_for_asset(self, asset: Dict[str, Any]) -> None:
        """Populate the indicator cache for all required timeframes of *asset*.

        Currently the asset dict carries a flat set of indicators (from the
        default timeframe, typically 5m).  We cache that under the default
        timeframe.  If the asset also carries a ``_indicators_by_tf`` mapping
        (``{timeframe: {indicator: value}}``), each sub-dict is cached under
        its respective timeframe.

        This design supports both the current flat-indicator pipeline and a
        future multi-timeframe data layer without breaking existing callers.
        """
        symbol = asset.get("symbol", "?")

        # Flat indicators → cache under default timeframe
        flat_indicators = asset.get("indicators", {})
        if not flat_indicators:
            flat_indicators = {k: v for k, v in asset.items()
                              if k not in ("symbol", "name", "_indicators_by_tf")}

        self._indicator_cache.put(symbol, self.default_timeframe, flat_indicators)

        # Multi-timeframe data (future-ready)
        by_tf = asset.get("_indicators_by_tf", {})
        for tf, ind_dict in by_tf.items():
            if tf in VALID_TIMEFRAMES and isinstance(ind_dict, dict):
                self._indicator_cache.put(symbol, tf, ind_dict)

    def _get_indicators_for_condition(
        self, symbol: str, condition: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Return the best available indicator dict for a condition's timeframe.

        Falls back to the default timeframe if the requested one is missing.
        """
        tf = condition.get("timeframe") or self.default_timeframe
        cached = self._indicator_cache.get(symbol, tf)
        if cached is not None:
            return cached
        # Fallback: default timeframe data
        return self._indicator_cache.get(symbol, self.default_timeframe) or {}

    # ── Pipeline ──────────────────────────────────────────────────────────────

    def process_watchlist(
        self,
        assets: List[Dict[str, Any]],
        include_details: bool = False
    ) -> Dict[str, Any]:
        """
        Process a watchlist through the full profile pipeline.

        Execution order (per-asset):
            1. Block Rules  → veto (skip further processing)
            2. Filters      → structural gate
            3. Scoring      → Alpha Score
            4. Signals      → entry conditions
            5. Entry Triggers → final positive gate

        Args:
            assets: List of asset dicts with indicators and metadata
            include_details: Include detailed evaluation info

        Returns:
            {
                "assets": [...],
                "total_before_filter": int,
                "total_after_filter": int,
                "total_blocked": int,
                "signals_count": int,
                "profile_applied": bool,
                "timeframes_used": [list of timeframes]
            }
        """
        # Clear cache for this run
        self._indicator_cache.clear()
        total_before = len(assets)

        # Pre-populate indicator cache for every asset
        for asset in assets:
            self._populate_cache_for_asset(asset)

        if self._tf_groups:
            logger.info(
                "[ProfileEngine] Timeframes required: %s  (%d cache entries)",
                sorted(self._tf_groups.keys()), self._indicator_cache.size,
            )

        # ── Step 1: Block Rules (veto — absolute block) ──────────────────────
        blocked_count = 0
        unblocked_assets: List[Dict[str, Any]] = []

        blocks_configured = bool(self.block_rules_config.get("blocks"))
        for asset in assets:
            if blocks_configured:
                symbol = asset.get("symbol", "?")
                eval_data = self._build_eval_data(asset)
                block_result = self.block_engine.evaluate(eval_data)
                if block_result.get("blocked"):
                    blocked_count += 1
                    logger.debug(
                        "[ProfileEngine] BLOCKED %s: %s",
                        symbol, block_result.get("triggered_blocks"),
                    )
                    continue
            unblocked_assets.append(asset)

        if blocked_count:
            logger.info(
                "[ProfileEngine] Block Rules removed %d/%d assets",
                blocked_count, total_before,
            )

        # ── Step 2: Filters (structural L1 gate) ─────────────────────────────
        filtered_assets = self._apply_filters(unblocked_assets)

        # ── Steps 3-5: Score → Signals → Entry Triggers ──────────────────────
        processed_assets = []
        signals_count = 0

        for asset in filtered_assets:
            processed = self._process_single_asset(asset, include_details)
            processed_assets.append(processed)
            if processed.get("signal", {}).get("triggered"):
                signals_count += 1

        # Rank by score
        processed_assets.sort(
            key=lambda x: x.get("score", {}).get("total_score", 0),
            reverse=True,
        )

        return {
            "assets": processed_assets,
            "total_before_filter": total_before,
            "total_blocked": blocked_count,
            "total_after_filter": len(filtered_assets),
            "signals_count": signals_count,
            "profile_applied": bool(self.profile),
            "timeframes_used": sorted(self._tf_groups.keys()) if self._tf_groups else [self.default_timeframe],
        }

    def _build_eval_data(self, asset: Dict[str, Any]) -> Dict[str, Any]:
        """Merge asset-level fields with indicators for evaluation."""
        indicators = asset.get("indicators", {})
        if not indicators:
            indicators = {k: v for k, v in asset.items() if k not in ["symbol", "name"]}
        return {**asset, **indicators}

    def _apply_filters(
        self,
        assets: List[Dict[str, Any]],
        strict_indicators: bool = False,
    ) -> List[Dict[str, Any]]:
        """Apply L1 filter conditions to assets.

        Strict enforcement for market metadata fields (market_cap, volume_24h,
        price, change_24h): a None/missing value is treated as FAIL, not skip.
        This prevents assets with unknown market cap or volume from slipping
        through a "market_cap >= 5M" filter.

        Lenient evaluation is preserved for technical indicator fields (RSI, ADX,
        etc.) that may not be computed yet — those are skipped when absent.

        When ``strict_indicators=True`` (pipeline stage mode) indicator conditions
        with missing data are also treated as FAIL.  This prevents assets that
        have never had indicators computed from bypassing EMA/RSI/ADX conditions
        and incorrectly appearing in L1/L2/L3 pipeline stages.
        """
        filter_conditions = self.filters_config.get("conditions", [])
        filter_logic = self.filters_config.get("logic", "AND")

        if not filter_conditions:
            return assets

        # Market-data fields must always be evaluated (None → FAIL, not skip).
        # Indicator fields remain lenient (None → skip condition) unless
        # strict_indicators=True, in which case they also FAIL when absent.
        # orderbook_depth_usdt is intentionally excluded: it requires a per-symbol
        # API call that can fail silently (rate limit, thin market, exchange error).
        # When absent, it is treated as UNKNOWN and the filter is skipped rather
        # than rejecting the asset — see pipeline_profile_filters.STRICT_META_FIELDS.
        _STRICT_META = frozenset({
            "volume_24h", "market_cap", "price",
            "change_24h", "change_24h_pct", "price_change_24h",
            "spread_pct",
        })

        result = []
        for asset in assets:
            symbol = asset.get("symbol", "?")
            # Build eval_data from cache (respecting per-condition timeframe)
            base_data = self._build_eval_data(asset)

            applicable = [
                c for c in filter_conditions
                if "group" in c
                or c.get("field") in _STRICT_META          # always evaluate meta fields
                or base_data.get(c.get("field")) is not None   # skip only missing indicators
                or strict_indicators                           # strict: include missing indicators
            ]
            if not applicable:
                result.append(asset)
                continue
            eval_result = self.rule_engine.evaluate(applicable, base_data, filter_logic)

            # Structured logging for filter conditions
            for cond in applicable:
                field = cond.get("field", "")
                tf = cond.get("timeframe") or self.default_timeframe
                _log_condition_eval(
                    symbol=symbol,
                    section="filters",
                    indicator=field,
                    timeframe=tf,
                    period=cond.get("period"),
                    actual_value=base_data.get(field),
                    operator_str=cond.get("operator", ""),
                    target_value=cond.get("value"),
                    result=field in (eval_result.get("matched") or []),
                )

            if eval_result["passed"]:
                result.append(asset)
        return result

    def _process_single_asset(
        self,
        asset: Dict[str, Any],
        include_details: bool = False
    ) -> Dict[str, Any]:
        """Process a single asset through scoring, signal, and entry trigger evaluation."""
        symbol = asset.get("symbol", "?")

        # Merge asset-level fields with indicators for evaluation
        eval_data = self._build_eval_data(asset)

        # ── Scoring ───────────────────────────────────────────────────────────
        score_result = self.score_engine.compute_alpha_score(eval_data)

        # ── Signals ───────────────────────────────────────────────────────────
        signal_result = self.signal_engine.evaluate(
            eval_data,
            score_result.get("total_score", 0),
        )

        # Structured logging for signal conditions
        for cond in self.signal_engine.conditions:
            if not cond.get("enabled", True):
                continue
            ind = cond.get("indicator", "")
            tf = cond.get("timeframe") or self.default_timeframe
            _log_condition_eval(
                symbol=symbol,
                section="signals",
                indicator=ind,
                timeframe=tf,
                period=cond.get("period"),
                actual_value=eval_data.get(ind),
                operator_str=cond.get("operator", ""),
                target_value=cond.get("value"),
                result=cond.get("id", "?") in (signal_result.get("matched") or []),
            )

        # ── Entry Triggers ────────────────────────────────────────────────────
        entry_result = self.block_engine.evaluate_entry(
            eval_data,
            alpha_score=score_result.get("total_score", 0),
        )

        # Structured logging for entry triggers
        for cond in self.entry_triggers_config.get("conditions", []):
            if not cond.get("enabled", True):
                continue
            ind = cond.get("indicator", "")
            tf = cond.get("timeframe") or self.default_timeframe
            _log_condition_eval(
                symbol=symbol,
                section="entry_triggers",
                indicator=ind,
                timeframe=tf,
                period=cond.get("period"),
                actual_value=eval_data.get(ind),
                operator_str=cond.get("operator", ""),
                target_value=cond.get("value"),
                result=cond.get("id", "?") in (entry_result.get("matched") or []),
            )

        # Combine signal + entry trigger: signal only fires if entry is also allowed
        signal_triggered = signal_result.get("signal", False)
        entry_allowed = entry_result.get("allowed", True)

        processed: Dict[str, Any] = {
            "symbol": symbol,
            "name": asset.get("name"),
            "price": asset.get("price"),
            "change_24h": asset.get("change_24h"),
            "volume_24h": asset.get("volume_24h"),
            "score": score_result,
            "signal": {
                "triggered": signal_triggered and entry_allowed,
                "direction": signal_result.get("direction"),
                "matched_conditions": signal_result.get("matched", []),
                "failed_required": signal_result.get("failed_required", []),
                "skipped": signal_result.get("skipped", []),
            },
            "entry": {
                "allowed": entry_allowed,
                "matched": entry_result.get("matched", []),
                "failed_required": entry_result.get("failed_required", []),
                "skipped": entry_result.get("skipped", []),
            },
        }

        if include_details:
            processed["_evaluation"] = {
                "filter_matched": asset.get("_filter_matched", []),
                "score_matched_rules": score_result.get("matched_rules", []),
                "signal_details": signal_result,
                "entry_details": entry_result,
            }

        return processed

    # ── Testing / inspection helpers ──────────────────────────────────────────

    def get_timeframe_groups(self) -> Dict[str, List[Dict[str, Any]]]:
        """Return conditions grouped by timeframe (for debugging/inspection)."""
        return dict(self._tf_groups)

    def test_profile(
        self,
        assets: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Test profile against assets and return detailed analysis.

        Used for profile validation and debugging.

        Returns:
            {
                "summary": {...},
                "filter_analysis": {...},
                "score_distribution": {...},
                "signal_analysis": {...},
                "timeframe_groups": {...},
                "sample_assets": [...]
            }
        """
        result = self.process_watchlist(assets, include_details=True)

        # Build score distribution
        scores = [a.get("score", {}).get("total_score", 0) for a in result["assets"]]
        score_distribution = {
            "min": min(scores) if scores else 0,
            "max": max(scores) if scores else 0,
            "avg": sum(scores) / len(scores) if scores else 0,
            "strong_buy": sum(1 for s in scores if s >= 80),
            "buy": sum(1 for s in scores if 65 <= s < 80),
            "neutral": sum(1 for s in scores if 40 <= s < 65),
            "avoid": sum(1 for s in scores if s < 40)
        }

        # Signal analysis
        signals = [a for a in result["assets"] if a.get("signal", {}).get("triggered")]

        return {
            "summary": {
                "total_assets": result["total_before_filter"],
                "total_blocked": result.get("total_blocked", 0),
                "after_filter": result["total_after_filter"],
                "filter_rate": f"{(result['total_after_filter'] / result['total_before_filter'] * 100):.1f}%" if result["total_before_filter"] > 0 else "0%",
                "signals_triggered": result["signals_count"],
                "timeframes_used": result.get("timeframes_used", []),
            },
            "filter_analysis": {
                "conditions_count": len(self.filters_config.get("conditions", [])),
                "logic": self.filters_config.get("logic", "AND"),
                "passed_count": result["total_after_filter"],
                "filtered_out": result["total_before_filter"] - result["total_after_filter"]
            },
            "score_distribution": score_distribution,
            "signal_analysis": {
                "conditions_count": len(self.signals_config.get("conditions", [])),
                "logic": self.signals_config.get("logic", "AND"),
                "triggered_count": len(signals),
                "long_signals": sum(1 for s in signals if s.get("signal", {}).get("direction") == "long"),
                "short_signals": sum(1 for s in signals if s.get("signal", {}).get("direction") == "short")
            },
            "timeframe_groups": {tf: len(conds) for tf, conds in self._tf_groups.items()},
            "sample_assets": result["assets"][:10]  # Top 10 by score
        }

    def evaluate_asset(
        self,
        asset: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Evaluate a single asset against the profile.

        Returns detailed breakdown of block, filter, score, signal, and entry evaluation.
        """
        # Populate cache for this asset
        self._indicator_cache.clear()
        self._populate_cache_for_asset(asset)

        eval_data = self._build_eval_data(asset)
        symbol = asset.get("symbol", "?")

        # ── 1. Block Rules ────────────────────────────────────────────────────
        if self.block_rules_config.get("blocks"):
            block_result = self.block_engine.evaluate(eval_data)
            if block_result.get("blocked"):
                return {
                    "symbol": symbol,
                    "blocked": True,
                    "block_reasons": block_result.get("triggered_blocks", []),
                    "passed_filter": False,
                    "score": None,
                    "signal": None,
                    "entry": None,
                }

        # ── 2. Filters ────────────────────────────────────────────────────────
        filter_conditions = self.filters_config.get("conditions", [])
        filter_result = self.rule_engine.evaluate(
            filter_conditions,
            eval_data,
            self.filters_config.get("logic", "AND"),
        )

        if not filter_result["passed"]:
            return {
                "symbol": symbol,
                "blocked": False,
                "passed_filter": False,
                "filter_failed": filter_result["failed"],
                "score": None,
                "signal": None,
                "entry": None,
            }

        # ── 3-5. Score → Signals → Entry Triggers ────────────────────────────
        processed = self._process_single_asset(asset, include_details=True)
        processed["blocked"] = False
        processed["passed_filter"] = True
        processed["filter_matched"] = filter_result["matched"]

        return processed


def create_profile_engine(profile_config: Optional[Dict[str, Any]] = None) -> ProfileEngine:
    """Factory function to create ProfileEngine instance."""
    return ProfileEngine(profile_config)
