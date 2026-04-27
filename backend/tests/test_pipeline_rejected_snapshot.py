import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.pipeline_rejections import (
    evaluate_rejections,
    recompute_rejection_trace,
    rejection_metrics,
)


def test_block_rules_reject_before_filters_and_mark_remaining_trace_as_skipped():
    profile_config = {
        "filters": {
            "logic": "AND",
            "conditions": [
                {"field": "volume_24h", "operator": ">=", "value": 1_000_000},
            ],
        },
        "block_rules": {
            "blocks": [
                {
                    "id": "block_overbought",
                    "name": "Overbought RSI",
                    "logic": "AND",
                    "conditions": [
                        {"indicator": "rsi", "operator": ">", "value": 75},
                    ],
                }
            ]
        },
    }

    approved, rejected = evaluate_rejections(
        [{"symbol": "ETH_USDT", "rsi": 82, "volume_24h": 2_000_000}],
        profile_config=profile_config,
        stage="L1",
        profile_id="profile-1",
    )

    assert approved == []
    assert len(rejected) == 1
    assert rejected[0]["failed_type"] == "block_rule"
    assert rejected[0]["failed_indicator"] == "Overbought RSI"
    assert [item["status"] for item in rejected[0]["evaluation_trace"]] == ["FAIL", "SKIPPED"]


def test_filter_failure_trace_preserves_profile_order_and_stop_point():
    profile_config = {
        "filters": {
            "logic": "AND",
            "conditions": [
                {"field": "volume_24h", "operator": ">=", "value": 1_000_000},
                {"field": "rsi", "operator": "<", "value": 55},
                {"field": "adx", "operator": ">", "value": 20},
            ],
        },
        "block_rules": {"blocks": []},
    }

    approved, rejected = evaluate_rejections(
        [{"symbol": "BTC_USDT", "volume_24h": 5_000_000, "rsi": 62, "adx": 29}],
        profile_config=profile_config,
        stage="L2",
        profile_id="profile-2",
    )

    assert approved == []
    assert len(rejected) == 1
    assert rejected[0]["failed_type"] == "filter"
    assert rejected[0]["failed_indicator"] == "RSI"
    assert [item["status"] for item in rejected[0]["evaluation_trace"]] == ["PASS", "FAIL", "SKIPPED"]


def test_approved_assets_include_full_pass_trace():
    profile_config = {
        "filters": {
            "logic": "AND",
            "conditions": [
                {"field": "volume_24h", "operator": ">=", "value": 1_000_000},
                {"field": "rsi", "operator": "<", "value": 65},
            ],
        },
        "block_rules": {
            "blocks": [
                {
                    "id": "block_overbought",
                    "name": "Overbought RSI",
                    "logic": "AND",
                    "conditions": [
                        {"indicator": "rsi", "operator": ">", "value": 75},
                    ],
                }
            ]
        },
    }

    approved, rejected = evaluate_rejections(
        [{"symbol": "SOL_USDT", "volume_24h": 5_000_000, "rsi": 58}],
        profile_config=profile_config,
        stage="L2",
        profile_id="profile-3",
    )

    assert rejected == []
    assert len(approved) == 1
    assert [item["status"] for item in approved[0]["evaluation_trace"]] == ["PASS", "PASS", "PASS"]


def test_pool_stage_rejections_are_labeled_as_pool():
    profile_config = {
        "filters": {
            "logic": "AND",
            "conditions": [
                {"field": "market_cap", "operator": ">=", "value": 1_000_000_000},
            ],
        },
        "block_rules": {"blocks": []},
    }

    approved, rejected = evaluate_rejections(
        [{"symbol": "DOGE_USDT", "market_cap": 950_000_000}],
        profile_config=profile_config,
        stage="POOL",
        profile_id="profile-3",
    )

    assert approved == []
    assert len(rejected) == 1
    assert rejected[0]["stage"] == "POOL"
    assert rejected[0]["failed_indicator"] == "Market Cap"
    assert rejected[0]["evaluation_trace"][0]["status"] == "FAIL"


