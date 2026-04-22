import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.api.watchlists import (
    _extract_profile_indicator_fields,
    _passes_profile_filters,
    _uses_pipeline_filters,
)
from app.utils.pipeline_profile_filters import effective_pipeline_level
from app.services.score_engine import resolve_profile_scoring_rules
from app.services.profile_engine import ProfileEngine


def test_custom_watchlists_are_monitoring_boards():
    assert _uses_pipeline_filters("custom") is False
    assert _uses_pipeline_filters("Custom") is False
    assert _uses_pipeline_filters(None) is False


def test_pipeline_levels_keep_filter_enforcement():
    assert _uses_pipeline_filters("L1") is True
    assert _uses_pipeline_filters("L2") is True
    assert _uses_pipeline_filters("L3") is True


def test_source_pool_watchlist_with_profile_filters_is_promoted_to_l1():
    profile_config = {
        "filters": {
            "conditions": [
                {"field": "market_cap", "operator": ">=", "value": 10_000_000},
            ]
        }
    }

    assert effective_pipeline_level(
        "custom",
        source_pool_id="pool-123",
        profile_config=profile_config,
    ) == "L1"


def test_source_pool_watchlist_without_profile_filters_remains_monitoring_board():
    assert effective_pipeline_level(
        "custom",
        source_pool_id="pool-123",
        profile_config={"filters": {"conditions": []}},
    ) == "custom"


def test_profile_indicator_columns_follow_filter_conditions_order():
    """Watchlist columns are derived from Filter Conditions ONLY (not Signals)."""
    profile_config = {
        "filters": {
            "conditions": [
                {"field": "volume_24h"},
                {"field": "market_cap"},
                {"field": "spread_pct"},
            ]
        },
        "signals": {
            "conditions": [
                # rsi is a signal condition — must NOT appear in watchlist columns
                {"field": "rsi"},
            ]
        },
    }

    indicators = _extract_profile_indicator_fields(profile_config)

    assert indicators == [
        {"key": "_meta:volume_24h", "label": "Volume 24h", "field": "volume_24h"},
        {"key": "_meta:market_cap", "label": "Market Cap", "field": "market_cap"},
        {"key": "spread_pct", "label": "Spread%", "field": "spread_pct"},
    ]


def _make_level_wl_show_score(level: str) -> bool:
    """Helper to compute show_score in the same way the endpoint does."""
    return (level or "").upper() in {"L2", "L3"}


def test_show_score_hidden_for_pool_and_l1():
    """Stage 0 (custom/pool) and Stage 1 (L1) must not show Alpha Score."""
    assert _make_level_wl_show_score("custom") is False
    assert _make_level_wl_show_score("Custom") is False
    assert _make_level_wl_show_score("L1") is False
    assert _make_level_wl_show_score(None) is False


def test_show_score_visible_for_l2_and_l3():
    """Stage 2 (L2) and Stage 3 (L3) must show Alpha Score."""
    assert _make_level_wl_show_score("L2") is True
    assert _make_level_wl_show_score("L3") is True


def test_scoring_selected_rule_ids_takes_priority_over_filter_rule_ids():
    """scoring.selected_rule_ids (new contract) resolves before filters.conditions[].rule_id."""
    global_rules = [
        {"id": "rsi_1", "indicator": "rsi", "operator": "<=", "value": 30, "points": 25, "category": "momentum"},
        {"id": "adx_1", "indicator": "adx", "operator": ">=", "value": 20, "points": 20, "category": "market_structure"},
        {"id": "vol_1", "indicator": "volume_spike", "operator": ">=", "value": 1.5, "points": 15, "category": "liquidity"},
    ]
    profile_config = {
        # New contract: scoring.selected_rule_ids wins
        "scoring": {
            "selected_rule_ids": ["adx_1", "vol_1"],
        },
        # Legacy filter rule_id should be ignored when selected_rule_ids is present
        "filters": {
            "conditions": [
                {"field": "rsi", "rule_id": "rsi_1"},
            ]
        },
    }

    resolved = resolve_profile_scoring_rules(global_rules, profile_config)
    assert [r["id"] for r in resolved] == ["adx_1", "vol_1"]


