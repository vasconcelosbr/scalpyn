"""Gate.io Exchange Adapter — implements BaseExchangeAdapter for Gate.io."""

import logging
import time
import hashlib
import hmac
import json
from typing import List, Dict, Any

import httpx

from .base_adapter import BaseExchangeAdapter

logger = logging.getLogger(__name__)


class GateAdapter(BaseExchangeAdapter):
    """Gate.io V4 API adapter."""

    BASE_URL = "https://api.gateio.ws"
    PREFIX = "/api/v4"

    def __init__(self, api_key: str, api_secret: str):
        self.api_key = api_key.strip()
        self.api_secret = api_secret.strip()

    def _sign(self, method: str, endpoint: str, query: str = "", body: str = "") -> Dict[str, str]:
        t = str(int(time.time()))
        hashed_body = hashlib.sha512(body.encode("utf-8")).hexdigest()
        sign_string = f"{method}\n{self.PREFIX}{endpoint}\n{query}\n{hashed_body}\n{t}"
        sign = hmac.new(
            self.api_secret.encode("utf-8"),
            sign_string.encode("utf-8"),
            hashlib.sha512,
        ).hexdigest()
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "KEY": self.api_key,
            "Timestamp": t,
            "SIGN": sign,
        }

    async def _request(self, method: str, endpoint: str, params: dict = None, body: dict = None) -> Any:
        query = "&".join(f"{k}={v}" for k, v in (params or {}).items())
        body_str = json.dumps(body) if body else ""
        headers = self._sign(method, endpoint, query, body_str)
        url = f"{self.BASE_URL}{self.PREFIX}{endpoint}"
        if query:
            url += f"?{query}"

        async with httpx.AsyncClient(timeout=15) as client:
            if method == "GET":
                r = await client.get(url, headers=headers)
            elif method == "POST":
                r = await client.post(url, headers=headers, content=body_str)
            elif method == "DELETE":
                r = await client.delete(url, headers=headers)
            else:
                raise ValueError(f"Unsupported method: {method}")

        if r.status_code not in (200, 201):
            raise Exception(f"Gate.io API error {r.status_code}: {r.text}")
        return r.json()

    async def fetch_ohlcv(self, symbol: str, timeframe: str) -> List[Dict[str, Any]]:
        pair = symbol.replace("USDT", "_USDT") if "_" not in symbol else symbol
        data = await self._request("GET", "/spot/candlesticks", params={
            "currency_pair": pair,
            "interval": timeframe,
            "limit": "200",
        })
        return [
            {
                "time": int(c[0]),
                "open": float(c[5]),
                "high": float(c[3]),
                "low": float(c[4]),
                "close": float(c[2]),
                "volume": float(c[1]),
            }
            for c in data
        ]

    async def fetch_funding_rate(self, symbol: str) -> float:
        pair = symbol.replace("USDT", "_USDT") if "_" not in symbol else symbol
        try:
            data = await self._request("GET", f"/futures/usdt/contracts/{pair}")
            return float(data.get("funding_rate", 0))
        except Exception:
            return 0.0

    async def create_order(
        self, symbol: str, side: str, order_type: str, quantity: float, price: float = None
    ) -> Dict[str, Any]:
        pair = symbol.replace("USDT", "_USDT") if "_" not in symbol else symbol
        body = {
            "currency_pair": pair,
            "side": side,
            "type": order_type,
            "amount": str(quantity),
        }
        if order_type == "limit" and price:
            body["price"] = str(price)
            body["time_in_force"] = "gtc"

        return await self._request("POST", "/spot/orders", body=body)

    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        pair = symbol.replace("USDT", "_USDT") if "_" not in symbol else symbol
        try:
            await self._request("DELETE", f"/spot/orders/{order_id}", params={"currency_pair": pair})
            return True
        except Exception:
            return False

    async def get_balances(self) -> Dict[str, float]:
        accounts = await self._request("GET", "/spot/accounts")
        balances = {}
        for acc in accounts:
            avail = float(acc.get("available", 0))
            locked = float(acc.get("locked", 0))
            if avail > 0 or locked > 0:
                balances[acc.get("currency", "")] = avail + locked
        return balances