def test_cascade_skipped_blocks_carry_short_circuit_reason():
    """When the first block fails the asset is rejected immediately; the
    remaining blocks/filters must be marked SKIPPED with
    `reason="cascade_short_circuit"` so the frontend can label them
    'PULADO' instead of the misleading 'SEM DADOS / aguardando coleta'.
    """
    profile_config = {
        "filters": {
            "logic": "AND",
            "conditions": [
                {"field": "adx", "operator": ">=", "value": 15},
            ],
        },
        "block_rules": {
            "blocks": [
                {
                    "id": "block_taker_ratio",
                    "name": "Taker Ratio",
                    "logic": "AND",
                    "conditions": [
                        {"indicator": "taker_ratio", "operator": "<", "value": 1.04},
                    ],
                },
                {
                    "id": "block_spike",
                    "name": "Spike",
                    "logic": "AND",
                    "conditions": [
                        {"indicator": "volume_spike", "operator": "<", "value": 1.2},
                    ],
                },
                {
                    "id": "block_bbw",
                    "name": "BB Width",
                    "logic": "AND",
                    "conditions": [
                        {"indicator": "bb_width", "operator": "<", "value": 0.01},
                    ],
                },
            ]
        },
    }

    # taker_ratio < 1.04 → first block triggers (FAIL); the next two
    # blocks and the filter must be marked SKIPPED with the new reason.
    _, rejected = evaluate_rejections(
        [{"symbol": "UNI_USDT", "taker_ratio": 0.4087, "volume_spike": 0.5, "bb_width": 0.04, "adx": 22}],
        profile_config=profile_config,
        stage="L3",
        profile_id="profile-cascade",
    )

    assert len(rejected) == 1
    trace = rejected[0]["evaluation_trace"]

    by_indicator = {item["indicator"]: item for item in trace}
    assert by_indicator["Taker Ratio"]["status"] == "FAIL"

    # Cascade-skipped blocks carry the new reason.
    spike = by_indicator["Spike"]
    bbw = by_indicator["BB Width"]
    assert spike["status"] == "SKIPPED"
    assert spike["reason"] == "cascade_short_circuit"
    assert bbw["status"] == "SKIPPED"
    assert bbw["reason"] == "cascade_short_circuit"

    # The downstream filter is also cascade-skipped.
    adx = by_indicator["ADX"]
    assert adx["status"] == "SKIPPED"
    assert adx["reason"] == "cascade_short_circuit"


def test_filter_cascade_emits_short_circuit_reason_for_remaining_filters():
    """Filter cascade (AND logic) must propagate the new reason too."""
    profile_config = {
        "filters": {
            "logic": "AND",
            "conditions": [
                {"field": "rsi", "operator": "<", "value": 55},
                {"field": "adx", "operator": ">=", "value": 15},
                {"field": "volume_24h", "operator": ">", "value": 1_000_000},
            ],
        },
        "block_rules": {"blocks": []},
    }

    _, rejected = evaluate_rejections(
        [{"symbol": "BTC_USDT", "rsi": 62, "adx": 22, "volume_24h": 5_000_000}],
        profile_config=profile_config,
        stage="L2",
        profile_id="profile-filter-cascade",
    )

    trace = rejected[0]["evaluation_trace"]
    statuses = [(item["indicator"], item["status"], item.get("reason")) for item in trace]
    # rsi FAIL → adx + volume_24h are cascade-skipped.
    assert statuses == [
        ("RSI", "FAIL", None),
        ("ADX", "SKIPPED", "cascade_short_circuit"),
        ("Volume 24h", "SKIPPED", "cascade_short_circuit"),
    ]


def test_taker_ratio_above_plausibility_bound_is_invalid_value():
    """Regression: SUI showed taker_ratio == 8.98e9 in prod. The trace
    must mark this as `indicator_invalid_value`, not let the absurd
    number drive the rule outcome.
    """
    profile_config = {
        "filters": {"logic": "AND", "conditions": []},
        "block_rules": {
            "blocks": [
                {
                    "id": "block_taker",
                    "name": "Taker Ratio",
                    "logic": "AND",
                    "conditions": [
                        {"indicator": "taker_ratio", "operator": "<", "value": 1.04},
                    ],
                }
            ]
        },
    }

    approved, rejected = evaluate_rejections(
        [{"symbol": "SUI_USDT", "taker_ratio": 8_980_000_800.0}],
        profile_config=profile_config,
        stage="L3",
        profile_id="profile-taker-invalid",
    )

    # Block was SKIPPED → asset is NOT rejected by it (no false negative
    # blocking trades on garbage data).
    assert rejected == []
    assert len(approved) == 1
    trace = approved[0]["evaluation_trace"]
    block = next(item for item in trace if item["type"] == "block_rule")
    assert block["status"] == "SKIPPED"
    assert block["reason"] == "indicator_invalid_value"


