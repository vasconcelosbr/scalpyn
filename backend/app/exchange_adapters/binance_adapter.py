"""Binance public market-data adapter used as a fallback for critical indicators."""

import logging
from typing import Any, Dict, List, Optional

import httpx

from .base_adapter import BaseExchangeAdapter

logger = logging.getLogger(__name__)


class BinanceAdapter(BaseExchangeAdapter):
    """Lightweight Binance spot adapter for public market-data endpoints.

    The base adapter contract requires api_key/api_secret constructor arguments;
    they are retained here for interface compatibility and future authenticated
    extensions, but current fallback usage only calls public endpoints.
    """

    SPOT_BASE = "https://api.binance.com/api/v3"

    def __init__(self, api_key: str = "", api_secret: str = ""):
        _ = api_key, api_secret

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        raw = str(symbol or "").upper().strip().replace("/", "").replace("_", "")
        return raw

    async def _request(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Any:
        url = f"{self.SPOT_BASE}{endpoint}"
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            return response.json()

    async def fetch_ohlcv(self, symbol: str, timeframe: str) -> List[Dict[str, Any]]:
        return await self.get_klines(symbol, interval=timeframe, market="spot")

    async def fetch_funding_rate(self, symbol: str) -> float:
        return 0.0

    async def create_order(
        self, symbol: str, side: str, order_type: str,
        quantity: float, price: Optional[float] = None
    ) -> Dict[str, Any]:
        raise NotImplementedError("BinanceAdapter only supports public market-data endpoints.")

    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        raise NotImplementedError("BinanceAdapter only supports public market-data endpoints.")

    async def get_balances(self) -> Dict[str, float]:
        raise NotImplementedError("BinanceAdapter only supports public market-data endpoints.")

    async def get_spot_balance(self) -> Dict[str, Any]:
        raise NotImplementedError("BinanceAdapter only supports public market-data endpoints.")

    async def get_futures_balance(self) -> Dict[str, Any]:
        raise NotImplementedError("BinanceAdapter only supports public market-data endpoints.")

    async def transfer_between_accounts(
        self, currency: str, from_account: str, to_account: str, amount: str
    ) -> Dict[str, Any]:
        raise NotImplementedError("BinanceAdapter only supports public market-data endpoints.")

    async def get_tickers(
        self, symbols: Optional[List[str]] = None, market: str = "spot"
    ) -> List[Dict[str, Any]]:
        if market != "spot":
            return []
        if symbols:
            data = await self._request(
                "/ticker/24hr",
                params={"symbol": self._normalize_symbol(symbols[0])},
            )
            return [data] if isinstance(data, dict) else data
        data = await self._request("/ticker/24hr")
        return data if isinstance(data, list) else [data]

    async def get_orderbook(
        self, symbol: str, market: str = "spot", depth: int = 20
    ) -> Dict[str, Any]:
        if market != "spot":
            return {}
        return await self._request(
            "/depth",
            params={"symbol": self._normalize_symbol(symbol), "limit": depth},
        )

    async def get_recent_trades(self, symbol: str, limit: int = 500) -> List[Dict[str, Any]]:
        return await self._request(
            "/trades",
            params={"symbol": self._normalize_symbol(symbol), "limit": limit},
        )

    async def get_klines(
        self, symbol: str, interval: str = "1h",
        limit: int = 200, market: str = "spot"
    ) -> List[Dict[str, Any]]:
        if market != "spot":
            return []
        raw = await self._request(
            "/klines",
            params={"symbol": self._normalize_symbol(symbol), "interval": interval, "limit": limit},
        )
        return [
            {
                "time": int(candle[0]) // 1000,
                "open": float(candle[1]),
                "high": float(candle[2]),
                "low": float(candle[3]),
                "close": float(candle[4]),
                "volume": float(candle[5]),
                "quote_volume": float(candle[7]),
            }
            for candle in raw
        ]

    async def get_contract_info(self, contract: str) -> Dict[str, Any]:
        raise NotImplementedError("BinanceAdapter only supports public spot market-data endpoints.")

    async def get_contract_stats(
        self, contract: str, interval: str = "5m", limit: int = 1
    ) -> List[Dict[str, Any]]:
        raise NotImplementedError("BinanceAdapter only supports public spot market-data endpoints.")

    async def place_spot_order(
        self,
        currency_pair: str,
        side: str,
        order_type: str,
        amount: str,
        price: Optional[str] = None,
        time_in_force: str = "gtc",
        text: str = "t-scalpyn",
    ) -> Dict[str, Any]:
        raise NotImplementedError("BinanceAdapter only supports public market-data endpoints.")

    async def create_spot_price_trigger(
        self,
        currency_pair: str,
        trigger_price: str,
        trigger_rule: str,
        order_side: str,
        order_amount: str,
        expiration: int = 2592000,
        text: str = "t-scalpyn-tp",
    ) -> Dict[str, Any]:
        raise NotImplementedError("BinanceAdapter only supports public market-data endpoints.")

    async def get_futures_position(self, contract: str) -> Dict[str, Any]:
        raise NotImplementedError("BinanceAdapter only supports public market-data endpoints.")

    async def place_futures_order(
        self,
        contract: str,
        size: int,
        price: str = "0",
        tif: str = "ioc",
        is_reduce_only: bool = False,
        is_close: bool = False,
        text: str = "t-scalpyn",
    ) -> Dict[str, Any]:
        raise NotImplementedError("BinanceAdapter only supports public market-data endpoints.")

    async def set_leverage(
        self, contract: str, leverage: int, cross_leverage_limit: Optional[int] = None
    ) -> Dict[str, Any]:
        raise NotImplementedError("BinanceAdapter only supports public market-data endpoints.")

    async def close_position(self, contract: str, text: str = "t-scalpyn-close") -> Dict[str, Any]:
        raise NotImplementedError("BinanceAdapter only supports public market-data endpoints.")

    async def create_price_trigger(
        self,
        contract: str,
        trigger_price: str,
        trigger_rule: int,
        size: int,
        is_close: bool = False,
        is_reduce_only: bool = False,
        price_type: int = 1,
        expiration: int = 604800,
        text: str = "t-scalpyn-sl",
    ) -> Dict[str, Any]:
        raise NotImplementedError("BinanceAdapter only supports public market-data endpoints.")

    async def modify_price_trigger(
        self, order_id: int, trigger_price: str
    ) -> Dict[str, Any]:
        raise NotImplementedError("BinanceAdapter only supports public market-data endpoints.")

    async def cancel_price_trigger(self, order_id: int) -> Dict[str, Any]:
        raise NotImplementedError("BinanceAdapter only supports public market-data endpoints.")

    async def cancel_all_price_triggers(self, contract: str) -> List[Dict[str, Any]]:
        raise NotImplementedError("BinanceAdapter only supports public market-data endpoints.")
