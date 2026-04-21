import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.block_engine import BlockEngine
from app.services.feature_engine import FeatureEngine
from app.services.profile_engine import ProfileEngine
from app.services.signal_engine import SignalEngine


def _sample_ohlcv(rows: int = 250) -> pd.DataFrame:
    base = list(range(1, rows + 1))
    return pd.DataFrame(
        {
            "open": base,
            "high": [value + 1 for value in base],
            "low": [max(value - 1, 1) for value in base],
            "close": base,
            "volume": [1000 + value for value in base],
        }
    )


def test_feature_engine_emits_price_and_requested_ema_values():
    engine = FeatureEngine(
        {
            "rsi": {"enabled": False},
            "adx": {"enabled": False},
            "ema": {"enabled": True, "periods": [5, 9, 21, 50, 200]},
            "atr": {"enabled": True, "period": 14},
            "macd": {"enabled": False},
            "vwap": {"enabled": False},
            "stochastic": {"enabled": False},
            "obv": {"enabled": False},
            "bollinger": {"enabled": False},
            "parabolic_sar": {"enabled": False},
            "zscore": {"enabled": False},
            "volume_delta": {"enabled": False},
        }
    )

    indicators = engine.calculate(_sample_ohlcv())

    assert indicators["price"] == indicators["close"] == 250.0
    assert {"ema5", "ema9", "ema21", "ema50", "ema200"} <= set(indicators)
    assert "ema9_gt_ema21" in indicators
    assert indicators["atr_percent"] == indicators["atr_pct"]


def test_block_engine_supports_comparison_groups():
    engine = BlockEngine(
        {
            "blocks": [
                {
                    "id": "block_downtrend",
                    "name": "Downtrend",
                    "enabled": True,
                    "logic": "AND",
                    "conditions": [
                        {"id": "cmp1", "type": "comparison", "left": "price", "operator": "<", "right": "ema9"},
                        {"id": "cmp2", "type": "comparison", "left": "ema9", "operator": "<", "right": "ema21"},
                    ],
                }
            ]
        }
    )

    result = engine.evaluate({"price": 90, "ema9": 100, "ema21": 110})

    assert result["blocked"] is True
    assert result["triggered_blocks"] == ["Downtrend"]


def test_block_engine_keeps_legacy_threshold_behavior_for_existing_rules():
    engine = BlockEngine(
        {
            "blocks": [
                {
                    "id": "legacy_spread",
                    "name": "Spread too high",
                    "enabled": True,
                    "indicator": "spread_pct",
                    "type": "threshold",
                    "operator": "<",
                    "value": 0.3,
                }
            ]
        }
    )

    result = engine.evaluate({"spread_pct": 0.5})

    assert result["blocked"] is True
    assert result["triggered_blocks"] == ["Spread too high"]


def test_signal_engine_supports_comparison_conditions_with_and_logic():
    engine = SignalEngine(
        {
            "logic": "AND",
            "conditions": [
                {"id": "cmp1", "type": "comparison", "left": "price", "operator": ">", "right": "ema9"},
                {"id": "cmp2", "type": "comparison", "left": "ema9", "operator": ">", "right": "ema21"},
            ],
        }
    )

    passing = engine.evaluate({"price": 105, "ema9": 100, "ema21": 90}, alpha_score=0)
    failing = engine.evaluate({"price": 95, "ema9": 100, "ema21": 90}, alpha_score=0)

    assert passing["signal"] is True
    assert passing["matched"] == ["cmp1", "cmp2"]
    assert failing["signal"] is False
    assert failing["matched"] == ["cmp2"]


def test_profile_engine_applies_entry_trigger_comparisons():
    engine = ProfileEngine(
        {
            "entry_triggers": {
                "logic": "AND",
                "conditions": [
                    {"id": "entry_cmp", "type": "comparison", "left": "price", "operator": ">", "right": "ema9"},
                ],
            }
        }
    )

    blocked_entry = engine.evaluate_asset(
        {
            "symbol": "BTC_USDT",
            "price": 95,
            "indicators": {"price": 95, "ema9": 100},
        }
    )
    allowed_entry = engine.evaluate_asset(
        {
            "symbol": "BTC_USDT",
            "price": 105,
            "indicators": {"price": 105, "ema9": 100},
        }
    )

    assert blocked_entry["entry"]["allowed"] is False
    assert allowed_entry["entry"]["allowed"] is True
