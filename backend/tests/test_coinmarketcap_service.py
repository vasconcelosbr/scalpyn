import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services import coinmarketcap_service


def test_normalize_market_cap_symbols_strips_pairs_and_dedupes():
    symbols = [
        "btc_usdt",
        "BTCUSDT",
        "ETH",
        "ETH_USDT",
        "TON2L_USDT",
        "",
    ]

    assert coinmarketcap_service.normalize_market_cap_symbols(symbols) == ["BTC", "ETH"]


def test_extract_market_caps_accepts_dict_and_list_payloads():
    payload = {
        "data": {
            "BTC": [{"quote": {"USD": {"market_cap": 1_000_000}}}],
            "ETH": {"quote": {"USD": {"market_cap": "2500000.5"}}},
            "DOGE": [{"quote": {"USD": {"market_cap": None}}}],
        }
    }

    assert coinmarketcap_service.extract_market_caps(payload) == {
        "BTC": 1_000_000.0,
        "ETH": 2_500_000.5,
    }


def test_fetch_market_caps_batches_requests(monkeypatch):
    requested_batches = []

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers=None, params=None):
            batch = params["symbol"].split(",")
            requested_batches.append(batch)
            return FakeResponse(
                {
                    "data": {
                        symbol: {"quote": {"USD": {"market_cap": index + 1}}}
                        for index, symbol in enumerate(batch)
                    }
                }
            )

    monkeypatch.setattr(coinmarketcap_service.httpx, "AsyncClient", lambda **kwargs: FakeAsyncClient())

    symbols = [f"COIN{i}_USDT" for i in range(coinmarketcap_service.CMC_BATCH_SIZE + 1)]
    result = asyncio.run(coinmarketcap_service.fetch_market_caps(symbols, "test-key"))

    assert len(requested_batches) == 2
    assert len(requested_batches[0]) == coinmarketcap_service.CMC_BATCH_SIZE
    assert requested_batches[1] == [f"COIN{coinmarketcap_service.CMC_BATCH_SIZE}"]
    assert result["COIN0"] == 1.0
    assert result[f"COIN{coinmarketcap_service.CMC_BATCH_SIZE}"] == 1.0
