import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.ml.feature_extractor import (
    FEATURE_ALIASES,
    FEATURE_COLUMNS,
    extract_features,
    feature_columns_hash,
)
from backend.app.services.feature_engine import FeatureEngine


def _engine() -> FeatureEngine:
    return FeatureEngine(
        {
            "rsi": {"enabled": True, "period": 14},
            "adx": {"enabled": True, "period": 14},
            "ema": {"enabled": True, "periods": [9, 21, 50, 200]},
            "atr": {"enabled": True, "period": 14},
            "macd": {"enabled": True, "fast": 12, "slow": 26, "signal": 9},
            "vwap": {"enabled": True},
            "stochastic": {"enabled": False},
            "obv": {"enabled": False},
            "bollinger": {"enabled": False},
            "parabolic_sar": {"enabled": False},
            "zscore": {"enabled": False},
            "volume_delta": {"enabled": False},
            "volume_metrics": {"enabled": False},
            "volume_spike": {"enabled": False},
            "taker_ratio": {"enabled": False},
            "entry_exhaustion": {"enabled": False},
        }
    )


def test_feature_columns_hash_is_order_sensitive():
    base = ["rsi", "adx", "vwap_distance_pct"]
    reordered = ["adx", "rsi", "vwap_distance_pct"]

    assert feature_columns_hash(base) == feature_columns_hash(list(base))
    assert feature_columns_hash(base) != feature_columns_hash(reordered)


def test_feature_aliases_are_documented_without_duplicate_columns():
    assert FEATURE_ALIASES["ema9_ema21_distance_pct"] == "ema_distance_pct"
    assert FEATURE_ALIASES["price_vs_vwap_pct"] == "vwap_distance_pct"
    assert FEATURE_ALIASES["volume_spike_ratio"] == "volume_spike"

    assert "ema9_ema21_distance_pct" not in FEATURE_COLUMNS
    assert "price_vs_vwap_pct" not in FEATURE_COLUMNS
    assert "volume_spike_ratio" not in FEATURE_COLUMNS


def test_extract_features_derives_scalar_directional_features():
    features = extract_features(
        {
            "ema9": 105.0,
            "ema21": 110.0,
            "ema50": 100.0,
            "di_plus": 31.5,
            "di_minus": 18.25,
        }
    )

    assert features["ema21_ema50_distance_pct"] == pytest.approx(10.0)
    assert features["di_plus_minus_diff"] == pytest.approx(13.25)


def test_directional_features_return_none_with_insufficient_history():
    df = pd.DataFrame(
        {
            "open": [10.0, 10.2, 10.4, 10.6],
            "high": [10.3, 10.5, 10.7, 10.9],
            "low": [9.8, 10.0, 10.2, 10.4],
            "close": [10.1, 10.3, 10.5, 10.7],
            "volume": [100.0, 100.0, 100.0, 100.0],
        }
    )

    result = _engine()._calc_directional_features(df)

    assert result["rsi_slope_3"] is None
    assert result["macd_hist_slope_3"] is None
    assert result["ema21_ema50_distance_pct"] is None
    assert result["higher_highs_5"] is None
    assert result["higher_lows_5"] is None


def test_directional_features_from_closed_candles():
    n = 80
    close = np.array([100 + i * 0.15 + math.sin(i / 3) for i in range(n)], dtype=float)
    close[-5:] = np.array([112.0, 112.4, 112.8, 113.2, 113.6])
    df = pd.DataFrame(
        {
            "open": close - 0.1,
            "high": close + np.linspace(0.6, 1.0, n),
            "low": close - np.linspace(0.6, 1.0, n),
            "close": close,
            "volume": np.linspace(100.0, 180.0, n),
        }
    )

    result = _engine()._calc_directional_features(df)

    assert result["rsi_slope_3"] is not None
    assert result["rsi_slope_5"] is not None
    assert result["macd_hist_slope_3"] is not None
    assert result["macd_hist_slope_5"] is not None
    assert result["ema21_ema50_distance_pct"] is not None
    assert result["di_plus_minus_diff"] is not None
    assert result["adx_slope_3"] is not None
    assert result["higher_highs_5"] is True
    assert result["higher_lows_5"] is True


def test_vwap_reclaim_bool_uses_previous_closed_candle():
    df = pd.DataFrame(
        {
            "open": [10.0, 10.0, 10.0, 10.0, 9.0, 12.0],
            "high": [10.2, 10.2, 10.2, 10.2, 9.2, 12.2],
            "low": [9.8, 9.8, 9.8, 9.8, 8.8, 11.8],
            "close": [10.0, 10.0, 10.0, 10.0, 9.0, 12.0],
            "volume": [100.0, 100.0, 100.0, 100.0, 100.0, 100.0],
        }
    )

    result = _engine()._calc_directional_features(df)

    assert result["vwap_reclaim_bool"] is True
