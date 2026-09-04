import math

import numpy as np
import pandas as pd
import pytest

from app.services.feature_engine import FeatureEngine
from app.services.profile_engine import RuleEngine
from app.services.price_position import BREAKOUT_REFERENCE_INDICATORS


DIRECT_INDICATORS = {
    "adx_acceleration",
    "adx_slope_3",
    "macd_hist_slope_3",
    "macd_hist_slope_5",
    "rsi_slope_3",
    "entry_exhaustion_score",
    "rsi_6",
}


def _closed_candles(count: int = 120) -> pd.DataFrame:
    close = np.array([
        100 + index * 0.08 + math.sin(index / 4) * 1.8 + math.sin(index / 11)
        for index in range(count)
    ], dtype=float)
    return pd.DataFrame({
        "time": pd.date_range("2026-01-01", periods=count, freq="5min", tz="UTC"),
        "open": close - np.linspace(0.12, 0.25, count),
        "high": close + np.linspace(0.55, 1.05, count),
        "low": close - np.linspace(0.45, 0.95, count),
        "close": close,
        "volume": np.linspace(100, 260, count) * (1 + np.sin(np.arange(count) / 7) * 0.15),
    })


def test_seven_direct_producers_reach_the_condition_evaluator_from_closed_candles():
    engine = FeatureEngine({
        "rsi": {"enabled": True, "period": 14, "periods": [6]},
        "adx": {"enabled": True, "period": 14},
        "macd": {"enabled": True, "fast": 12, "slow": 26, "signal": 9},
        "atr": {"enabled": True, "period": 14},
        "entry_exhaustion": {"enabled": True},
    })

    indicators = engine.calculate(_closed_candles(), timeframe="5m")

    assert DIRECT_INDICATORS <= indicators.keys()
    assert all(indicators[indicator] is not None for indicator in DIRECT_INDICATORS)
    evaluator = RuleEngine()
    for indicator in DIRECT_INDICATORS:
        actual = indicators[indicator]
        status, detail = evaluator.evaluate_condition_status(
            {"field": indicator, "operator": "==", "value": actual},
            indicators,
        )
        assert status.value == "PASS", (indicator, detail)
        assert detail["actual"] == actual


@pytest.mark.parametrize("reference_window", ["5m", "15m", "30m", "1h"])
def test_breakout_virtual_indicator_resolves_every_admitted_window(reference_window):
    resolved = BREAKOUT_REFERENCE_INDICATORS[reference_window]
    status, detail = RuleEngine().evaluate_condition_status(
        {
            "field": "breakout_distance_pct",
            "reference_window": reference_window,
            "operator": "==",
            "value": 0.25,
        },
        {resolved: 0.25},
    )

    assert status.value == "PASS"
    assert detail["resolved_indicator"] == resolved
    assert detail["reference_window"] == reference_window
