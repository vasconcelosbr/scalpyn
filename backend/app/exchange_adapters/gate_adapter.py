"""Gate.io Exchange Adapter — Gate.io v4 REST API implementation."""

import asyncio
import hashlib
import hmac
import json
import logging
import time
from typing import Any, Dict, List, Optional

import httpx

from .base_adapter import BaseExchangeAdapter
from ..utils.gate_market_data import parse_gate_spot_candle

logger = logging.getLogger(__name__)


# ── Scalpyn-specific exceptions ───────────────────────────────────────────────

class GateAPIError(Exception):
    """Raised when Gate.io returns a non-2xx response."""
    def __init__(self, status_code: int, label: str, message: str):
        self.status_code = status_code
        self.label = label
        self.message = message
        super().__init__(f"[{label}] {message} (HTTP {status_code})")


class InsufficientBalanceError(GateAPIError):
    pass


class PositionNotFoundError(GateAPIError):
    pass


class OrderNotFoundError(GateAPIError):
    pass


class LeverageTooHighError(GateAPIError):
    pass


class OrderSizeError(GateAPIError):
    pass


class RiskLimitExceededError(GateAPIError):
    pass


# ── Gate error label → exception class ───────────────────────────────────────

_GATE_ERROR_MAP: Dict[str, type] = {
    "BALANCE_NOT_ENOUGH":        InsufficientBalanceError,
    "POSITION_NOT_FOUND":        PositionNotFoundError,
    "ORDER_NOT_FOUND":           OrderNotFoundError,
    "LEVERAGE_TOO_HIGH":         LeverageTooHighError,
    "ORDER_SIZE_TOO_SMALL":      OrderSizeError,
    "ORDER_SIZE_TOO_LARGE":      OrderSizeError,
    "RISK_LIMIT_EXCEEDED":       RiskLimitExceededError,
}


# ── Simple async token-bucket rate limiter ────────────────────────────────────

class _RateLimiter:
    """Leaky-bucket limiter. max_rate = requests per second."""

    def __init__(self, max_rate: float):
        self._interval = 1.0 / max_rate
        self._last_call = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = self._interval - (now - self._last_call)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call = time.monotonic()


# ── Adapter ───────────────────────────────────────────────────────────────────

