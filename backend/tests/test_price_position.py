from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from app.services.price_position import (
    BREAKOUT_REFERENCE_INDICATORS,
    calculate_price_position,
)
from app.services.robust_indicators.envelope import DataSource, wrap_indicator
from app.services.robust_indicators.score import calculate_score_with_confidence
from app.services.rule_engine import RuleEngine


AS_OF = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)


def _frame_5m() -> pd.DataFrame:
    times = pd.date_range(end=AS_OF - timedelta(minutes=5), periods=210, freq="5min", tz="UTC")
    closes = [100.0 + index * 0.05 for index in range(len(times))]
    frame = pd.DataFrame({
        "time": times,
        "open": closes,
        "high": [value + 0.2 for value in closes],
        "low": [value - 0.2 for value in closes],
        "close": closes,
        "volume": [10.0] * len(times),
    })
    partial = pd.DataFrame([{
        "time": AS_OF,
        "open": 500.0,
        "high": 999.0,
        "low": 1.0,
        "close": 900.0,
        "volume": 10.0,
    }])
    return pd.concat([frame, partial], ignore_index=True)


def _frame_1m() -> pd.DataFrame:
    times = pd.date_range(end=AS_OF - timedelta(minutes=1), periods=10, freq="1min", tz="UTC")
    closes = [100.0 + index for index in range(len(times))]
    return pd.DataFrame({
        "time": times,
        "open": closes,
        "high": [value + 0.1 for value in closes],
        "low": [value - 0.1 for value in closes],
        "close": closes,
        "volume": [1.0] * len(times),
    })


def test_price_position_computes_all_requested_metrics_from_closed_candles():
    frame_5m = _frame_5m()
    frame_1m = _frame_1m()
    result = calculate_price_position(frame_5m, df_1m=frame_1m, as_of=AS_OF)

    expected = {
        "ema5_distance_pct", "ema9_distance_pct", "ema21_distance_pct",
        "ema50_distance_pct", "ema200_distance_pct", "vwap_distance_pct",
        "bb_upper_distance_pct", "bb_middle_distance_pct", "bb_lower_distance_pct",
        "recent_high_5m_distance_pct", "recent_high_15m_distance_pct",
        "recent_high_30m_distance_pct", "recent_high_1h_distance_pct",
        "recent_low_15m_distance_pct", "price_change_1m_pct",
        "price_change_5m_pct", "price_change_15m_pct",
    }
    assert expected <= result.keys()
    assert all(result[name] is not None for name in expected)
    assert result["ema21_distance_pct"] > 0
    assert result["price_change_5m_pct"] > 0
    assert result["price_change_15m_pct"] > result["price_change_5m_pct"]


def test_ema21_distance_uses_fixed_ema_period_21():
    frame = _frame_5m()
    result = calculate_price_position(frame, as_of=AS_OF)
    closed = pd.to_numeric(frame.iloc[:-1]["close"], errors="coerce")
    price = float(closed.iloc[-1])
    ema21 = float(closed.ewm(span=21, adjust=False).mean().iloc[-1])
    expected = round((price - ema21) / ema21 * 100.0, 4)

    assert result["ema21_distance_pct"] == expected


def test_recent_high_excludes_base_and_partial_candles():
    frame = _frame_5m()
    closed_base_index = len(frame) - 2
    frame.loc[closed_base_index, ["open", "high", "low", "close"]] = [200.0, 800.0, 199.0, 200.0]
    prior_high = float(frame.loc[closed_base_index - 1, "high"])

    result = calculate_price_position(frame, as_of=AS_OF)

    assert result["recent_high_5m_level"] == prior_high
    assert result["recent_high_5m_level"] != 800.0
    assert result["recent_high_5m_level"] != 999.0


def test_price_change_1m_is_unavailable_without_closed_1m_source():
    result = calculate_price_position(_frame_5m(), df_1m=None, as_of=AS_OF)
    assert result["price_change_1m_pct"] is None


def test_breakout_rule_resolves_reference_window_and_preserves_trace():
    data = {"recent_high_15m_distance_pct": 0.25}
    condition = {
        "id": "breakout",
        "field": "breakout_distance_pct",
        "reference_window": "15m",
        "operator": "between",
        "min": -0.3,
        "max": 1.0,
    }

    status, detail = RuleEngine().evaluate_condition_status(condition, data)

    assert status.value == "PASS"
    assert detail["reference_window"] == "15m"
    assert detail["resolved_indicator"] == BREAKOUT_REFERENCE_INDICATORS["15m"]


def test_breakout_rule_without_reference_window_is_skipped_not_zero():
    status, detail = RuleEngine().evaluate_condition_status(
        {
            "field": "breakout_distance_pct",
            "operator": "between",
            "min": -0.3,
            "max": 1.0,
        },
        {"recent_high_15m_distance_pct": 0.0},
    )
    assert status.value == "SKIPPED"
    assert detail["actual"] is None


def test_robust_scoring_resolves_breakout_reference_window():
    envelopes = {
        "recent_high_30m_distance_pct": wrap_indicator(
            "recent_high_30m_distance_pct",
            0.2,
            DataSource.GATE_CANDLES,
        ),
    }
    score = calculate_score_with_confidence(envelopes, [{
        "id": "breakout_30m",
        "indicator": "breakout_distance_pct",
        "reference_window": "30m",
        "operator": "between",
        "min": -0.3,
        "max": 1.0,
        "points": 10,
        "category": "signal",
    }])
    assert score.score == 100.0
    assert score.matched_rules[0]["reference_window"] == "30m"


def test_frontend_uses_one_price_position_catalog_on_every_profile_surface():
    frontend = Path(__file__).resolve().parents[2] / "frontend"
    catalog = (frontend / "lib/indicatorCatalog.ts").read_text(encoding="utf-8")
    required = {
        "ema21_distance_pct", "vwap_distance_pct", "bb_upper_distance_pct",
        "recent_high_5m_distance_pct", "recent_high_15m_distance_pct",
        "recent_high_30m_distance_pct", "recent_high_1h_distance_pct",
        "recent_low_15m_distance_pct", "breakout_distance_pct",
        "price_change_1m_pct", "price_change_5m_pct", "price_change_15m_pct",
    }
    assert all(name in catalog for name in required)
    for relative in (
        "components/profiles/ConditionBuilder.tsx",
        "components/profiles/ProfileBuilder.tsx",
        "components/profiles/BulkProfileBuilder.tsx",
    ):
        source = (frontend / relative).read_text(encoding="utf-8")
        assert "indicatorOptionsForSection" in source
    score_source = (frontend / "app/settings/score/page.tsx").read_text(encoding="utf-8")
    assert "PRICE_POSITION_INDICATORS" in score_source