def test_rejection_metrics_group_by_indicator_and_block_rate():
    metrics = rejection_metrics([
        {"failed_type": "filter", "failed_indicator": "RSI"},
        {"failed_type": "filter", "failed_indicator": "RSI"},
        {"failed_type": "block_rule", "failed_indicator": "Overbought RSI"},
    ])

    assert metrics["total_rejected"] == 3
    assert metrics["filter_count"] == 2
    assert metrics["block_rule_count"] == 1
    assert metrics["block_rule_rate"] == 33.3
    assert metrics["top_indicator"] == "RSI"


def test_approved_assets_receive_normalized_analysis_snapshot():
    profile_config = {
        "filters": {
            "logic": "AND",
            "conditions": [
                {"field": "volume_24h", "operator": ">=", "value": 1_000_000},
            ],
        },
        "block_rules": {"blocks": []},
    }

    approved, rejected = evaluate_rejections(
        [{"symbol": "SOL_USDT", "volume_24h": 2_500_000}],
        profile_config=profile_config,
        stage="L1",
        profile_id="profile-approved",
    )

    assert rejected == []
    snapshot = approved[0]["analysis_snapshot"]
    assert snapshot["status"] == "approved"
    assert snapshot["details"] is not None
    assert snapshot["details"]["filters"][0]["status"] == "PASS"
    assert snapshot["details"]["evaluation_trace"][0]["status"] == "PASS"
    assert snapshot["failed_indicators"] == []
    assert snapshot["conditions"] == ["Volume 24h >= 1000000"]
    assert snapshot["current_values"]["Volume 24h"] == 2_500_000
    assert snapshot["expected_values"]["Volume 24h"] == "1000000"


def test_recompute_rejection_trace_uses_current_indicators_for_cascade_label():
    """Regression for the Rejected tab: a row stored before #71 carries an
    old-format trace where the cascade-skipped blocks lack
    `reason="cascade_short_circuit"`. On read the API must rebuild the
    trace from current indicators so the frontend renders PULADO instead
    of "Current: aguardando coleta".
    """
    profile_config = {
        "filters": {
            "logic": "AND",
            "conditions": [
                {"field": "adx", "operator": ">=", "value": 15},
            ],
        },
        "block_rules": {
            "blocks": [
                {
                    "id": "block_taker_ratio",
                    "name": "Taker Ratio",
                    "logic": "AND",
                    "conditions": [
                        {"indicator": "taker_ratio", "operator": "<", "value": 1.04},
                    ],
                },
                {
                    "id": "block_spike",
                    "name": "Spike",
                    "logic": "AND",
                    "conditions": [
                        {"indicator": "volume_spike", "operator": "<", "value": 1.2},
                    ],
                },
            ]
        },
    }

    # Old-format stored trace: no `reason` field on the SKIPPED entry.
    stored_trace = [
        {"type": "block_rule", "indicator": "Taker Ratio", "status": "FAIL"},
        {"type": "block_rule", "indicator": "Spike", "status": "SKIPPED"},
        {"type": "filter", "indicator": "ADX", "status": "SKIPPED"},
    ]

    # Current indicators: taker_ratio still failing → cascade triggers.
    indicators = {
        "taker_ratio": 0.4087,
        "volume_spike": 0.5,
        "adx": 22,
    }

    trace = recompute_rejection_trace(
        "UNI_USDT",
        profile_config=profile_config,
        indicators=indicators,
        meta={},
        stored_trace=stored_trace,
    )

    by_indicator = {item["indicator"]: item for item in trace}
    assert by_indicator["Taker Ratio"]["status"] == "FAIL"
    spike = by_indicator["Spike"]
    assert spike["status"] == "SKIPPED"
    assert spike["reason"] == "cascade_short_circuit"
    adx = by_indicator["ADX"]
    assert adx["status"] == "SKIPPED"
    assert adx["reason"] == "cascade_short_circuit"


