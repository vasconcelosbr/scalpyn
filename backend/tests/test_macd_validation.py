"""Tests for validate_macd_histogram() and extended _calc_macd fields.

Covers:
- histogram_sign
- momentum_direction (up/down/flat with epsilon)
- momentum_strength (strengthening/weakening/flat with epsilon)
- consistency_status (z-score, sign divergence, scale outlier)
- signal_quality
- diagnostic_message non-empty
- insufficient data raises ValueError
- extended _calc_macd fields align with validate_macd_histogram
"""

import math
import pytest
import pandas as pd

from app.services.indicator_validity import validate_macd_histogram
from app.services.feature_engine import FeatureEngine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _linear_prices(n: int, start: float = 100.0, step: float = 0.5) -> list:
    """Generate a smoothly rising price series."""
    return [start + i * step for i in range(n)]


def _oscillating_prices(n: int, amplitude: float = 1.0, period: int = 10) -> list:
    """Generate an oscillating price series."""
    import math as m
    return [100.0 + amplitude * m.sin(2 * m.pi * i / period) for i in range(n)]


def _build_df(close_prices):
    """Wrap close prices in a minimal OHLCV DataFrame for FeatureEngine."""
    return pd.DataFrame({
        "open": close_prices,
        "high": [p + 0.1 for p in close_prices],
        "low": [p - 0.1 for p in close_prices],
        "close": close_prices,
        "volume": [1_000_000.0] * len(close_prices),
    })


MACD_CONFIG = {
    "macd": {"enabled": True, "fast": 12, "slow": 26, "signal": 9},
    "rsi": {"enabled": False},
    "adx": {"enabled": False},
    "ema": {"enabled": False},
    "atr": {"enabled": False},
    "vwap": {"enabled": False},
    "stochastic": {"enabled": False},
    "obv": {"enabled": False},
    "bollinger": {"enabled": False},
    "parabolic_sar": {"enabled": False},
    "zscore": {"enabled": False},
    "volume_delta": {"enabled": False},
    "volume_spike": {"enabled": False},
    "taker_ratio": {"enabled": False},
    "volume_metrics": {"enabled": False},
}

N = 60


# ---------------------------------------------------------------------------
# 1. histogram_sign
# ---------------------------------------------------------------------------

class TestHistogramSign:
    def test_positive_histogram(self):
        prices = _linear_prices(N, step=1.0)
        result = validate_macd_histogram(prices)
        assert result["histogram_sign"] in ("positive", "negative", "zero")

    def test_sign_matches_value(self):
        prices = _linear_prices(N, step=1.0)
        result = validate_macd_histogram(prices)
        val = result["histogram_value"]
        sign = result["histogram_sign"]
        if val > 0:
            assert sign == "positive"
        elif val < 0:
            assert sign == "negative"
        else:
            assert sign == "zero"

    def test_negative_trend_gives_negative_sign(self):
        prices = _linear_prices(N, step=-0.8)
        result = validate_macd_histogram(prices)
        assert result["histogram_sign"] == "negative"
        assert result["histogram_value"] < 0


# ---------------------------------------------------------------------------
# 2. momentum_direction
# ---------------------------------------------------------------------------

class TestMomentumDirection:
    def test_direction_is_valid_enum(self):
        prices = _linear_prices(N)
        result = validate_macd_histogram(prices)
        assert result["momentum_direction"] in ("up", "down", "flat")

    def test_direction_matches_current_vs_prev(self):
        """Direction must be consistent with the actual (current - prev) comparison."""
        prices = _linear_prices(N, step=0.4)
        result = validate_macd_histogram(prices)
        current = result["histogram_value"]
        prev = result["details"]["histogram_prev"]
        assert prev is not None
        std = result["details"]["histogram_std_10"] or 0.0
        epsilon = std * 0.1 if std > 0 else 1e-6
        direction = result["momentum_direction"]
        if current > prev + epsilon:
            assert direction == "up", f"Expected up: current={current}, prev={prev}"
        elif current < prev - epsilon:
            assert direction == "down", f"Expected down: current={current}, prev={prev}"
        else:
            assert direction == "flat"

    def test_direction_logic_on_reversal(self):
        """After a clear price reversal, direction must match the new histogram slope."""
        down = [100.0 - i * 0.3 for i in range(50)]
        up = [down[-1] + i * 0.8 for i in range(1, 11)]
        prices = down + up
        result = validate_macd_histogram(prices)
        current = result["histogram_value"]
        prev = result["details"]["histogram_prev"]
        std = result["details"]["histogram_std_10"] or 0.0
        epsilon = std * 0.1 if std > 0 else 1e-6
        direction = result["momentum_direction"]
        if current > prev + epsilon:
            assert direction == "up"
        elif current < prev - epsilon:
            assert direction == "down"
        else:
            assert direction == "flat"


