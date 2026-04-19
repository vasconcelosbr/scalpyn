import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.utils.pipeline_profile_filters import select_profile_filter_conditions


def test_relaxes_strict_meta_conditions_below_10_percent_coverage():
    conditions = [
        {"field": "volume_24h", "operator": ">=", "value": 1_000_000},
        {"field": "rsi", "operator": "<", "value": 70},
    ]

    selected = select_profile_filter_conditions(
        conditions,
        total_symbols=200,
        symbols_with_meta=5,
    )

    assert selected["relaxed_strict_meta"] is True
    assert selected["conditions"] == [{"field": "rsi", "operator": "<", "value": 70}]


def test_keeps_strict_meta_conditions_at_10_percent_coverage():
    conditions = [
        {"field": "market_cap", "operator": ">=", "value": 50_000_000},
        {"field": "adx", "operator": ">", "value": 20},
    ]

    selected = select_profile_filter_conditions(
        conditions,
        total_symbols=200,
        symbols_with_meta=20,
    )

    assert selected["relaxed_strict_meta"] is False
    assert selected["conditions"] == conditions


def test_handles_profiles_with_only_strict_meta_conditions():
    conditions = [
        {"field": "spread_pct", "operator": "<", "value": 0.3},
        {"field": "orderbook_depth_usdt", "operator": ">=", "value": 10_000},
    ]

    selected = select_profile_filter_conditions(
        conditions,
        total_symbols=100,
        symbols_with_meta=0,
    )

    assert selected["relaxed_strict_meta"] is True
    assert selected["conditions"] == []
