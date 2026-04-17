"""
Test suite for market_cap filter fix in _passes_profile_filters.

Validates that the fix for the market_cap bypass bug is working correctly:
- Strict meta fields (market_cap, volume_24h, price, etc.) FAIL when None
- Indicator fields (RSI, ADX, etc.) are SKIPPED when None
"""
import sys
import os

# Add backend to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.api.watchlists import _passes_profile_filters


def test_market_cap_none_fails_filter():
    """Assets with market_cap=None should FAIL market_cap filters."""
    asset = {
        "symbol": "TEST_USDT",
        "market_cap": None,  # Unknown market cap
        "volume_24h": 1000000,
        "price": 1.5,
    }

    conditions = [
        {"field": "market_cap", "operator": ">=", "value": 5000000}  # >= 5M
    ]

    result = _passes_profile_filters(asset, conditions, logic="AND")
    assert result is False, "Asset with market_cap=None should FAIL market_cap >= 5M filter"
    print("✓ PASS: market_cap=None fails market_cap >= 5M filter")


def test_market_cap_zero_fails_filter():
    """Assets with market_cap=0 should FAIL market_cap filters (explicit zero)."""
    asset = {
        "symbol": "TEST_USDT",
        "market_cap": 0,  # Zero market cap
        "volume_24h": 1000000,
        "price": 1.5,
    }

    conditions = [
        {"field": "market_cap", "operator": ">=", "value": 5000000}  # >= 5M
    ]

    result = _passes_profile_filters(asset, conditions, logic="AND")
    assert result is False, "Asset with market_cap=0 should FAIL market_cap >= 5M filter"
    print("✓ PASS: market_cap=0 fails market_cap >= 5M filter")


def test_market_cap_valid_passes_filter():
    """Assets with valid market_cap >= threshold should PASS."""
    asset = {
        "symbol": "BTC_USDT",
        "market_cap": 10000000,  # 10M
        "volume_24h": 1000000,
        "price": 50000,
    }

    conditions = [
        {"field": "market_cap", "operator": ">=", "value": 5000000}  # >= 5M
    ]

    result = _passes_profile_filters(asset, conditions, logic="AND")
    assert result is True, "Asset with market_cap=10M should PASS market_cap >= 5M filter"
    print("✓ PASS: market_cap=10M passes market_cap >= 5M filter")


def test_market_cap_below_threshold_fails():
    """Assets with market_cap < threshold should FAIL."""
    asset = {
        "symbol": "SMALL_USDT",
        "market_cap": 1000000,  # 1M
        "volume_24h": 1000000,
        "price": 0.5,
    }

    conditions = [
        {"field": "market_cap", "operator": ">=", "value": 5000000}  # >= 5M
    ]

    result = _passes_profile_filters(asset, conditions, logic="AND")
    assert result is False, "Asset with market_cap=1M should FAIL market_cap >= 5M filter"
    print("✓ PASS: market_cap=1M fails market_cap >= 5M filter")


def test_volume_24h_none_fails_filter():
    """Assets with volume_24h=None should FAIL volume filters (strict meta field)."""
    asset = {
        "symbol": "TEST_USDT",
        "market_cap": 10000000,
        "volume_24h": None,  # Unknown volume
        "price": 1.5,
    }

    conditions = [
        {"field": "volume_24h", "operator": ">=", "value": 500000}  # >= 500k
    ]

    result = _passes_profile_filters(asset, conditions, logic="AND")
    assert result is False, "Asset with volume_24h=None should FAIL volume filter"
    print("✓ PASS: volume_24h=None fails volume_24h >= 500k filter")


def test_price_none_fails_filter():
    """Assets with price=None should FAIL price filters (strict meta field)."""
    asset = {
        "symbol": "TEST_USDT",
        "market_cap": 10000000,
        "volume_24h": 1000000,
        "price": None,  # Unknown price
    }

    conditions = [
        {"field": "price", "operator": ">", "value": 1.0}
    ]

    result = _passes_profile_filters(asset, conditions, logic="AND")
    assert result is False, "Asset with price=None should FAIL price filter"
    print("✓ PASS: price=None fails price > 1.0 filter")


