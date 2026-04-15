"""Profile Engine — orchestrates profile-based filtering, scoring, and signal generation."""

import logging
from typing import Dict, Any, List, Optional
from uuid import UUID

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


class ProfileEngine:
    """
    Profile Engine — Strategy Definition Layer
    
    Orchestrates:
    1. Filter Layer (L1): Apply profile filters on assets
    2. Scoring Integration: Modify Alpha Score with profile weights
    3. Signal Engine Integration: Evaluate entry conditions
    
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
        
        # Extract config sections
        self.filters_config = self.profile.get("filters", {})
        self.scoring_config = self.profile.get("scoring", {})
        # Signal conditions may be stored under 'entry_triggers' OR 'signals'
        self.signals_config = (
            self.profile.get("entry_triggers") or
            self.profile.get("signals") or
            {}
        )
        
        # Initialize engines with profile config
        self._init_engines()
    
    def _init_engines(self):
        """Initialize Score and Signal engines with profile config."""
        # Build score config from profile weights
        weights = self.scoring_config.get("weights", DEFAULT_WEIGHTS)
        score_config = {
            "weights": weights,
            "scoring_rules": self.scoring_config.get("rules", []),
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
    
    def _convert_signal_conditions(self, conditions: List[Dict]) -> List[Dict]:
        """Convert profile signal conditions to SignalEngine format."""
        converted = []
        for cond in conditions:
            converted.append({
                "id": cond.get("id", cond.get("field", "unknown")),
                "indicator": cond.get("field", ""),
                "operator": cond.get("operator", "=="),
                "value": cond.get("value"),
                "required": cond.get("required", False),
                "enabled": cond.get("enabled", True)
            })
        return converted
    
    def process_watchlist(
        self,
        assets: List[Dict[str, Any]],
        include_details: bool = False
    ) -> Dict[str, Any]:
        """
        Process a watchlist through the full profile pipeline.
        
        Pipeline:
        1. Load watchlist assets
        2. Apply L1 filters (if profile exists)
        3. Compute scores using profile weights
        4. Apply signal conditions
        5. Return ranked assets with signals
        
        Args:
            assets: List of asset dicts with indicators and metadata
            include_details: Include detailed evaluation info
            
        Returns:
            {
                "assets": [...],
                "total_before_filter": int,
                "total_after_filter": int,
                "signals_count": int,
                "profile_applied": bool
            }
        """
        total_before = len(assets)
        
        # Step 1: Apply L1 Filters
        filtered_assets = self._apply_filters(assets)
        
        # Step 2 & 3: Compute scores and signals for each asset
        processed_assets = []
        signals_count = 0
        
        for asset in filtered_assets:
            processed = self._process_single_asset(asset, include_details)
            processed_assets.append(processed)
            if processed.get("signal", {}).get("triggered"):
                signals_count += 1
        
        # Step 4: Rank by score
        processed_assets.sort(
            key=lambda x: x.get("score", {}).get("total_score", 0),
            reverse=True
        )
        
        return {
            "assets": processed_assets,
            "total_before_filter": total_before,
            "total_after_filter": len(filtered_assets),
            "signals_count": signals_count,
            "profile_applied": bool(self.profile)
        }
    
    def _apply_filters(self, assets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Apply L1 filter conditions to assets.

        Strict enforcement for market metadata fields (market_cap, volume_24h,
        price, change_24h): a None/missing value is treated as FAIL, not skip.
        This prevents assets with unknown market cap or volume from slipping
        through a "market_cap >= 5M" filter.

        Lenient evaluation is preserved for technical indicator fields (RSI, ADX,
        etc.) that may not be computed yet — those are skipped when absent.
        """
        filter_conditions = self.filters_config.get("conditions", [])
        filter_logic = self.filters_config.get("logic", "AND")

        if not filter_conditions:
            return assets

        # Market-data fields must always be evaluated (None → FAIL, not skip).
        # Indicator fields remain lenient (None → skip condition).
        _STRICT_META = frozenset({
            "volume_24h", "market_cap", "price",
            "change_24h", "change_24h_pct", "price_change_24h",
            "spread_pct", "orderbook_depth_usdt",
        })

        result = []
        for asset in assets:
            applicable = [
                c for c in filter_conditions
                if "group" in c
                or c.get("field") in _STRICT_META          # always evaluate meta fields
                or asset.get(c.get("field")) is not None   # skip only missing indicators
            ]
            if not applicable:
                result.append(asset)
                continue
            eval_result = self.rule_engine.evaluate(applicable, asset, filter_logic)
            if eval_result["passed"]:
                result.append(asset)
        return result
    
    def _process_single_asset(
        self,
        asset: Dict[str, Any],
        include_details: bool = False
    ) -> Dict[str, Any]:
        """Process a single asset through scoring and signal evaluation."""
        
        # Extract indicators from asset
        indicators = asset.get("indicators", {})
        if not indicators:
            # Try to use asset fields directly as indicators
            indicators = {k: v for k, v in asset.items() if k not in ["symbol", "name"]}
        
        # Merge asset-level fields with indicators for evaluation
        eval_data = {**asset, **indicators}
        
        # Compute Alpha Score with profile weights
        score_result = self.score_engine.compute_alpha_score(eval_data)
        
        # Evaluate signal conditions
        signal_result = self.signal_engine.evaluate(
            eval_data,
            score_result.get("total_score", 0)
        )
        
        processed = {
            "symbol": asset.get("symbol"),
            "name": asset.get("name"),
            "price": asset.get("price"),
            "change_24h": asset.get("change_24h"),
            "volume_24h": asset.get("volume_24h"),
            "score": score_result,
            "signal": {
                "triggered": signal_result.get("signal", False),
                "direction": signal_result.get("direction"),
                "matched_conditions": signal_result.get("matched", []),
                "failed_required": signal_result.get("failed_required", [])
            }
        }
        
        if include_details:
            processed["_evaluation"] = {
                "filter_matched": asset.get("_filter_matched", []),
                "score_matched_rules": score_result.get("matched_rules", []),
                "signal_details": signal_result
            }
        
        return processed
    
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
                "after_filter": result["total_after_filter"],
                "filter_rate": f"{(result['total_after_filter'] / result['total_before_filter'] * 100):.1f}%" if result["total_before_filter"] > 0 else "0%",
                "signals_triggered": result["signals_count"]
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
            "sample_assets": result["assets"][:10]  # Top 10 by score
        }
    
    def evaluate_asset(
        self,
        asset: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Evaluate a single asset against the profile.
        
        Returns detailed breakdown of filter, score, and signal evaluation.
        """
        # Check filter
        filter_conditions = self.filters_config.get("conditions", [])
        filter_result = self.rule_engine.evaluate(
            filter_conditions,
            asset,
            self.filters_config.get("logic", "AND")
        )
        
        if not filter_result["passed"]:
            return {
                "symbol": asset.get("symbol"),
                "passed_filter": False,
                "filter_failed": filter_result["failed"],
                "score": None,
                "signal": None
            }
        
        # Process through full pipeline
        processed = self._process_single_asset(asset, include_details=True)
        processed["passed_filter"] = True
        processed["filter_matched"] = filter_result["matched"]
        
        return processed


def create_profile_engine(profile_config: Optional[Dict[str, Any]] = None) -> ProfileEngine:
    """Factory function to create ProfileEngine instance."""
    return ProfileEngine(profile_config)
