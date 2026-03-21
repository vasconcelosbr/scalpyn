from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional


class BaseExchangeAdapter(ABC):
    """Unified interface for all exchange integrations."""

    def __init__(self, api_key: str, api_secret: str):
        pass

    # ── Legacy methods (keep for backward compatibility) ─────────────────────

    @abstractmethod
    async def fetch_ohlcv(self, symbol: str, timeframe: str) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    async def fetch_funding_rate(self, symbol: str) -> float:
        pass

    @abstractmethod
    async def create_order(
        self, symbol: str, side: str, order_type: str,
        quantity: float, price: Optional[float] = None
    ) -> Dict[str, Any]:
        pass

    @abstractmethod
    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        pass

    @abstractmethod
    async def get_balances(self) -> Dict[str, float]:
        pass

    # ── Account ───────────────────────────────────────────────────────────────

    @abstractmethod
    async def get_spot_balance(self) -> Dict[str, Any]:
        """GET /spot/accounts → USDT available + locked."""
        pass

    @abstractmethod
    async def get_futures_balance(self) -> Dict[str, Any]:
        """GET /futures/{settle}/accounts → margin available, equity."""
        pass

    @abstractmethod
    async def transfer_between_accounts(
        self, currency: str, from_account: str, to_account: str, amount: str
    ) -> Dict[str, Any]:
        """POST /wallet/transfers → move funds spot ↔ futures."""
        pass

    # ── Market data ───────────────────────────────────────────────────────────

    @abstractmethod
    async def get_tickers(
        self, symbols: Optional[List[str]] = None, market: str = "spot"
    ) -> List[Dict[str, Any]]:
        """GET /spot/tickers or /futures/{settle}/tickers."""
        pass

    @abstractmethod
    async def get_orderbook(
        self, symbol: str, market: str = "spot", depth: int = 20
    ) -> Dict[str, Any]:
        """GET /spot/order_book or /futures/{settle}/order_book."""
        pass

    @abstractmethod
    async def get_klines(
        self, symbol: str, interval: str = "1h",
        limit: int = 200, market: str = "spot"
    ) -> List[Dict[str, Any]]:
        """GET /spot/candlesticks or /futures/{settle}/candlesticks."""
        pass

    @abstractmethod
    async def get_contract_info(self, contract: str) -> Dict[str, Any]:
        """GET /futures/{settle}/contracts/{contract} → leverage_max, fees, limits."""
        pass

    @abstractmethod
    async def get_contract_stats(
        self, contract: str, interval: str = "5m", limit: int = 1
    ) -> List[Dict[str, Any]]:
        """GET /futures/{settle}/contract_stats → OI, long/short ratio."""
        pass

    # ── Spot trading ─────────────────────────────────────────────────────────

    @abstractmethod
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
        """POST /spot/orders."""
        pass

    @abstractmethod
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
        """POST /spot/price_orders → TP trigger for spot positions."""
        pass

    # ── Futures trading ───────────────────────────────────────────────────────

    @abstractmethod
    async def get_futures_position(self, contract: str) -> Dict[str, Any]:
        """GET /futures/{settle}/positions/{contract}."""
        pass

    @abstractmethod
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
        """POST /futures/{settle}/orders. size>0=LONG, size<0=SHORT, size=0+is_close=close all."""
        pass

    @abstractmethod
    async def set_leverage(
        self, contract: str, leverage: int, cross_leverage_limit: Optional[int] = None
    ) -> Dict[str, Any]:
        """POST /futures/{settle}/positions/{contract}/leverage."""
        pass

    @abstractmethod
    async def close_position(self, contract: str, text: str = "t-scalpyn-close") -> Dict[str, Any]:
        """Close entire position via POST /futures/{settle}/orders with is_close=true."""
        pass

    @abstractmethod
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
        """POST /futures/{settle}/price_orders → SL / TP triggers."""
        pass

    @abstractmethod
    async def modify_price_trigger(
        self, order_id: int, trigger_price: str
    ) -> Dict[str, Any]:
        """PUT /futures/{settle}/price_orders/amend/{order_id} → move SL to BE."""
        pass

    @abstractmethod
    async def cancel_price_trigger(self, order_id: int) -> Dict[str, Any]:
        """DELETE /futures/{settle}/price_orders/{order_id}."""
        pass

    @abstractmethod
    async def cancel_all_price_triggers(self, contract: str) -> List[Dict[str, Any]]:
        """DELETE /futures/{settle}/price_orders?contract={contract}."""
        pass
