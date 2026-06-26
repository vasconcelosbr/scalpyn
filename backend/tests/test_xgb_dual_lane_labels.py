from datetime import datetime, timezone

import numpy as np
import pandas as pd

from backend.scripts.run_xgb_dual_lane_labels import (
    build_xgb_l1_spectrum_dataset,
    build_xgb_l3_profile_dataset,
    classify_profile_threshold,
    derive_labels,
    select_operational_threshold,
    temporal_split,
    _json_safe_raw_model_output,
)


def _record(source="L1_SPECTRUM", profile_id=None, pnl=1.2, mfe=1.4, outcome="TP_HIT"):
    return {
        "shadow_id": "s1",
        "symbol": "BTC_USDT",
        "source": source,
        "pnl_pct": pnl,
        "net_return_pct": pnl,
        "holding_seconds": 900,
        "outcome": outcome,
        "features_snapshot": {
            "rsi": 55,
            "adx": 25,
            "volume_delta": 100,
            "taker_ratio": 0.6,
            "volume_24h_usdt": 1000000,
            "orderbook_depth_usdt": 50000,
            "spread_pct": 0.01,
            "volume_spike": 1.2,
            "close": 100,
            "ema9": 101,
            "ema21": 100,
            "ema50": 98,
            "ema200": 90,
        },
        "created_at": datetime(2026, 6, 1, tzinfo=timezone.utc),
        "profile_id": profile_id,
        "max_profit_first_30m": mfe,
        "mae_pct": -0.3,
        "sl_pct_applied": -1.0,
        "barrier_touched": "TP" if outcome == "TP_HIT" else "SL",
    }


def test_derive_l1_and_l3_labels_from_economic_fields():
    labels = derive_labels(_record())
    assert labels["l1_mfe_30m_gte_1pct"] == 1
    assert labels["l1_hit_tp_before_sl"] == 1
    assert labels["l3_profile_ev_positive"] == 1
    assert labels["l3_profile_hit_tp_before_sl"] == 1
    assert labels["l3_profile_mae_controlled"] == 1


def test_l1_builder_excludes_profile_features():
    bundle, _audit = build_xgb_l1_spectrum_dataset([_record(profile_id="p1") for _ in range(40)])
    assert bundle.contract_id == "XGB_L1_SPECTRUM_V1"
    assert bundle.train_sources == ["L1_SPECTRUM"]
    assert bundle.label_name == "l1_mfe_30m_gte_1pct"
    assert "profile_id_encoded" not in bundle.feature_columns


def test_l3_builder_requires_profile_and_adds_prior_features():
    records = []
    for idx in range(40):
        row = _record(source="L3", profile_id="p1", pnl=1.0 if idx % 2 == 0 else -1.0)
        row["shadow_id"] = f"s{idx}"
        row["created_at"] = datetime(2026, 6, 1, 0, idx, tzinfo=timezone.utc)
        records.append(row)
    records.append(_record(source="L3", profile_id=None))
    bundle, _audit = build_xgb_l3_profile_dataset(records)
    assert bundle.contract_id == "XGB_L3_PROFILE_V1"
    assert bundle.excluded_count == 1
    assert "profile_id_encoded" in bundle.df.columns
    assert "profile_trade_count_prior" in bundle.df.columns


def test_temporal_split_is_60_20_20():
    df = pd.DataFrame({"_created_at": pd.date_range("2026-01-01", periods=100), "y": np.arange(100)})
    train, val, test = temporal_split(df)
    assert len(train) == 60
    assert len(val) == 20
    assert len(test) == 20
    assert train["_created_at"].max() < val["_created_at"].min()
    assert val["_created_at"].max() < test["_created_at"].min()


def _make_sweep(threshold, approved_count, precision, recall, fpr, ev):
    """Helper to build a minimal threshold sweep row."""
    tp = int(approved_count * precision)
    return {
        "threshold": threshold,
        "approved_count": approved_count,
        "precision": precision,
        "recall": recall,
        "fpr": fpr,
        "ev": ev,
        "tp": tp,
        "fp": approved_count - tp,
        "tn": 0,
        "fn": 0,
        "avg_pnl": ev,
        "lift_vs_baseline": None,
    }


# --- Phase C: select_operational_threshold ---

def test_select_operational_threshold_rejects_extreme_threshold_low_n():
    # threshold 0.95 with only 1 approved → must be rejected
    sweep = [
        _make_sweep(0.95, approved_count=1, precision=1.0, recall=0.01, fpr=0.00, ev=2.0),
        _make_sweep(0.50, approved_count=6, precision=0.67, recall=0.10, fpr=0.05, ev=0.8),
    ]
    threshold, status = select_operational_threshold(sweep, "XGB_L1_SPECTRUM", baseline_precision=0.40)
    assert threshold is None
    assert status == "NO_VALID_OPERATING_POINT"


def test_select_operational_threshold_picks_valid_candidate():
    sweep = [
        _make_sweep(0.95, approved_count=1, precision=1.0, recall=0.01, fpr=0.00, ev=2.0),
        _make_sweep(0.30, approved_count=219, precision=0.55, recall=0.60, fpr=0.15, ev=1.2),
    ]
    threshold, status = select_operational_threshold(sweep, "XGB_L1_SPECTRUM", baseline_precision=0.40)
    assert threshold == 0.30
    assert status == "OPERATIONAL"


