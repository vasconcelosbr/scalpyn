import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.api.watchlists import _extract_profile_indicator_fields, _uses_pipeline_filters


def test_custom_watchlists_are_monitoring_boards():
    assert _uses_pipeline_filters("custom") is False
    assert _uses_pipeline_filters("Custom") is False
    assert _uses_pipeline_filters(None) is False


def test_pipeline_levels_keep_filter_enforcement():
    assert _uses_pipeline_filters("L1") is True
    assert _uses_pipeline_filters("L2") is True
    assert _uses_pipeline_filters("L3") is True


def test_profile_indicator_columns_follow_profile_conditions_order():
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
                {"field": "rsi"},
            ]
        },
    }

    indicators = _extract_profile_indicator_fields(profile_config)

    assert indicators == [
        {"key": "_meta:volume_24h", "label": "Volume 24h", "field": "volume_24h"},
        {"key": "_meta:market_cap", "label": "Market Cap", "field": "market_cap"},
        {"key": "spread_pct", "label": "Spread%", "field": "spread_pct"},
        {"key": "rsi", "label": "RSI", "field": "rsi"},
    ]
