from abc import ABC, abstractmethod
from typing import List, Dict, Any

class BaseExchangeAdapter(ABC):
    """Unified interface for all exchange integrations."""

    def __init__(self, api_key: bytes, api_secret: bytes):
        pass

    @abstractmethod
    async def fetch_ohlcv(self, symbol: str, timeframe: str) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    async def fetch_funding_rate(self, symbol: str) -> float:
        pass

    @abstractmethod
    async def create_order(self, symbol: str, side: str, order_type: str, quantity: float, price: float = None) -> Dict[str, Any]:
        pass

    @abstractmethod
    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        pass

    @abstractmethod
    async def get_balances(self) -> Dict[str, float]:
        pass