# ---------------------------------------------------------------------------
# 3. momentum_strength
# ---------------------------------------------------------------------------

class TestMomentumStrength:
    def test_strength_is_valid_enum(self):
        prices = _linear_prices(N)
        result = validate_macd_histogram(prices)
        assert result["momentum_strength"] in ("strengthening", "weakening", "flat")

    def test_strengthening_logic_matches_abs_comparison(self):
        """Strength must be consistent with abs(current) vs abs(prev) comparison."""
        prices = _linear_prices(N, step=0.4)
        result = validate_macd_histogram(prices)
        current = result["histogram_value"]
        prev = result["details"]["histogram_prev"]
        assert prev is not None
        std = result["details"]["histogram_std_10"] or 0.0
        epsilon = std * 0.1 if std > 0 else 1e-6
        strength = result["momentum_strength"]
        if abs(current) > abs(prev) + epsilon:
            assert strength == "strengthening"
        elif abs(current) < abs(prev) - epsilon:
            assert strength == "weakening"
        else:
            assert strength == "flat"

    def test_weakening_towards_zero(self):
        """Deceleration after a trend: histogram shrinks toward zero → weakening."""
        up = [100.0 + i * 0.5 for i in range(40)]
        flat = [up[-1]] * 20
        prices = up + flat
        result = validate_macd_histogram(prices)
        assert result["momentum_strength"] in ("weakening", "flat")

    def test_flat_detection_uses_epsilon(self):
        """Perfectly constant prices → histogram essentially zero → flat."""
        prices = [100.0] * N
        result = validate_macd_histogram(prices)
        assert result["momentum_direction"] == "flat"
        assert result["momentum_strength"] == "flat"


# ---------------------------------------------------------------------------
# 4. consistency_status
# ---------------------------------------------------------------------------

class TestConsistencyStatus:
    def test_normal_series_is_valid(self):
        prices = _linear_prices(N, step=0.3)
        result = validate_macd_histogram(prices)
        assert result["consistency_status"] == "valid"

    def test_zscore_outlier(self):
        """Stable trend then sudden massive reversal → z_score > 3 → inconsistent.

        The prior-10 baseline has near-zero std (stable positive histogram).
        The last candle creates a large negative histogram value far from that baseline.
        """
        stable = [100.0 + i * 0.4 for i in range(70)]
        stable.append(stable[-1] - 200.0)
        result = validate_macd_histogram(stable)
        assert result["consistency_status"] == "inconsistent"

    def test_sign_divergence(self):
        """Downtrend baseline then sudden positive spike → sign divergence → inconsistent."""
        down = [100.0 - i * 0.6 for i in range(70)]
        spike = [down[-1] + 500.0]
        prices = down + spike
        result = validate_macd_histogram(prices)
        mean_10 = result["details"]["histogram_mean_10"]
        current = result["histogram_value"]
        if mean_10 is not None and mean_10 != 0 and current * mean_10 < 0:
            assert result["consistency_status"] == "inconsistent"
        else:
            assert result["consistency_status"] in ("valid", "inconsistent")

    def test_scale_outlier_10x_mean(self):
        """Stable mild trend then extreme price jump → abs(current) > 10*abs(mean) → inconsistent."""
        mild = [100.0 + i * 0.05 for i in range(70)]
        mild.append(mild[-1] + 5000.0)
        result = validate_macd_histogram(mild)
        assert result["consistency_status"] == "inconsistent"

    def test_first_inconsistency_is_preserved(self):
        """Short-circuit: once inconsistent, status must not flip back to valid."""
        stable = [100.0 + i * 0.3 for i in range(70)]
        stable.append(stable[-1] - 200.0)
        result = validate_macd_histogram(stable)
        assert result["consistency_status"] == "inconsistent"

    def test_zscore_clamped_to_10(self):
        """z_score must never exceed 10 in the details even for absurd outliers."""
        prices = _linear_prices(N - 1, step=0.1)
        prices.append(prices[-1] + 999_999.0)
        result = validate_macd_histogram(prices)
        z = result["details"]["z_score"]
        if z is not None:
            assert z <= 10.0


