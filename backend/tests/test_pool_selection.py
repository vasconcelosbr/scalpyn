import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.pool_selection import (
    apply_pool_asset_exclusions,
    apply_pool_discovery_filters,
    extract_profile_discovery_thresholds,
    is_auto_discovery_enabled,
)


def test_extract_profile_discovery_thresholds_reads_volume_and_market_cap():
    profile_config = {
        "filters": {
            "conditions": [
                {"field": "volume_24h", "operator": ">=", "value": 500_000},
                {"field": "market_cap", "operator": ">=", "value": 10_000_000},
                {"field": "rsi", "operator": "<", "value": 60},
            ],
        },
    }

    min_volume, min_market_cap, profile_applied = extract_profile_discovery_thresholds(profile_config)

    assert min_volume == 500_000
    assert min_market_cap == 10_000_000
    assert profile_applied is True


def test_apply_pool_discovery_filters_enforces_market_cap_before_cap_limit():
    result = apply_pool_discovery_filters(
        {"AAA_USDT", "BBB_USDT", "CCC_USDT"},
        vol_map={
            "AAA_USDT": 2_000_000,
            "BBB_USDT": 2_000_000,
            "CCC_USDT": 2_000_000,
        },
        market_cap_map={
            "AAA_USDT": 15_000_000,
            "BBB_USDT": 5_000_000,
        },
        min_volume=1_000_000,
        min_market_cap=10_000_000,
        max_assets=1,
    )

    assert result["pre_volume_count"] == 3
    assert result["post_volume_count"] == 3
    assert result["pre_market_cap_count"] == 3
    assert result["post_market_cap_count"] == 1
    assert result["symbols"] == {"AAA_USDT"}


def test_pool_asset_exclusion_survives_a_future_discovery_selection():
    assert apply_pool_asset_exclusions(
        {"BTC_USDT", "XAUT_USDT"},
        {"XAUT_USDT"},
    ) == {"BTC_USDT"}


def test_auto_discovery_is_fail_closed_when_operator_disables_it():
    assert is_auto_discovery_enabled({"auto_refresh": True}) is True
    assert is_auto_discovery_enabled({"auto_refresh": False}) is False
    assert is_auto_discovery_enabled({}) is False
    assert is_auto_discovery_enabled({"auto_refresh": "true"}) is False
