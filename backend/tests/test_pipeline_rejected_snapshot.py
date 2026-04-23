import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.pipeline_rejections import evaluate_rejections, rejection_metrics


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