def test_rsi_none_skipped_passes():
    """Indicator fields like RSI should be SKIPPED (not failed) when None."""
    asset = {
        "symbol": "TEST_USDT",
        "market_cap": 10000000,
        "volume_24h": 1000000,
        "price": 50000,
        "rsi": None,  # RSI not computed yet
    }

    # Condition with RSI (indicator field) - should be skipped when None
    conditions = [
        {"field": "rsi", "operator": "<", "value": 70}
    ]

    result = _passes_profile_filters(asset, conditions, logic="AND")
    # When all conditions are skipped (no results), function returns True
    assert result is True, "Asset with rsi=None should SKIP (not fail) RSI filter"
    print("✓ PASS: rsi=None is skipped (asset passes)")


def test_combined_strict_and_indicator_filters():
    """Test combination of strict meta filters and indicator filters."""
    asset = {
        "symbol": "BTC_USDT",
        "market_cap": 10000000,  # 10M - passes
        "volume_24h": 1000000,   # 1M - passes
        "price": 50000,          # passes
        "rsi": None,             # Should be skipped
        "adx": 25,               # passes
    }

    conditions = [
        {"field": "market_cap", "operator": ">=", "value": 5000000},  # strict - passes
        {"field": "volume_24h", "operator": ">=", "value": 500000},   # strict - passes
        {"field": "rsi", "operator": "<", "value": 70},               # indicator - skipped
        {"field": "adx", "operator": ">", "value": 20},               # indicator - passes
    ]

    result = _passes_profile_filters(asset, conditions, logic="AND")
    assert result is True, "Asset should pass when strict fields valid and indicators present/skipped"
    print("✓ PASS: Combined strict + indicator filters work correctly")


def test_strict_meta_none_with_and_logic():
    """With AND logic, any strict meta field=None should FAIL the entire filter."""
    asset = {
        "symbol": "TEST_USDT",
        "market_cap": None,      # FAIL - strict field
        "volume_24h": 2000000,   # passes
        "price": 10,             # passes
    }

    conditions = [
        {"field": "market_cap", "operator": ">=", "value": 5000000},
        {"field": "volume_24h", "operator": ">=", "value": 1000000},
        {"field": "price", "operator": ">", "value": 1.0},
    ]

    result = _passes_profile_filters(asset, conditions, logic="AND")
    assert result is False, "AND logic: one strict field=None should fail entire filter"
    print("✓ PASS: AND logic with one strict field=None fails correctly")


def test_strict_meta_none_with_or_logic():
    """With OR logic, one strict field passing should pass even if another is None."""
    asset = {
        "symbol": "TEST_USDT",
        "market_cap": None,      # FAIL - strict field
        "volume_24h": 2000000,   # passes
        "price": 10,             # passes
    }

    conditions = [
        {"field": "market_cap", "operator": ">=", "value": 5000000},  # fails (None)
        {"field": "volume_24h", "operator": ">=", "value": 1000000},  # passes
    ]

    result = _passes_profile_filters(asset, conditions, logic="OR")
    assert result is True, "OR logic: one passing condition should pass even if another is None"
    print("✓ PASS: OR logic with strict field=None works correctly")


def test_change_24h_alias():
    """Test that change_24h and price_change_24h are aliased correctly."""
    asset = {
        "symbol": "BTC_USDT",
        "market_cap": 10000000,
        "volume_24h": 1000000,
        "price_change_24h": 5.5,  # Using price_change_24h
        # change_24h not present
    }

    # Filter uses change_24h field name
    conditions = [
        {"field": "change_24h", "operator": ">", "value": 2.0}
    ]

    result = _passes_profile_filters(asset, conditions, logic="AND")
    assert result is True, "change_24h should alias to price_change_24h"
    print("✓ PASS: change_24h aliases to price_change_24h correctly")


def test_spread_pct_strict_meta():
    """Test that spread_pct is treated as strict meta field."""
    asset = {
        "symbol": "TEST_USDT",
        "market_cap": 10000000,
        "volume_24h": 1000000,
        "spread_pct": None,  # Unknown spread
    }

    conditions = [
        {"field": "spread_pct", "operator": "<", "value": 1.5}
    ]

    result = _passes_profile_filters(asset, conditions, logic="AND")
    assert result is False, "spread_pct=None should FAIL spread filter (strict meta)"
    print("✓ PASS: spread_pct=None fails spread filter")


