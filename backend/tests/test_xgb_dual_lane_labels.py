from datetime import datetime, timezone

import numpy as np
import pandas as pd

from backend.scripts.run_xgb_dual_lane_labels import (
    build_xgb_l1_spectrum_dataset,
    build_xgb_l3_profile_dataset,
    derive_labels,
    temporal_split,
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

