from datetime import datetime, timezone

from app.ml.l1_feature_contract import (
    evaluate_l1_snapshot,
    load_l1_feature_contract,
)
from app.ml.native_capture_governance import classify_l1_lane_row


REQUIRED = [
    "taker_ratio", "volume_delta", "rsi", "macd_histogram_pct",
    "macd_histogram_slope", "adx", "adx_acceleration", "spread_pct",
    "volume_spike", "bb_width", "atr_pct", "ema9_gt_ema21",
    "orderbook_depth_usdt", "vwap_distance_pct", "rsi_slope_3",
    "rsi_slope_5", "macd_hist_slope_3", "macd_hist_slope_5",
    "ema21_ema50_distance_pct", "di_plus_minus_diff", "adx_slope_3",
    "vwap_reclaim_bool", "higher_highs_5", "higher_lows_5",
]
OPTIONAL = [
    "volume_24h_usdt", "flow_strength", "momentum_strength",
    "delta_normalized", "ema_distance_pct", "ema50_distance_pct",
    "ema200_distance_pct",
]
EXCLUDED = [
    "liquidity_score", "market_structure_score", "momentum_score",
    "signal_score", "di_trend", "trend_alignment", "ema50_gt_ema200",
]


def ml_config():
    return {
        "ml_l1_feature_contract_version": "l1_spectrum_entry_v2",
        "ml_l1_feature_exclusions": EXCLUDED,
        "ml_feature_contract": {
            "L1_SPECTRUM": {
                "version": "l1_spectrum_entry_v2",
                "required": REQUIRED,
                "optional": OPTIONAL,
                "min_row_coverage": 0.7,
            },
            # Sentinel proves the L1 loader ignores the adjacent L3 contract.
            "L3_PROFILE": {
                "required": ["l3_sentinel"],
                "optional": [],
                "min_row_coverage": 1.0,
            },
        },
        "ml_feature_ranges": {
            "atr_pct": {"gt": 0},
            "rsi": {"gte": 0, "lte": 100},
            "spread_pct": {"gte": 0},
            "l3_sentinel": {"gt": 999},
        },
    }


def full_snapshot():
    snapshot = {name: 1.0 for name in REQUIRED + OPTIONAL}
    snapshot["rsi"] = 50.0
    return snapshot


def test_contract_has_exact_deterministic_l1_feature_set():
    contract = load_l1_feature_contract(ml_config())
    assert contract.version == "l1_spectrum_entry_v2"
    assert len(contract.feature_names) == 31
    assert contract.feature_names == tuple(REQUIRED + OPTIONAL)
    assert not set(contract.feature_names) & set(EXCLUDED)
    assert "l3_sentinel" not in contract.feature_names


def test_full_snapshot_is_lane_eligible():
    result = evaluate_l1_snapshot(full_snapshot(), ml_config())
    assert result.eligible is True
    assert result.coverage == 1.0
    assert result.reasons == ()


def test_missing_required_and_invalid_range_fail_closed():
    snapshot = full_snapshot()
    snapshot["volume_delta"] = None
    snapshot["atr_pct"] = 0.0
    result = evaluate_l1_snapshot(snapshot, ml_config())
    assert result.eligible is False
    assert "missing_required:volume_delta" in result.reasons
    assert "range_gt:atr_pct" in result.reasons


def test_stale_required_feature_is_preserved_but_not_lane_eligible():
    metadata = {
        "taker_ratio": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "stale": True,
        }
    }
    result = evaluate_l1_snapshot(
        full_snapshot(), ml_config(), feature_metadata=metadata
    )
    assert result.eligible is False
    assert result.stale_required_features == ("taker_ratio",)
    assert "stale_required:taker_ratio" in result.reasons


def test_missing_contract_is_fail_closed_without_affecting_native_capture():
    result = evaluate_l1_snapshot(full_snapshot(), {})
    assert result.eligible is False
    assert result.contract_version == "UNCONFIGURED"
    assert result.reasons == ("missing_l1_feature_contract_version",)


def test_persisted_l1_lane_classification_is_separate_from_native():
    row = {
        "source": "L1_SPECTRUM",
        "eligible_for_training": True,
        "config_snapshot": {
            "l1_native_eligible": True,
            "l1_lane_eligible": False,
            "l1_feature_contract_version": "l1_spectrum_entry_v2",
            "l1_lane_eligibility_reasons": ["missing_required:volume_delta"],
        },
    }
    bucket, reasons = classify_l1_lane_row(row)
    assert bucket == "lane_ineligible"
    assert reasons == ["missing_required:volume_delta"]


def test_l3_rows_are_outside_l1_lane_classification():
    assert classify_l1_lane_row(
        {
            "source": "L3",
            "eligible_for_training": True,
            "config_snapshot": {},
        }
    ) == ("not_l1", [])
