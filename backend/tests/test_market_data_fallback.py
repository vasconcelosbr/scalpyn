import math

import pytest

from app.services.market_data_service import MarketDataService


@pytest.mark.anyio
async def test_fetch_orderbook_metrics_uses_binance_fallback_when_gate_depth_missing(monkeypatch):
    service = MarketDataService()

    async def fake_gate_orderbook(symbol: str, depth: int):
        return {"bids": [], "asks": []}

    async def fake_binance_orderbook(symbol: str, depth: int):
        return {
            "bids": [["1.00", "10"]],
            "asks": [["1.10", "20"]],
        }

    monkeypatch.setattr(service, "_fetch_gate_orderbook", fake_gate_orderbook)
    monkeypatch.setattr(service, "_fetch_binance_orderbook", fake_binance_orderbook)

    metrics = await service.fetch_orderbook_metrics("ADA_USDT", depth=10)

    assert metrics["orderbook_depth_usdt"] == 32.0
    assert metrics["market_data_source"] == "binance"


@pytest.mark.anyio
async def test_fetch_indicator_fallbacks_returns_mixed_normalized_market_data(monkeypatch):
    service = MarketDataService()

    existing_data = {
        "price": 2.0,
        "volume_24h": None,
        "spread_pct": None,
        "orderbook_depth_usdt": None,
    }

    async def fake_gate_ticker(symbol: str):
        return {
            "currency_pair": "ADA_USDT",
            "last": "2.0",
            "base_volume": "1000",
            "quote_volume": "2000",
            "highest_bid": "1.99",
            "lowest_ask": "2.01",
        }

    async def fake_gate_orderbook(symbol: str, depth: int):
        return {"bids": [], "asks": []}

    async def fake_binance_orderbook(symbol: str, depth: int):
        return {
            "bids": [["2.00", "5"]],
            "asks": [["2.10", "4"]],
        }

    async def fake_binance_trades(symbol: str, limit: int):
        return [
            {"price": "2.00", "qty": "3", "quoteQty": "6", "isBuyerMaker": False},
            {"price": "2.05", "qty": "1", "quoteQty": "2.05", "isBuyerMaker": True},
        ]

    monkeypatch.setattr(service, "_fetch_gate_ticker", fake_gate_ticker)
    monkeypatch.setattr(service, "_fetch_gate_orderbook", fake_gate_orderbook)
    monkeypatch.setattr(service, "_fetch_binance_orderbook", fake_binance_orderbook)
    monkeypatch.setattr(service, "_fetch_binance_trades", fake_binance_trades)

    payload = await service.fetch_indicator_fallbacks("ADAUSDT", existing_data=existing_data, depth=10)

    assert payload["market_data_symbol"] == "ADA/USDT"
    assert payload["volume_24h_ticker_base"] == 1000.0
    assert payload["volume_24h_ticker_usdt"] == 2000.0
    assert payload["orderbook_depth_usdt"] == 18.4
    assert payload["orderbook_depth_source"] == "binance"
    assert payload["taker_buy_volume"] == 3.0
    assert payload["taker_sell_volume"] == 1.0
    assert payload["volume_delta_trades"] == 2.0
    assert math.isclose(payload["taker_ratio"], 3.0, rel_tol=1e-9)
    assert payload["indicator_trace"]["volume_delta_trades"]["source"] == "binance_trade"
    assert payload["market_data_source"] == "mixed"
    assert payload["market_data_confidence"] == 0.85
