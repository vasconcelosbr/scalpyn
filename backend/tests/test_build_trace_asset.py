"""Regression coverage for `build_trace_asset` (task #69).

The watchlist evaluation trace used to display "SEM DADOS / aguardando
coleta" for indicators that were actually present in the database. The
root cause was that `_resolve_watchlist_pipeline` seeded the asset dict
with `meta.get(...)` first (often `None`) and then merged indicators
with a `if k not in asset_entry` guard, so any meta `None` would
shadow a perfectly valid indicator value.

These tests pin down the merge contract of the unified helper so the
same regression cannot reappear.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.pipeline_rejections import (  # noqa: E402
    build_asset_evaluation_trace,
    build_trace_asset,
)


# Fixture mirroring SUI's actual data shape on 2026-04-27: indicators_json
# carries spread_pct/orderbook_depth_usdt/bb_width/adx, but
# market_metadata only knows about market_cap/volume_24h/price_change_24h.
SUI_INDICATORS = {
    "rsi": 48.5,
    "adx": 22.4,
    "bb_width": 0.0541,
    "spread_pct": 0.0108,
    "orderbook_depth_usdt": 184_500.0,
    "taker_ratio": 0.5123,
    "volume_spike": 1.42,
    "macd_signal": "bullish",
    # non-scalar entry must be dropped by the helper
    "extra_payload": {"some": "object"},
    # explicit None must not poison the merge
    "ema_align_label": None,
}

SUI_META = {
    "current_price": 2.81,
    "price_change_24h": -1.23,
    "volume_24h": 9_812_345.0,
    "market_cap": 8_400_000_000.0,
    # spread_pct / orderbook_depth_usdt are missing here on purpose —
    # this is the exact shape that triggered the original bug.
    "spread_pct": None,
    "orderbook_depth_usdt": None,
}


def test_indicators_win_when_meta_value_is_none():
    """Hybrid fields must come from indicators when meta is None."""
    asset = build_trace_asset("SUI_USDT", indicators=SUI_INDICATORS, meta=SUI_META)

    assert asset["spread_pct"] == 0.0108
    assert asset["orderbook_depth_usdt"] == 184_500.0


def test_required_meta_fields_always_present_even_when_missing():
    """DB-write paths look up these keys directly — they must never KeyError."""
    asset = build_trace_asset("ABC_USDT")

    for key in ("current_price", "price_change_24h", "volume_24h", "market_cap"):
        assert key in asset
        assert asset[key] is None
    assert asset["alpha_score"] is None


def test_meta_values_propagate_when_indicators_missing_them():
    asset = build_trace_asset("SUI_USDT", indicators=SUI_INDICATORS, meta=SUI_META)

    assert asset["volume_24h"] == 9_812_345.0
    assert asset["market_cap"] == 8_400_000_000.0
    assert asset["current_price"] == 2.81
    assert asset["price_change_24h"] == -1.23
    # change_24h alias auto-populated from price_change_24h.
    assert asset["change_24h"] == -1.23
    # price alias auto-populated from current_price.
    assert asset["price"] == 2.81


def test_non_scalar_indicator_values_are_dropped():
    asset = build_trace_asset("SUI_USDT", indicators=SUI_INDICATORS)

    assert "extra_payload" not in asset


def test_explicit_none_indicator_does_not_overwrite_existing_value():
    asset = build_trace_asset(
        "SUI_USDT",
        indicators={"rsi": None, "adx": 30.0},
        meta={},
    )
    assert "rsi" not in asset
    assert asset["adx"] == 30.0


def test_alias_bollinger_width_resolves_from_canonical():
    """Profile rule referencing legacy `bollinger_width` must find `bb_width`."""
    asset = build_trace_asset(
        "SUI_USDT",
        indicators={"bb_width": 0.0541},
    )
    assert asset["bollinger_width"] == 0.0541
    assert asset["bb_width"] == 0.0541


def test_alias_canonical_resolves_from_legacy_spelling():
    """Reverse direction: legacy payload uses the alias spelling."""
    asset = build_trace_asset(
        "SUI_USDT",
        indicators={"bollinger_width": 0.07, "volume_24h_usdt": 4_500_000.0},
    )
    assert asset["bb_width"] == 0.07
    assert asset["volume_24h"] == 4_500_000.0


def test_alpha_score_passthrough():
    asset = build_trace_asset("SUI_USDT", alpha_score=72.4)
    assert asset["alpha_score"] == 72.4
    assert asset["score"] == 72.4


def test_string_indicators_only_propagate_for_known_keys():
    asset = build_trace_asset(
        "SUI_USDT",
        indicators={"macd_signal": "bullish", "random_string": "ignored"},
    )
    assert asset["macd_signal"] == "bullish"
    assert "random_string" not in asset


def test_trace_no_longer_reports_sem_dados_for_sui_block_rule():
    """End-to-end: a profile referencing spread_pct must NOT skip with
    `indicator_not_available` when the indicator is present in
    indicators_json but missing from market_metadata.
    """
    profile_config = {
        "block_rules": {
            "blocks": [
                {
                    "id": "block_wide_spread",
                    "name": "Wide Spread",
                    "logic": "AND",
                    "conditions": [
                        {"indicator": "spread_pct", "operator": ">", "value": 0.5},
                    ],
                }
            ]
        },
        "filters": {
            "logic": "AND",
            "conditions": [
                {"field": "bb_width", "operator": ">=", "value": 0.01},
                {"field": "adx", "operator": ">=", "value": 15},
            ],
        },
    }

    asset = build_trace_asset(
        "SUI_USDT", indicators=SUI_INDICATORS, meta=SUI_META, alpha_score=64.0
    )
    trace = build_asset_evaluation_trace(asset, profile_config=profile_config)

    by_indicator = {item["indicator"]: item for item in trace}

    # Block rule evaluated against indicators_json value (0.0108 < 0.5):
    # block did NOT trigger → status PASS, NOT skipped for missing data.
    assert by_indicator["Wide Spread"]["status"] == "PASS"
    assert by_indicator["Wide Spread"].get("reason") != "indicator_not_available"

    # Filters on indicator-only fields actually evaluate.
    assert by_indicator["BB Width"]["status"] == "PASS"
    assert by_indicator["BB Width"]["current_value"] == 0.0541
    assert by_indicator["ADX"]["status"] == "PASS"
    assert by_indicator["ADX"]["current_value"] == 22.4

    # No item should be SKIPPED with indicator_not_available for fields
    # that were present in the indicators payload.
    for label in ("Wide Spread", "BB Width", "ADX"):
        item = by_indicator[label]
        assert item.get("reason") != "indicator_not_available", (
            f"{label} was wrongly reported as missing data: {item}"
        )