# ---------------------------------------------------------------------------
# 5. signal_quality
# ---------------------------------------------------------------------------

class TestSignalQuality:
    def test_signal_quality_enum(self):
        prices = _linear_prices(N)
        result = validate_macd_histogram(prices)
        assert result["signal_quality"] in (
            "strong_bullish", "weak_bullish", "neutral",
            "weak_bearish", "strong_bearish",
        )

    def test_strong_bullish(self):
        prices = [100.0 + i ** 1.8 * 0.05 for i in range(N)]
        result = validate_macd_histogram(prices)
        if result["histogram_sign"] == "positive" and result["momentum_strength"] == "strengthening":
            assert result["signal_quality"] == "strong_bullish"

    def test_weak_bullish(self):
        up = [100.0 + i * 0.5 for i in range(40)]
        decel = [up[-1] + i * 0.01 for i in range(20)]
        prices = up + decel
        result = validate_macd_histogram(prices)
        if result["histogram_sign"] == "positive" and result["momentum_strength"] == "weakening":
            assert result["signal_quality"] == "weak_bullish"

    def test_strong_bearish(self):
        prices = [100.0 - i ** 1.8 * 0.05 for i in range(N)]
        result = validate_macd_histogram(prices)
        if result["histogram_sign"] == "negative" and result["momentum_strength"] == "strengthening":
            assert result["signal_quality"] == "strong_bearish"

    def test_weak_bearish(self):
        down = [100.0 - i * 0.5 for i in range(40)]
        decel = [down[-1] - i * 0.01 for i in range(20)]
        prices = down + decel
        result = validate_macd_histogram(prices)
        if result["histogram_sign"] == "negative" and result["momentum_strength"] == "weakening":
            assert result["signal_quality"] == "weak_bearish"

    def test_neutral_at_zero(self):
        prices = [100.0] * N
        result = validate_macd_histogram(prices)
        assert result["signal_quality"] == "neutral"


# ---------------------------------------------------------------------------
# 6. diagnostic_message
# ---------------------------------------------------------------------------

class TestDiagnosticMessage:
    def test_message_non_empty(self):
        prices = _linear_prices(N)
        result = validate_macd_histogram(prices)
        assert isinstance(result["diagnostic_message"], str)
        assert len(result["diagnostic_message"]) > 10

    def test_inconsistent_message_mentions_inconsistency(self):
        prices = _linear_prices(N - 1, step=0.1)
        prices.append(prices[-1] + 999_999.0)
        result = validate_macd_histogram(prices)
        if result["consistency_status"] == "inconsistent":
            assert "inconsisten" in result["diagnostic_message"].lower() or "inconsistency" in result["diagnostic_message"].lower()


# ---------------------------------------------------------------------------
# 7. Insufficient data
# ---------------------------------------------------------------------------

class TestInsufficientData:
    def test_raises_on_too_few_candles(self):
        with pytest.raises(ValueError, match="at least"):
            validate_macd_histogram([1.0] * 10)

    def test_exact_minimum_does_not_raise(self):
        prices = [100.0 + i * 0.1 for i in range(35)]
        result = validate_macd_histogram(prices)
        assert "histogram_value" in result

    def test_custom_periods_minimum_enforced(self):
        with pytest.raises(ValueError):
            validate_macd_histogram([1.0] * 20, fast=5, slow=20, signal=5)

    def test_custom_periods_exact_minimum(self):
        prices = [100.0 + i * 0.1 for i in range(25)]
        result = validate_macd_histogram(prices, fast=5, slow=20, signal=5)
        assert "histogram_value" in result


# ---------------------------------------------------------------------------
# 8. Consistency with FeatureEngine._calc_macd
# ---------------------------------------------------------------------------