def test_select_operational_threshold_no_valid_point_when_all_fpr_high():
    sweep = [
        _make_sweep(0.10, approved_count=200, precision=0.45, recall=0.90, fpr=0.35, ev=0.5),
        _make_sweep(0.20, approved_count=150, precision=0.48, recall=0.70, fpr=0.30, ev=0.4),
    ]
    threshold, status = select_operational_threshold(sweep, "XGB_L1_SPECTRUM", baseline_precision=0.40)
    assert threshold is None
    assert status == "NO_VALID_OPERATING_POINT"


def test_select_operational_threshold_no_valid_point_zero_ev():
    sweep = [
        _make_sweep(0.30, approved_count=50, precision=0.55, recall=0.50, fpr=0.10, ev=0.0),
        _make_sweep(0.50, approved_count=30, precision=0.60, recall=0.30, fpr=0.08, ev=-0.1),
    ]
    threshold, status = select_operational_threshold(sweep, "XGB_L1_SPECTRUM", baseline_precision=0.40)
    assert threshold is None
    assert status == "NO_VALID_OPERATING_POINT"


# --- Phase E: classify_profile_threshold ---

def test_classify_profile_cold_start_total_below_100():
    # completed_trades_total < 100 → cold_start regardless of test-set size
    status, reason = classify_profile_threshold(
        completed_trades_total=14, positive_count=5, approved_count=10,
        precision_test=0.60, fpr_test=0.10, ev_test=1.0,
    )
    assert status == "cold_start"
    assert "completed_trades_total" in reason


def test_classify_profile_cold_start_insufficient_positives():
    status, reason = classify_profile_threshold(
        completed_trades_total=101, positive_count=10, approved_count=30,
        precision_test=0.60, fpr_test=0.10, ev_test=1.0,
    )
    assert status == "cold_start"
    assert "positive_count" in reason


def test_classify_profile_rejected_high_fpr():
    status, reason = classify_profile_threshold(
        completed_trades_total=150, positive_count=40, approved_count=35,
        precision_test=0.60, fpr_test=0.714, ev_test=0.5,
    )
    assert status == "rejected"
    assert "fpr" in reason


def test_classify_profile_rejected_negative_ev():
    status, reason = classify_profile_threshold(
        completed_trades_total=150, positive_count=40, approved_count=35,
        precision_test=0.60, fpr_test=0.10, ev_test=-0.1,
    )
    assert status == "rejected"
    assert "ev" in reason


def test_classify_profile_rejected_low_precision():
    status, reason = classify_profile_threshold(
        completed_trades_total=150, positive_count=40, approved_count=35,
        precision_test=0.40, fpr_test=0.10, ev_test=0.5,
    )
    assert status == "rejected"
    assert "precision" in reason


def test_classify_profile_approved_candidate():
    status, reason = classify_profile_threshold(
        completed_trades_total=150, positive_count=40, approved_count=35,
        precision_test=0.55, fpr_test=0.10, ev_test=0.8,
    )
    assert status == "approved_candidate"
    assert reason == "passes_all_criteria"


def test_classify_profile_ml_eligible_513_completed():
    # 513 completed → PROFILE_THRESHOLD_ELIGIBLE (>=500); passes maturity gate
    status, reason = classify_profile_threshold(
        completed_trades_total=513, positive_count=35, approved_count=31,
        precision_test=0.55, fpr_test=0.10, ev_test=0.5,
    )
    assert status == "approved_candidate"


def test_classify_profile_ml_eligible_301_completed():
    # 301 completed → ML_ELIGIBLE (>=100); passes maturity gate
    status, reason = classify_profile_threshold(
        completed_trades_total=301, positive_count=35, approved_count=31,
        precision_test=0.55, fpr_test=0.10, ev_test=0.5,
    )
    assert status == "approved_candidate"


def test_classify_profile_500_completed_approved_lt_30_is_insufficient_not_cold_start():
    # completed_trades_total >= 500 but approved_count < 30 → insufficient_operating_sample, NOT cold_start
    status, reason = classify_profile_threshold(
        completed_trades_total=500, positive_count=35, approved_count=5,
        precision_test=0.55, fpr_test=0.10, ev_test=0.5,
    )
    assert status == "insufficient_operating_sample"
    assert status != "cold_start"


# --- Phase F: _json_safe_raw_model_output ---

def test_json_safe_raw_model_output_normal_float():
    value, repr_ = _json_safe_raw_model_output(1.5)
    assert value == 1.5
    assert repr_ is None


def test_json_safe_raw_model_output_nan():
    value, repr_ = _json_safe_raw_model_output(float("nan"))
    assert value is None
    assert repr_ == "nan"


def test_json_safe_raw_model_output_inf():
    value, repr_ = _json_safe_raw_model_output(float("inf"))
    assert value is None
    assert repr_ == "inf"


def test_json_safe_raw_model_output_neg_inf():
    value, repr_ = _json_safe_raw_model_output(float("-inf"))
    assert value is None
    assert repr_ == "-inf"


def test_json_safe_raw_model_output_none():
    value, repr_ = _json_safe_raw_model_output(None)
    assert value is None
    assert repr_ is None