def test_recompute_rejection_trace_falls_back_when_indicators_missing():
    """Defensive fallback: when there is no indicators row for the symbol
    (collector gap, delisted asset, fresh symbol not yet scored), the
    API must keep the stored trace verbatim instead of mass-downgrading
    every entry to SEM DADOS — that would erase the historical reason
    for the rejection.
    """
    profile_config = {
        "filters": {"logic": "AND", "conditions": [
            {"field": "adx", "operator": ">=", "value": 15},
        ]},
        "block_rules": {"blocks": []},
    }
    stored_trace = [
        {"type": "filter", "indicator": "ADX", "status": "FAIL", "current_value": 12, "expected": "15"},
    ]

    trace = recompute_rejection_trace(
        "GHOST_USDT",
        profile_config=profile_config,
        indicators=None,
        meta=None,
        stored_trace=stored_trace,
    )

    assert trace == stored_trace


def test_recompute_rejection_trace_falls_back_when_only_meta_is_present():
    """The realistic collector-gap scenario: market_metadata still has a
    fresh row for the symbol (price, volume, market_cap) but the
    indicators table has no entry yet. Indicator-based rules (rsi,
    taker_ratio, …) have no data, so recomputing would degrade the
    trace; the helper must fall back to the stored snapshot.
    """
    profile_config = {
        "filters": {"logic": "AND", "conditions": [
            {"field": "rsi", "operator": "<", "value": 55},
        ]},
        "block_rules": {
            "blocks": [
                {
                    "id": "block_taker",
                    "name": "Taker Ratio",
                    "logic": "AND",
                    "conditions": [
                        {"indicator": "taker_ratio", "operator": "<", "value": 1.04},
                    ],
                }
            ]
        },
    }
    stored_trace = [
        {"type": "block_rule", "indicator": "Taker Ratio", "status": "FAIL", "current_value": 0.4, "expected": "1.04"},
        {"type": "filter", "indicator": "RSI", "status": "SKIPPED", "reason": "cascade_short_circuit"},
    ]

    # market_metadata-only payload: enough for volume/market_cap rules,
    # but NOT enough for taker_ratio / rsi.
    meta = {
        "current_price": 1.23,
        "price_change_24h": -2.4,
        "volume_24h": 5_000_000.0,
        "market_cap": 1_000_000_000.0,
        "spread_pct": 0.05,
        "orderbook_depth_usdt": 200_000.0,
    }

    trace = recompute_rejection_trace(
        "FRESHLISTED_USDT",
        profile_config=profile_config,
        indicators=None,
        meta=meta,
        stored_trace=stored_trace,
    )

    assert trace == stored_trace


def test_recompute_rejection_trace_falls_back_when_profile_config_missing():
    """Without a profile_config there is nothing to evaluate against, so
    the helper must return the stored trace untouched.
    """
    stored_trace = [
        {"type": "filter", "indicator": "RSI", "status": "FAIL"},
    ]
    trace = recompute_rejection_trace(
        "BTC_USDT",
        profile_config=None,
        indicators={"rsi": 80},
        meta={"current_price": 50000.0},
        stored_trace=stored_trace,
    )
    assert trace == stored_trace


def test_rejected_assets_expose_normalized_details_contract():
    profile_config = {
        "filters": {
            "logic": "AND",
            "conditions": [
                {"field": "rsi", "operator": "<", "value": 55},
            ],
        },
        "block_rules": {"blocks": []},
    }

    approved, rejected = evaluate_rejections(
        [{"symbol": "XRP_USDT", "rsi": 62}],
        profile_config=profile_config,
        stage="L2",
        profile_id="profile-rejected",
    )

    assert approved == []
    item = rejected[0]
    assert item["status"] == "rejected"
    assert item["details"] is not None
    assert item["details"]["filters"][0]["status"] == "FAIL"
    assert item["details"]["conditions"] == ["RSI < 55"]
    assert item["failed_indicators"] == ["RSI"]
    assert item["current_values"]["RSI"] == 62
    assert item["expected_values"]["RSI"] == "55"