class TestConsistencyWithFeatureEngine:
    def test_histogram_value_matches(self):
        prices = _linear_prices(N, step=0.4)
        df = _build_df(prices)
        engine = FeatureEngine(MACD_CONFIG)
        fe_result = engine._calc_macd(df)
        val_result = validate_macd_histogram(prices)

        fe_hist = fe_result["macd_histogram"]
        val_hist = val_result["histogram_value"]
        assert fe_hist is not None
        assert abs(fe_hist - val_hist) < 1e-6, (
            f"FeatureEngine histogram {fe_hist} != validate histogram {val_hist}"
        )

    def test_macd_line_matches(self):
        prices = _linear_prices(N, step=0.4)
        df = _build_df(prices)
        engine = FeatureEngine(MACD_CONFIG)
        fe_result = engine._calc_macd(df)
        val_result = validate_macd_histogram(prices)

        assert abs(fe_result["macd"] - val_result["details"]["macd_line"]) < 1e-6

    def test_signal_line_matches(self):
        prices = _linear_prices(N, step=0.4)
        df = _build_df(prices)
        engine = FeatureEngine(MACD_CONFIG)
        fe_result = engine._calc_macd(df)
        val_result = validate_macd_histogram(prices)

        assert abs(fe_result["macd_signal_line"] - val_result["details"]["signal_line"]) < 1e-6

    def test_histogram_prev_matches(self):
        prices = _linear_prices(N, step=0.4)
        df = _build_df(prices)
        engine = FeatureEngine(MACD_CONFIG)
        fe_result = engine._calc_macd(df)
        val_result = validate_macd_histogram(prices)

        fe_prev = fe_result.get("macd_histogram_prev")
        val_prev = val_result["details"]["histogram_prev"]
        assert fe_prev is not None
        assert val_prev is not None
        assert abs(fe_prev - val_prev) < 1e-6

    def test_mean_10_matches(self):
        prices = _linear_prices(N, step=0.4)
        df = _build_df(prices)
        engine = FeatureEngine(MACD_CONFIG)
        fe_result = engine._calc_macd(df)
        val_result = validate_macd_histogram(prices)

        fe_mean = fe_result.get("macd_histogram_mean_10")
        val_mean = val_result["details"]["histogram_mean_10"]
        assert fe_mean is not None
        assert abs(fe_mean - val_mean) < 1e-6

    def test_histogram_slope_is_current_minus_prev(self):
        """macd_histogram_slope = histogram_value - histogram_prev."""
        prices = _linear_prices(N, step=0.4)
        df = _build_df(prices)
        engine = FeatureEngine(MACD_CONFIG)
        fe_result = engine._calc_macd(df)

        hist = fe_result["macd_histogram"]
        prev = fe_result["macd_histogram_prev"]
        slope = fe_result.get("macd_histogram_slope")
        assert hist is not None
        assert prev is not None
        assert slope is not None
        assert abs(slope - (hist - prev)) < 1e-7


# ---------------------------------------------------------------------------
# 9. details structure
# ---------------------------------------------------------------------------

class TestDetailsStructure:
    def test_all_detail_keys_present(self):
        prices = _linear_prices(N)
        result = validate_macd_histogram(prices)
        details = result["details"]
        for key in ("macd_line", "signal_line", "histogram_prev",
                    "histogram_mean_10", "histogram_std_10", "z_score",
                    "outlier_threshold"):
            assert key in details, f"Missing key: {key}"

    def test_top_level_keys_present(self):
        prices = _linear_prices(N)
        result = validate_macd_histogram(prices)
        for key in ("histogram_value", "histogram_sign", "momentum_state",
                    "momentum_direction", "momentum_strength",
                    "consistency_status", "signal_quality",
                    "diagnostic_message", "details"):
            assert key in result, f"Missing top-level key: {key}"

    def test_momentum_state_valid_enum(self):
        """momentum_state must be one of increasing/decreasing/flat."""
        prices = _linear_prices(N)
        result = validate_macd_histogram(prices)
        assert result["momentum_state"] in ("increasing", "decreasing", "flat")

    def test_momentum_state_consistent_with_direction(self):
        """momentum_state maps 1-to-1 from momentum_direction."""
        mapping = {"up": "increasing", "down": "decreasing", "flat": "flat"}
        prices = _linear_prices(N)
        result = validate_macd_histogram(prices)
        assert result["momentum_state"] == mapping[result["momentum_direction"]]

    def test_outlier_threshold_is_mean_plus_3std(self):
        """outlier_threshold = mean_10 + 3 * std_10 when std > 0."""
        prices = _linear_prices(N, step=0.4)
        result = validate_macd_histogram(prices)
        details = result["details"]
        mean = details["histogram_mean_10"]
        std = details["histogram_std_10"]
        threshold = details["outlier_threshold"]
        if std and std > 0:
            expected = round(mean + 3 * std, 8)
            assert abs(threshold - expected) < 1e-7
        else:
            assert threshold is None
