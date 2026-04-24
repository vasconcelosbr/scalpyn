from datetime import datetime, timedelta, timezone

import pandas as pd

from app.services.feature_engine import FeatureEngine
from app.services.seed_service import DEFAULT_INDICATORS


def _build_hourly_frame(candles: int, close: float = 2.0) -> pd.DataFrame:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = []
    for idx in range(candles):
        open_price = close - 0.05 if idx % 2 == 0 else close + 0.05
        rows.append(
            {
                "time": start + timedelta(hours=idx),
                "open": open_price,
                "high": close + 0.1,
                "low": close - 0.1,
                "close": close,
                "volume": 10 + idx,
                "quote_volume": (10 + idx) * close,
            }
        )
    return pd.DataFrame(rows)


def test_feature_engine_preserves_candle_volume_when_24h_window_complete():
    engine = FeatureEngine(DEFAULT_INDICATORS)
    df = _build_hourly_frame(24)
    market_data = {
        "snapshot_consistent": True,
        "volume_24h_ticker_base": 999.0,
        "volume_24h_ticker_usdt": 1998.0,
        "indicator_trace": {
            "volume_24h_ticker_base": {
                "value": 999.0,
                "source": "gate_ticker",
                "timestamp": "2026-01-02T00:00:00+00:00",
            },
            "volume_24h_ticker_usdt": {
                "value": 1998.0,
                "source": "gate_ticker",
                "timestamp": "2026-01-02T00:00:00+00:00",
            },
        },
    }

    results = engine.calculate(df, market_data=market_data)

    expected_base = round(float(df["volume"].sum()), 8)
    expected_usdt = round(float(df["quote_volume"].sum()), 8)
    assert results["volume_24h_candles_base"] == expected_base
    assert results["volume_24h_candles_usdt"] == expected_usdt
    assert results["volume_24h_ticker_usdt"] == 1998.0
    assert results["volume_24h_usdt"] == expected_usdt
    assert results["volume_24h_final"] == expected_usdt
    assert results["volume_24h_final_source"] == "gate_candle"
    assert results["indicator_trace"]["volume_24h_final"]["source"] == "gate_candle"


def test_feature_engine_uses_trade_metrics_explicitly_when_candle_window_is_incomplete():
    engine = FeatureEngine(DEFAULT_INDICATORS)
    df = _build_hourly_frame(10)
    market_data = {
        "snapshot_consistent": True,
        "volume_24h_ticker_base": 150.0,
        "volume_24h_ticker_usdt": 300.0,
        "taker_buy_volume": 12.0,
        "taker_sell_volume": 4.0,
        "taker_ratio": 3.0,
        "volume_delta_trades": 8.0,
        "orderbook_depth_usdt": 125.0,
        "orderbook_depth_source": "binance",
        "spread_pct": 0.12,
        "indicator_trace": {
            "volume_24h_ticker_base": {
                "value": 150.0,
                "source": "gate_ticker",
                "timestamp": "2026-01-02T00:00:00+00:00",
            },
            "volume_24h_ticker_usdt": {
                "value": 300.0,
                "source": "gate_ticker",
                "timestamp": "2026-01-02T00:00:00+00:00",
            },
            "taker_ratio": {
                "value": 3.0,
                "source": "binance_trade",
                "timestamp": "2026-01-02T00:00:01+00:00",
            },
            "volume_delta_trades": {
                "value": 8.0,
                "source": "binance_trade",
                "timestamp": "2026-01-02T00:00:01+00:00",
            },
            "orderbook_depth_usdt": {
                "value": 125.0,
                "source": "binance_orderbook",
                "timestamp": "2026-01-02T00:00:01+00:00",
            },
            "spread_pct": {
                "value": 0.12,
                "source": "gate_ticker",
                "timestamp": "2026-01-02T00:00:00+00:00",
            },
        },
    }

    results = engine.calculate(df, market_data=market_data)

    assert results["volume_24h_window_complete"] is False
    assert results["volume_24h_usdt"] == 300.0
    assert results["volume_24h_final"] == 300.0
    assert results["volume_24h_final_source"] == "gate_ticker"
    assert results["volume_delta_candle"] != results["volume_delta_trades"]
    assert results["volume_delta"] == 8.0
    assert results["taker_ratio_candle"] != results["taker_ratio"]
    assert results["taker_ratio"] == 3.0
    assert results["orderbook_depth_source"] == "binance"
    assert "volume_window_incomplete" in results["data_quality_flags"]
    assert results["indicator_trace"]["volume_delta"]["source"] == "binance_trade"
    assert results["indicator_trace"]["volume_24h_final"]["source"] == "gate_ticker"