class GateAdapter(BaseExchangeAdapter):
    """
    Gate.io v4 REST adapter for the Scalpyn trading engine.

    Auth: HMAC-SHA512 per Gate.io v4 spec.
    Rate limits:  reads  → 400 req/s (spot) / 200 req/s (futures)
                  orders → 200 req/s
    settle:       always "usdt" (USDT-margined perpetuals).
    """

    SPOT_BASE    = "https://api.gateio.ws/api/v4"
    FUTURES_BASE = "https://fx-api.gateio.ws/api/v4"
    PREFIX       = "/api/v4"
    SETTLE       = "usdt"

    def __init__(self, api_key: str, api_secret: str):
        self.api_key    = api_key.strip()
        self.api_secret = api_secret.strip()
        # separate limiters for read vs write traffic
        self._read_limiter  = _RateLimiter(max_rate=200)   # conservative safe floor
        self._write_limiter = _RateLimiter(max_rate=100)   # price_orders ceiling

    # ── Auth ──────────────────────────────────────────────────────────────────

    def _sign(
        self, method: str, endpoint: str,
        query: str = "", body: str = "",
    ) -> Dict[str, str]:
        t            = str(int(time.time()))
        hashed_body  = hashlib.sha512(body.encode()).hexdigest()
        sign_string  = f"{method}\n{self.PREFIX}{endpoint}\n{query}\n{hashed_body}\n{t}"
        signature    = hmac.new(
            self.api_secret.encode(),
            sign_string.encode(),
            hashlib.sha512,
        ).hexdigest()
        return {
            "Accept":       "application/json",
            "Content-Type": "application/json",
            "KEY":          self.api_key,
            "Timestamp":    t,
            "SIGN":         signature,
        }

    # ── HTTP transport ────────────────────────────────────────────────────────

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        """'SOLUSDT' → 'SOL_USDT'; 'SOL_USDT' passes through."""
        if "_" not in symbol and symbol.endswith("USDT"):
            return symbol[:-4] + "_USDT"
        return symbol

    async def _request(
        self,
        method:   str,
        endpoint: str,
        params:   Optional[Dict] = None,
        body:     Optional[Dict] = None,
        base_url: Optional[str]  = None,
        write:    bool           = False,
    ) -> Any:
        """
        Execute a signed Gate.io API call.

        Args:
            write: if True, use write-rate-limiter; else read-rate-limiter.
        """
        if write:
            await self._write_limiter.acquire()
        else:
            await self._read_limiter.acquire()

        query    = "&".join(f"{k}={v}" for k, v in (params or {}).items())
        body_str = json.dumps(body) if body else ""
        headers  = self._sign(method, endpoint, query, body_str)

        root = base_url or self.SPOT_BASE
        url  = f"{root}{endpoint}"
        if query:
            url += f"?{query}"

        async with httpx.AsyncClient(timeout=15) as client:
            if method == "GET":
                r = await client.get(url, headers=headers)
            elif method == "POST":
                r = await client.post(url, headers=headers, content=body_str)
            elif method == "PUT":
                r = await client.put(url, headers=headers, content=body_str)
            elif method == "DELETE":
                r = await client.delete(url, headers=headers)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")

        if r.status_code not in (200, 201):
            self._raise_gate_error(r)

        return r.json()

    @staticmethod
    def _raise_gate_error(r: httpx.Response) -> None:
        try:
            data  = r.json()
            label = data.get("label", "UNKNOWN_ERROR")
            msg   = data.get("message", r.text)
        except Exception:
            label = "PARSE_ERROR"
            msg   = r.text

        exc_cls = _GATE_ERROR_MAP.get(label, GateAPIError)
        raise exc_cls(r.status_code, label, msg)

    def _futures_url(self) -> str:
        return self.FUTURES_BASE

    # =========================================================================
    # ACCOUNT
    # =========================================================================

    async def get_spot_balance(self) -> Dict[str, Any]:
        """GET /spot/accounts → list of {currency, available, locked}."""
        return await self._request("GET", "/spot/accounts")

    async def get_futures_balance(self) -> Dict[str, Any]:
        """GET /futures/usdt/accounts → {available, equity, unrealised_pnl, ...}."""
        return await self._request(
            "GET", f"/futures/{self.SETTLE}/accounts",
            base_url=self._futures_url(),
        )

    async def get_balances(self) -> Dict[str, float]:
        """Legacy: flat {currency: total} dict from spot accounts."""
        accounts = await self.get_spot_balance()
        result   = {}
        for acc in accounts:
            avail  = float(acc.get("available", 0))
            locked = float(acc.get("locked", 0))
            if avail > 0 or locked > 0:
                result[acc.get("currency", "")] = avail + locked
        return result

    async def transfer_between_accounts(
        self,
        currency:     str,
        from_account: str,
        to_account:   str,
        amount:       str,
    ) -> Dict[str, Any]:
        """POST /wallet/transfers — from_account/to_account: 'spot' | 'futures'."""
        return await self._request(
            "POST", "/wallet/transfers",
            body={"currency": currency, "from": from_account, "to": to_account, "amount": amount},
            write=True,
        )

    # =========================================================================
    # MARKET DATA
    # =========================================================================

    async def get_tickers(
        self,
        symbols: Optional[List[str]] = None,
        market:  str = "spot",
    ) -> List[Dict[str, Any]]:
        """
        GET /spot/tickers or /futures/{settle}/tickers.
        Pass symbols=None for full universe.
        """
        if market == "futures":
            params = {}
            if symbols:
                params["contract"] = symbols[0]
            return await self._request(
                "GET", f"/futures/{self.SETTLE}/tickers",
                params=params, base_url=self._futures_url(),
            )
        # spot: optionally filter by currency_pair
        params = {}
        if symbols and len(symbols) == 1:
            params["currency_pair"] = self._normalize_symbol(symbols[0])
        return await self._request("GET", "/spot/tickers", params=params)

    async def get_orderbook(
        self,
        symbol: str,
        market: str = "spot",
        depth:  int = 20,
    ) -> Dict[str, Any]:
        """GET order book (asks, bids) for L1 liquidity scoring."""
        pair = self._normalize_symbol(symbol)
        if market == "futures":
            return await self._request(
                "GET", f"/futures/{self.SETTLE}/order_book",
                params={"contract": pair, "limit": str(depth)},
                base_url=self._futures_url(),
            )
        return await self._request(
            "GET", "/spot/order_book",
            params={"currency_pair": pair, "limit": str(depth)},
        )

    async def get_klines(
        self,
        symbol:   str,
        interval: str = "1h",
        limit:    int = 200,
        market:   str = "spot",
    ) -> List[Dict[str, Any]]:
        """
        Candlestick data. Returns normalized list:
        [{time, open, high, low, close, volume}, ...]
        """
        pair = self._normalize_symbol(symbol)
        if market == "futures":
            raw = await self._request(
                "GET", f"/futures/{self.SETTLE}/candlesticks",
                params={"contract": pair, "interval": interval, "limit": str(limit)},
                base_url=self._futures_url(),
            )
            return [
                {
                    "time":   int(c["t"]),
                    "open":   float(c["o"]),
                    "high":   float(c["h"]),
                    "low":    float(c["l"]),
                    "close":  float(c["c"]),
                    "volume": float(c["v"]),
                }
                for c in raw
            ]
        # spot candles: [t, quote_volume, close, high, low, open, base_volume, ...]
        raw = await self._request(
            "GET", "/spot/candlesticks",
            params={"currency_pair": pair, "interval": interval, "limit": str(limit)},
        )
        return [
            {
                **parse_gate_spot_candle(c),
                "time": int(c[0]),
            }
            for c in raw
        ]

    # Legacy alias
    async def fetch_ohlcv(self, symbol: str, timeframe: str) -> List[Dict[str, Any]]:
        return await self.get_klines(symbol, interval=timeframe, market="spot")

    async def get_contract_info(self, contract: str) -> Dict[str, Any]:
        """
        GET /futures/usdt/contracts/{contract}.
        Returns leverage_min/max, fees, mark_price, funding_rate, etc.
        """
        pair = self._normalize_symbol(contract)
        return await self._request(
            "GET", f"/futures/{self.SETTLE}/contracts/{pair}",
            base_url=self._futures_url(),
        )

    async def get_contract_stats(
        self,
        contract: str,
        interval: str = "5m",
        limit:    int = 1,
    ) -> List[Dict[str, Any]]:
        """
        GET /futures/usdt/contract_stats → OI, long/short ratio, top-trader data.
        Used by L5 Order Flow scoring.
        """
        pair = self._normalize_symbol(contract)
        return await self._request(
            "GET", f"/futures/{self.SETTLE}/contract_stats",
            params={"contract": pair, "interval": interval, "limit": str(limit)},
            base_url=self._futures_url(),
        )

    async def fetch_funding_rate(self, symbol: str) -> float:
        """Legacy: return current funding_rate for a contract."""
        try:
            info = await self.get_contract_info(symbol)
            return float(info.get("funding_rate", 0))
        except Exception:
            return 0.0

    # =========================================================================
    # SPOT TRADING
    # =========================================================================

    async def place_spot_order(
        self,
        currency_pair: str,
        side:          str,
        order_type:    str,
        amount:        str,
        price:         Optional[str] = None,
        time_in_force: str           = "gtc",
        text:          str           = "t-scalpyn",
    ) -> Dict[str, Any]:
        """
        POST /spot/orders.
        order_type: 'market' | 'limit'
        side: 'buy' | 'sell'
        amount: in USDT for market-buy, in base coin for market-sell / limit.
        """
        pair = self._normalize_symbol(currency_pair)
        body: Dict[str, Any] = {
            "currency_pair": pair,
            "side":          side,
            "type":          order_type,
            "amount":        amount,
            "time_in_force": "ioc" if order_type == "market" else time_in_force,
            "text":          text,
        }
        if order_type == "limit" and price:
            body["price"] = price
        return await self._request("POST", "/spot/orders", body=body, write=True)

    # Legacy alias
    async def create_order(
        self,
        symbol:     str,
        side:       str,
        order_type: str,
        quantity:   float,
        price:      Optional[float] = None,
    ) -> Dict[str, Any]:
        return await self.place_spot_order(
            currency_pair=symbol,
            side=side,
            order_type=order_type,
            amount=str(quantity),
            price=str(price) if price else None,
        )

    async def cancel_spot_order(self, order_id: str, currency_pair: str) -> bool:
        pair = self._normalize_symbol(currency_pair)
        try:
            await self._request(
                "DELETE", f"/spot/orders/{order_id}",
                params={"currency_pair": pair}, write=True,
            )
            return True
        except Exception:
            return False

    # Legacy alias
    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        return await self.cancel_spot_order(order_id, symbol)

    async def create_spot_price_trigger(
        self,
        currency_pair: str,
        trigger_price: str,
        trigger_rule:  str,
        order_side:    str,
        order_amount:  str,
        expiration:    int = 2592000,
        text:          str = "t-scalpyn-tp",
    ) -> Dict[str, Any]:
        """
        POST /spot/price_orders → automated TP for spot positions.
        trigger_rule: '>=' | '<='
        """
        pair = self._normalize_symbol(currency_pair)
        body = {
            "market":  pair,
            "trigger": {
                "price":      trigger_price,
                "rule":       trigger_rule,
                "expiration": expiration,
            },
            "put": {
                "side":          order_side,
                "type":          "market",
                "amount":        order_amount,
                "time_in_force": "ioc",
                "text":          text,
            },
        }
        return await self._request("POST", "/spot/price_orders", body=body, write=True)

    # =========================================================================
    # FUTURES TRADING
    # =========================================================================

    async def get_futures_position(self, contract: str) -> Dict[str, Any]:
        """
        GET /futures/usdt/positions/{contract}.
        Returns: size, entry_price, mark_price, liq_price, unrealised_pnl, leverage, ...
        """
        pair = self._normalize_symbol(contract)
        return await self._request(
            "GET", f"/futures/{self.SETTLE}/positions/{pair}",
            base_url=self._futures_url(),
        )

    async def list_futures_positions(self) -> List[Dict[str, Any]]:
        """GET /futures/usdt/positions → all open and historical position rows."""
        return await self._request(
            "GET", f"/futures/{self.SETTLE}/positions",
            base_url=self._futures_url(),
        )

    async def place_futures_order(
        self,
        contract:      str,
        size:          int,
        price:         str  = "0",
        tif:           str  = "ioc",
        is_reduce_only: bool = False,
        is_close:      bool  = False,
        text:          str   = "t-scalpyn",
    ) -> Dict[str, Any]:
        """
        POST /futures/usdt/orders.
        size > 0 → LONG (buy)
        size < 0 → SHORT (sell)
        size = 0 + is_close=True → close entire position
        """
        pair = self._normalize_symbol(contract)
        body: Dict[str, Any] = {
            "contract":      pair,
            "size":          size,
            "price":         price,
            "tif":           tif,
            "is_reduce_only": is_reduce_only,
            "is_close":      is_close,
            "text":          text,
        }
        return await self._request(
            "POST", f"/futures/{self.SETTLE}/orders",
            body=body, base_url=self._futures_url(), write=True,
        )

    async def set_leverage(
        self,
        contract:             str,
        leverage:             int,
        cross_leverage_limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        POST /futures/usdt/positions/{contract}/leverage?leverage={n}
        leverage=0 → cross margin; cross_leverage_limit sets the cross cap.
        """
        pair   = self._normalize_symbol(contract)
        params = {"leverage": str(leverage)}
        if cross_leverage_limit is not None:
            params["cross_leverage_limit"] = str(cross_leverage_limit)
        return await self._request(
            "POST", f"/futures/{self.SETTLE}/positions/{pair}/leverage",
            params=params, base_url=self._futures_url(), write=True,
        )

    async def close_position(
        self,
        contract: str,
        text:     str = "t-scalpyn-close",
    ) -> Dict[str, Any]:
        """Market-close the entire futures position (size=0, is_close=True)."""
        return await self.place_futures_order(
            contract=contract,
            size=0,
            price="0",
            tif="ioc",
            is_close=True,
            text=text,
        )

    async def create_price_trigger(
        self,
        contract:       str,
        trigger_price:  str,
        trigger_rule:   int,
        size:           int,
        is_close:       bool = False,
        is_reduce_only: bool = False,
        price_type:     int  = 1,
        expiration:     int  = 604800,
        text:           str  = "t-scalpyn-sl",
    ) -> Dict[str, Any]:
        """
        POST /futures/usdt/price_orders → create SL or TP trigger.

        trigger_rule:  1 = price >= trigger (TP for LONG / SL for SHORT)
                       2 = price <= trigger (SL for LONG / TP for SHORT)
        price_type:    0 = last price  |  1 = mark price (recommended for SL)
        size:          0 + is_close=True  → close full position
                       negative int      → partial close of LONG
        """
        pair = self._normalize_symbol(contract)
        body = {
            "initial": {
                "contract":      pair,
                "size":          size,
                "price":         "0",
                "tif":           "ioc",
                "is_close":      is_close,
                "is_reduce_only": is_reduce_only,
                "text":          text,
            },
            "trigger": {
                "strategy_type": 0,
                "price_type":    price_type,
                "price":         trigger_price,
                "rule":          trigger_rule,
                "expiration":    expiration,
            },
        }
        return await self._request(
            "POST", f"/futures/{self.SETTLE}/price_orders",
            body=body, base_url=self._futures_url(), write=True,
        )

    async def modify_price_trigger(
        self,
        order_id:      int,
        trigger_price: str,
    ) -> Dict[str, Any]:
        """
        PUT /futures/usdt/price_orders/amend/{order_id}.
        Used to move SL to breakeven after TP1 is hit.
        """
        body = {
            "order_id":     order_id,
            "trigger_price": trigger_price,
            "price":         "0",
            "size":          0,
        }
        return await self._request(
            "PUT", f"/futures/{self.SETTLE}/price_orders/amend/{order_id}",
            body=body, base_url=self._futures_url(), write=True,
        )

    async def cancel_price_trigger(self, order_id: int) -> Dict[str, Any]:
        """DELETE /futures/usdt/price_orders/{order_id}."""
        return await self._request(
            "DELETE", f"/futures/{self.SETTLE}/price_orders/{order_id}",
            base_url=self._futures_url(), write=True,
        )

    async def cancel_all_price_triggers(self, contract: str) -> List[Dict[str, Any]]:
        """DELETE /futures/usdt/price_orders?contract={contract} — cleanup after position closes."""
        pair = self._normalize_symbol(contract)
        return await self._request(
            "DELETE", f"/futures/{self.SETTLE}/price_orders",
            params={"contract": pair},
            base_url=self._futures_url(), write=True,
        )

    # =========================================================================
    # UNIVERSE DISCOVERY  (public endpoints — no auth required)
    # =========================================================================

    @staticmethod
    async def _public_get(url: str, params: Optional[Dict] = None) -> Any:
        """Unsigned GET for Gate.io public endpoints."""
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(
                url,
                params=params,
                headers={"Accept": "application/json"},
            )
        if r.status_code != 200:
            raise GateAPIError(r.status_code, "PUBLIC_ERROR", r.text[:300])
        return r.json()

    async def get_my_closed_spot_orders(
        self,
        days: int = 90,
        page: int = 1,
        limit: int = 100,
        from_timestamp: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        GET /spot/orders?status=finished — authenticated, returns user's closed spot orders.

        Returns list of dicts with keys:
            id, currency_pair, side ("buy"|"sell"), amount, price, avg_deal_price,
            filled_total (quote filled), left, fee, fee_currency,
            create_time, finish_time, status ("closed"|"cancelled").

        When from_timestamp is provided it is used directly; otherwise it is
        computed from days (relative to now).
        """
        from datetime import datetime, timezone, timedelta
        if from_timestamp is not None:
            start_ts = from_timestamp
        else:
            start_ts = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
        return await self._request(
            "GET",
            "/spot/orders",
            params={
                "status": "finished",
                "page": str(page),
                "limit": str(limit),
                "from": str(start_ts),
            },
        )

    async def get_my_spot_trades(
        self,
        currency_pair: Optional[str] = None,
        days: int = 90,
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        """
        GET /spot/my_trades — authenticated, returns user's personal trade fills.

        Returns list of dicts with keys:
            id, create_time, create_time_ms, order_id, currency_pair,
            side ("buy"|"sell"), amount, price, role ("maker"|"taker"),
            fee, fee_currency, point_fee, gt_fee.
        """
        from datetime import datetime, timezone, timedelta
        start_ts = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
        params: Dict[str, str] = {
            "limit": str(limit),
            "from": str(start_ts),
        }
        if currency_pair:
            params["currency_pair"] = self._normalize_symbol(currency_pair)
        return await self._request("GET", "/spot/my_trades", params=params)

    async def get_spot_trades(
        self,
        currency_pair: str,
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        """
        GET /spot/trades (public) — recent trades for a spot pair.

        Returns list of dicts with keys:
            id, create_time, create_time_ms, currency_pair, side ("buy"|"sell"),
            amount (base asset qty), price (USDT).
        """
        pair = self._normalize_symbol(currency_pair)
        return await self._public_get(
            f"{self.SPOT_BASE}/spot/trades",
            params={"currency_pair": pair, "limit": str(limit)},
        )

    async def list_spot_pairs(self) -> List[Dict[str, Any]]:
        """
        GET /spot/currency_pairs (public) → all spot trading pairs.

        Returns a list of dicts with keys:
            id, base, quote, fee, min_base_amount, min_quote_amount,
            amount_precision, precision, trade_status, sell_start, buy_start.
        """
        raw = await self._public_get(f"{self.SPOT_BASE}/spot/currency_pairs")
        return raw  # list already

    async def list_spot_tickers_public(self) -> List[Dict[str, Any]]:
        """
        GET /spot/tickers (public, no auth) → all spot tickers.

        More reliable than currency_pairs for discovery because tickers
        only exist for pairs that are actively traded.

        Returns a list of dicts with keys:
            currency_pair, last, lowest_ask, highest_bid,
            change_percentage, base_volume, quote_volume,
            high_24h, low_24h, etf_net_value, etf_pre_net_value,
            etf_pre_timestamp, etf_leverage.
        """
        raw = await self._public_get(f"{self.SPOT_BASE}/spot/tickers")
        return raw  # list already

    async def list_futures_contracts(self) -> List[Dict[str, Any]]:
        """
        GET /futures/usdt/contracts (public) → all USDT-margined perpetual contracts.

        Returns a list of dicts with keys:
            name, type, quanto_multiplier, leverage_min, leverage_max,
            mark_price, index_price, funding_rate, order_price_deviate, etc.
        """
        raw = await self._public_get(
            f"{self.FUTURES_BASE}/futures/{self.SETTLE}/contracts"
        )
        return raw  # list already

    async def search_pairs(
        self,
        query: str,
        market_type: str = "spot",
    ) -> List[Dict[str, Any]]:
        """
        Filter spot pairs or futures contracts by a search query (case-insensitive
        prefix / substring match on the pair id / contract name).

        Returns up to 10 matches sorted by relevance (prefix match first).
        Each result: { symbol, base, quote, market_type }
        """
        q = query.upper().strip()
        if not q:
            return []

        if market_type == "futures":
            pairs = await self.list_futures_contracts()
            results = [
                {
                    "symbol": p["name"],
                    "base": p["name"].replace(f"_{self.SETTLE.upper()}", ""),
                    "quote": self.SETTLE.upper(),
                    "market_type": "futures",
                }
                for p in pairs
                if q in p["name"].upper()
            ]
        else:
            pairs = await self.list_spot_pairs()
            results = [
                {
                    "symbol": p["id"],
                    "base": p.get("base", ""),
                    "quote": p.get("quote", ""),
                    "market_type": "spot",
                }
                for p in pairs
                if p.get("trade_status") in ("tradable", "buyable", "sellable")
                and q in p["id"].upper()
            ]

        # Sort: exact prefix match first, then alphabetical
        results.sort(key=lambda x: (not x["symbol"].startswith(q), x["symbol"]))
        return results[:10]