def test_orderbook_depth_strict_meta():
    """Test that orderbook_depth_usdt is treated as strict meta field."""
    asset = {
        "symbol": "TEST_USDT",
        "market_cap": 10000000,
        "volume_24h": 1000000,
        "orderbook_depth_usdt": None,  # Unknown depth
    }

    conditions = [
        {"field": "orderbook_depth_usdt", "operator": ">=", "value": 5000}
    ]

    result = _passes_profile_filters(asset, conditions, logic="AND")
    assert result is False, "orderbook_depth_usdt=None should FAIL depth filter (strict meta)"
    print("✓ PASS: orderbook_depth_usdt=None fails depth filter")


def test_between_operator_with_none():
    """Test that 'between' operator fails when value is None."""
    asset = {
        "symbol": "TEST_USDT",
        "market_cap": None,
        "volume_24h": 1000000,
    }

    conditions = [
        {"field": "market_cap", "operator": "between", "min": 1000000, "max": 10000000}
    ]

    result = _passes_profile_filters(asset, conditions, logic="AND")
    assert result is False, "between operator should fail when field is None (strict meta)"
    print("✓ PASS: between operator with market_cap=None fails")


def test_between_operator_with_valid_value():
    """Test that 'between' operator works with valid values."""
    asset = {
        "symbol": "BTC_USDT",
        "market_cap": 5000000,  # 5M - in range
        "volume_24h": 1000000,
    }

    conditions = [
        {"field": "market_cap", "operator": "between", "min": 1000000, "max": 10000000}
    ]

    result = _passes_profile_filters(asset, conditions, logic="AND")
    assert result is True, "between operator should pass when value in range"
    print("✓ PASS: between operator with valid value passes")


def test_empty_conditions_list():
    """Test that empty conditions list returns True."""
    asset = {
        "symbol": "TEST_USDT",
        "market_cap": None,
    }

    result = _passes_profile_filters(asset, [], logic="AND")
    assert result is True, "Empty conditions should return True"
    print("✓ PASS: Empty conditions list returns True")


def test_no_conditions_match_field():
    """Test that when no conditions have valid fields, returns True."""
    asset = {
        "symbol": "TEST_USDT",
        "market_cap": 10000000,
    }

    conditions = [
        {"operator": ">=", "value": 5000000}  # No field specified
    ]

    result = _passes_profile_filters(asset, conditions, logic="AND")
    assert result is True, "When no conditions have valid fields, should return True"
    print("✓ PASS: Conditions without fields return True")


if __name__ == "__main__":
    print("\n" + "="*70)
    print("Testing market_cap filter fix in _passes_profile_filters")
    print("="*70 + "\n")

    tests = [
        test_market_cap_none_fails_filter,
        test_market_cap_zero_fails_filter,
        test_market_cap_valid_passes_filter,
        test_market_cap_below_threshold_fails,
        test_volume_24h_none_fails_filter,
        test_price_none_fails_filter,
        test_rsi_none_skipped_passes,
        test_combined_strict_and_indicator_filters,
        test_strict_meta_none_with_and_logic,
        test_strict_meta_none_with_or_logic,
        test_change_24h_alias,
        test_spread_pct_strict_meta,
        test_orderbook_depth_strict_meta,
        test_between_operator_with_none,
        test_between_operator_with_valid_value,
        test_empty_conditions_list,
        test_no_conditions_match_field,
    ]

    passed = 0
    failed = 0

    for test_func in tests:
        try:
            test_func()
            passed += 1
        except AssertionError as e:
            print(f"✗ FAIL: {test_func.__name__}")
            print(f"  Error: {e}")
            failed += 1
        except Exception as e:
            print(f"✗ ERROR: {test_func.__name__}")
            print(f"  Exception: {e}")
            failed += 1

    print("\n" + "="*70)
    print(f"Test Results: {passed} passed, {failed} failed")
    print("="*70 + "\n")

    if failed > 0:
        sys.exit(1)
    else:
        print("✓ ALL TESTS PASSED - market_cap filter fix is working correctly!\n")
        sys.exit(0)
