import pandas as pd

from app.services.feature_engine import FeatureEngine


def _frame(base: float, half_range: float, rows: int = 20) -> pd.DataFrame:
    close = [base + ((idx % 3) - 1) * half_range / 4 for idx in range(rows)]
    return pd.DataFrame(
        {
            "open": close,
            "high": [value + half_range for value in close],
            "low": [value - half_range for value in close],
            "close": close,
            "volume": [1.0] * rows,
        }
    )


def test_micro_price_positive_atr_is_not_rounded_to_zero():
    engine = FeatureEngine({"atr": {"enabled": True, "period": 14}})

    result = engine.calculate(_frame(0.00000289, 0.000000002))

    assert 0.0 < result["atr"] < 0.00000001
    assert result["atr_pct"] > 0.0
    assert result["atr_percent"] == result["atr_pct"]


def test_representable_atr_keeps_existing_eight_decimal_rounding():
    engine = FeatureEngine({"atr": {"enabled": True, "period": 14}})
    frame = _frame(100.0, 0.123456789)
    high, low, close = frame["high"], frame["low"], frame["close"]
    true_range = pd.concat(
        [high - low, (high - close.shift()).abs(), (low - close.shift()).abs()],
        axis=1,
    ).max(axis=1)
    expected = round(float(true_range.rolling(window=14).mean().iloc[-1]), 8)

    result = engine._calc_atr(frame)

    assert result["atr"] == expected