def test_scoring_selected_rule_ids_empty_falls_back_to_filter_rule_ids():
    """When scoring.selected_rule_ids is empty, legacy filters.conditions[].rule_id is used."""
    global_rules = [
        {"id": "rsi_1", "indicator": "rsi", "operator": "<=", "value": 30, "points": 25, "category": "momentum"},
        {"id": "adx_1", "indicator": "adx", "operator": ">=", "value": 20, "points": 20, "category": "market_structure"},
    ]
    profile_config = {
        "scoring": {
            "selected_rule_ids": [],  # empty → fall through to legacy
        },
        "filters": {
            "conditions": [
                {"field": "rsi", "rule_id": "rsi_1"},
            ]
        },
    }

    resolved = resolve_profile_scoring_rules(global_rules, profile_config)
    assert [r["id"] for r in resolved] == ["rsi_1"]


# ── strict_indicators tests ───────────────────────────────────────────────────

_EMA_CONDITIONS = [
    {"field": "ema9_gt_ema50", "operator": "==", "value": True},
]

_VOLUME_CONDITIONS = [
    {"field": "volume_24h", "operator": ">=", "value": 500_000},
]

_MIXED_CONDITIONS = _VOLUME_CONDITIONS + _EMA_CONDITIONS


def test_passes_profile_filters_lenient_skips_missing_indicator():
    """Default (strict_indicators=False): missing indicator condition is skipped → asset passes."""
    asset = {"symbol": "PEPE_USDT", "volume_24h": 12_000_000}  # no ema9_gt_ema50
    assert _passes_profile_filters(asset, _EMA_CONDITIONS) is True


def test_passes_profile_filters_strict_fails_missing_indicator():
    """strict_indicators=True: missing indicator condition FAILS → asset rejected."""
    asset = {"symbol": "PEPE_USDT", "volume_24h": 12_000_000}  # no ema9_gt_ema50
    assert _passes_profile_filters(asset, _EMA_CONDITIONS, strict_indicators=True) is False


def test_passes_profile_filters_strict_passes_when_indicator_present_and_true():
    """strict_indicators=True: asset with bullish indicator passes."""
    asset = {"symbol": "BTC_USDT", "volume_24h": 1_000_000, "ema9_gt_ema50": True}
    assert _passes_profile_filters(asset, _MIXED_CONDITIONS, strict_indicators=True) is True


def test_passes_profile_filters_strict_fails_when_indicator_present_and_false():
    """strict_indicators=True: asset with bearish indicator fails."""
    asset = {"symbol": "BTC_USDT", "volume_24h": 1_000_000, "ema9_gt_ema50": False}
    assert _passes_profile_filters(asset, _MIXED_CONDITIONS, strict_indicators=True) is False


def test_profile_engine_apply_filters_strict_rejects_no_indicator_asset():
    """_apply_filters(strict_indicators=True): asset with no indicator data fails indicator condition."""
    profile_config = {
        "filters": {
            "conditions": [{"field": "ema9_gt_ema50", "operator": "==", "value": True}],
            "logic": "AND",
        }
    }
    engine = ProfileEngine(profile_config)
    asset_no_ind = {"symbol": "PEPE_USDT", "indicators": {}}
    result = engine._apply_filters([asset_no_ind], strict_indicators=True)
    assert result == [], "Asset without indicators should be rejected in strict pipeline mode"


def test_profile_engine_apply_filters_strict_passes_bullish_asset():
    """_apply_filters(strict_indicators=True): asset with bullish EMA passes."""
    profile_config = {
        "filters": {
            "conditions": [{"field": "ema9_gt_ema50", "operator": "==", "value": True}],
            "logic": "AND",
        }
    }
    engine = ProfileEngine(profile_config)
    asset_bullish = {"symbol": "BTC_USDT", "indicators": {"ema9_gt_ema50": True}}
    result = engine._apply_filters([asset_bullish], strict_indicators=True)
    assert len(result) == 1, "Bullish asset should pass strict pipeline filter"


def test_profile_engine_apply_filters_lenient_passes_no_indicator_asset():
    """_apply_filters(strict_indicators=False, default): asset with no indicators is NOT rejected."""
    profile_config = {
        "filters": {
            "conditions": [{"field": "ema9_gt_ema50", "operator": "==", "value": True}],
            "logic": "AND",
        }
    }
    engine = ProfileEngine(profile_config)
    asset_no_ind = {"symbol": "PEPE_USDT", "indicators": {}}
    result = engine._apply_filters([asset_no_ind])  # default strict_indicators=False
    assert len(result) == 1, "Lenient mode should not reject asset with missing indicators"

