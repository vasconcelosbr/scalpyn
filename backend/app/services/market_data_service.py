"""Market Data Service — centralized collection from exchanges into TimescaleDB."""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

import httpx
import pandas as pd

logger = logging.getLogger(__name__)

GATE_SPOT_URL = "https://api.gateio.ws/api/v4/spot/candlesticks"
GATE_TICKERS_URL = "https://api.gateio.ws/api/v4/spot/tickers"
GATE_FUNDING_URL = "https://api.gateio.ws/api/v4/futures/usdt/funding_rate"


class MarketDataService:
    """Centralized market data collection. Runs as master process (not per-user)."""

    async def fetch_ohlcv(
        self, symbol: str, timeframe: str = "1h", limit: int = 200,
    ) -> Optional[pd.DataFrame]:
        """Fetch OHLCV candles from Gate.io.

        Returns DataFrame with columns: [time, open, high, low, close, volume].
        """
        tf_map = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d"}
        gate_tf = tf_map.get(timeframe, "1h")
        pair = symbol.replace("USDT", "_USDT") if "_" not in symbol else symbol

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(GATE_SPOT_URL, params={
                    "currency_pair": pair,
                    "interval": gate_tf,
                    "limit": limit,
                })
                resp.raise_for_status()
                data = resp.json()

            if not data:
                return None

            # Gate.io format: [timestamp, volume, close, high, low, open, ...]
            rows = []
            for candle in data:
                rows.append({
                    "time": datetime.fromtimestamp(int(candle[0]), tz=timezone.utc),
                    "volume": float(candle[1]),
                    "close": float(candle[2]),
                    "high": float(candle[3]),
                    "low": float(candle[4]),
                    "open": float(candle[5]),
                })

            df = pd.DataFrame(rows)
            df = df.sort_values("time").reset_index(drop=True)
            return df

        except Exception as e:
            logger.error(f"Failed to fetch OHLCV for {symbol}: {e}")
            return None

    async def fetch_all_tickers(self) -> List[Dict[str, Any]]:
        """Fetch all USDT spot tickers from Gate.io."""
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(GATE_TICKERS_URL)
                resp.raise_for_status()
                tickers = resp.json()

            usdt_tickers = [
                t for t in tickers
                if isinstance(t, dict) and t.get("currency_pair", "").endswith("_USDT")
            ]
            return usdt_tickers
        except Exception as e:
            logger.error(f"Failed to fetch tickers: {e}")
            return []

    async def fetch_funding_rates(self) -> List[Dict[str, Any]]:
        """Fetch funding rates for USDT perpetual futures."""
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(GATE_FUNDING_URL)
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            logger.error(f"Failed to fetch funding rates: {e}")
            return []

    async def get_universe_symbols(self, universe_config: Dict[str, Any]) -> List[str]:
        """Filter symbols based on universe configuration."""
        min_volume = universe_config.get("min_volume_24h", 5_000_000)
        max_assets = universe_config.get("max_assets", 100)

        tickers = await self.fetch_all_tickers()

        # Sort by 24h volume descending
        tickers.sort(key=lambda x: float(x.get("quote_volume", 0) or 0), reverse=True)

        symbols = []
        for ticker in tickers:
            pair = ticker.get("currency_pair", "")
            vol = float(ticker.get("quote_volume", 0) or 0)
            if vol >= min_volume:
                symbol = pair.replace("_", "")  # BTC_USDT -> BTCUSDT
                symbols.append(symbol)
            if len(symbols) >= max_assets:
                break

        return symbols


    async def get_market_metadata(
        self,
        min_volume: float = 0,
        min_market_cap: float = 0,
        symbols: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Fetch market metadata (price, volume, market_cap) for USDT pairs from Gate.io.
        Optionally filter by min_volume, min_market_cap, or a specific symbol list.
        Returns list of dicts with symbol, price, volume_24h, market_cap, change_24h_pct.
        """
        tickers = await self.fetch_all_tickers()
        result = []
        for t in tickers:
            pair = t.get("currency_pair", "")
            symbol = pair.replace("_USDT", "USDT")
            if symbols and symbol not in symbols:
                continue
            volume = float(t.get("quote_volume", 0) or 0)
            if volume < min_volume:
                continue
            price = float(t.get("last", 0) or 0)
            change_pct = float(t.get("change_percentage", 0) or 0)
            result.append({
                "symbol": symbol,
                "price": price,
                "volume_24h": volume,
                "market_cap": 0,  # Gate.io tickers don't include market_cap
                "change_24h_pct": change_pct,
            })
        return result


market_data_service = MarketDataService()
