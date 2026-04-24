"""Market Data Service — centralized collection from exchanges into TimescaleDB."""

import asyncio
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
import pandas as pd

from ..exchange_adapters.binance_adapter import BinanceAdapter
from ..utils.gate_market_data import parse_gate_spot_candle
from ..utils.symbol_filters import is_excluded_asset, is_leveraged_base

logger = logging.getLogger(__name__)

GATE_SPOT_URL = "https://api.gateio.ws/api/v4/spot/candlesticks"
GATE_TICKERS_URL = "https://api.gateio.ws/api/v4/spot/tickers"
GATE_ORDERBOOK_URL = "https://api.gateio.ws/api/v4/spot/order_book"
GATE_FUNDING_URL = "https://api.gateio.ws/api/v4/futures/usdt/funding_rate"


@dataclass
class MarketDataNormalized:
    symbol: str
    price: Optional[float] = None
    volume_base: Optional[float] = None
    volume_quote: Optional[float] = None
    orderbook_depth: Optional[float] = None
    spread_pct: Optional[float] = None
    taker_buy_volume: Optional[float] = None
    taker_sell_volume: Optional[float] = None
    taker_ratio: Optional[float] = None
    volume_delta: Optional[float] = None
    source: str = "gate"
    confidence_score: Optional[float] = None
    source_map: Dict[str, str] = field(default_factory=dict)
    timestamp_map: Dict[str, datetime] = field(default_factory=dict)
    flags: List[str] = field(default_factory=list)
    snapshot_consistent: bool = True
    max_timestamp_diff_seconds: Optional[float] = None

    def to_indicator_payload(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "market_data_symbol": self.symbol,
            "market_data_source": self.source,
            "market_data_confidence": self.confidence_score,
            "snapshot_consistent": self.snapshot_consistent,
        }
        if self.max_timestamp_diff_seconds is not None:
            payload["max_timestamp_diff_seconds"] = round(float(self.max_timestamp_diff_seconds), 6)
        if self.price is not None:
            payload["price"] = round(float(self.price), 8)
        if self.volume_base is not None:
            payload["volume_24h_ticker_base"] = round(float(self.volume_base), 8)
        if self.volume_quote is not None:
            payload["volume_24h_ticker_usdt"] = round(float(self.volume_quote), 8)
        if self.orderbook_depth is not None:
            payload["orderbook_depth_usdt"] = round(float(self.orderbook_depth), 8)
        if self.spread_pct is not None:
            payload["spread_pct"] = round(float(self.spread_pct), 4)
        if self.taker_buy_volume is not None:
            payload["taker_buy_volume"] = round(float(self.taker_buy_volume), 8)
        if self.taker_sell_volume is not None:
            payload["taker_sell_volume"] = round(float(self.taker_sell_volume), 8)
        if self.taker_ratio is not None:
            payload["taker_ratio"] = round(float(self.taker_ratio), 8)
        if self.volume_delta is not None:
            payload["volume_delta_trades"] = round(float(self.volume_delta), 8)
        orderbook_source = self.source_map.get("orderbook_depth_usdt")
        if orderbook_source:
            payload["orderbook_depth_source"] = "binance" if orderbook_source.startswith("binance") else "gate"
        if self.flags:
            payload["data_quality_flags"] = sorted(set(self.flags))
        indicator_trace: Dict[str, Dict[str, Any]] = {}
        for indicator, source in self.source_map.items():
            value = self._indicator_value(indicator)
            if value is None:
                continue
            indicator_trace[indicator] = {
                "value": self._round_indicator_value(indicator, value),
                "source": source,
                "timestamp": self._serialize_timestamp(self.timestamp_map.get(indicator)),
            }
        if indicator_trace:
            payload["indicator_trace"] = indicator_trace
        return payload

    def _indicator_value(self, indicator: str) -> Optional[float]:
        mapping = {
            "price": self.price,
            "volume_24h_ticker_base": self.volume_base,
            "volume_24h_ticker_usdt": self.volume_quote,
            "orderbook_depth_usdt": self.orderbook_depth,
            "spread_pct": self.spread_pct,
            "taker_buy_volume": self.taker_buy_volume,
            "taker_sell_volume": self.taker_sell_volume,
            "taker_ratio": self.taker_ratio,
            "volume_delta_trades": self.volume_delta,
        }
        return mapping.get(indicator)

    @staticmethod
    def _round_indicator_value(indicator: str, value: float) -> float:
        digits = {
            "spread_pct": 4,
        }.get(indicator, 8)
        return round(float(value), digits)

    @staticmethod
    def _serialize_timestamp(timestamp: Optional[datetime]) -> Optional[str]:
        if timestamp is None:
            return None
        return timestamp.astimezone(timezone.utc).isoformat()


def _is_etf_pair(currency_pair: str) -> bool:
    """Return True if the pair is a leveraged/ETF token (e.g. BTC3L_USDT, BTCUP_USDT)."""
    base = currency_pair.split("_")[0]
    return is_leveraged_base(base)


class MarketDataService:
    """Centralized market data collection. Runs as master process (not per-user)."""

    def __init__(self):
        self._binance = BinanceAdapter()
        self._cache: Dict[str, tuple[float, Any]] = {}

    @staticmethod
    def _fallback_config() -> Dict[str, Any]:
        from .seed_service import DEFAULT_INDICATORS

        return DEFAULT_INDICATORS.get("market_data_fallback", {})

    @staticmethod
    def normalize_symbol(symbol: str) -> str:
        raw = str(symbol or "").upper().strip().replace("-", "/").replace("_", "/")
        if "/" in raw:
            base, quote = raw.split("/", 1)
            return f"{base}/{quote}"
        if raw.endswith("USDT"):
            return f"{raw[:-4]}/USDT"
        return raw

    @classmethod
    def to_gate_symbol(cls, symbol: str) -> str:
        return cls.normalize_symbol(symbol).replace("/", "_")

    @classmethod
    def to_binance_symbol(cls, symbol: str) -> str:
        return cls.normalize_symbol(symbol).replace("/", "")

    @staticmethod
    def _as_float(value: Any) -> Optional[float]:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(parsed):
            return None
        return parsed

    def is_valid_data(self, indicator: str, value: Any) -> bool:
        parsed = self._as_float(value)
        if parsed is None:
            return False
        if indicator in {"price", "volume_24h_ticker_base", "volume_24h_ticker_usdt", "orderbook_depth_usdt"}:
            return parsed > 0
        if indicator == "spread_pct":
            return 0 <= parsed < 100
        if indicator == "taker_ratio":
            ratio_min = float(self._fallback_config().get("taker_ratio_min", 0.2))
            ratio_max = float(self._fallback_config().get("taker_ratio_max", 5.0))
            return ratio_min <= parsed <= ratio_max
        if indicator in {"taker_buy_volume", "taker_sell_volume"}:
            return parsed >= 0
        if indicator == "volume_delta_trades":
            return True
        return True

    def _get_cached(self, cache_key: str, ttl_seconds: float) -> Any:
        self._purge_expired_cache()
        cached = self._cache.get(cache_key)
        if not cached:
            return None
        ts, payload = cached
        if (time.monotonic() - ts) > ttl_seconds:
            self._cache.pop(cache_key, None)
            return None
        return payload

    def _set_cache(self, cache_key: str, payload: Any) -> Any:
        self._purge_expired_cache()
        self._cache[cache_key] = (time.monotonic(), payload)
        max_entries = int(self._fallback_config().get("max_cache_entries", 1000))
        while len(self._cache) > max_entries:
            oldest_key = min(self._cache, key=lambda key: self._cache[key][0])
            self._cache.pop(oldest_key, None)
        return payload

    def _purge_expired_cache(self) -> None:
        cfg = self._fallback_config()
        ttl_by_prefix = {
            "binance:ticker:": float(cfg.get("ticker_cache_ttl_seconds", 5)),
            "binance:orderbook:": float(cfg.get("orderbook_cache_ttl_seconds", 5)),
            "binance:trades:": float(cfg.get("trades_cache_ttl_seconds", 1)),
        }
        now = time.monotonic()
        expired_keys = []
        for key, (created_at, _) in self._cache.items():
            ttl_seconds = next((ttl for prefix, ttl in ttl_by_prefix.items() if key.startswith(prefix)), None)
            if ttl_seconds is not None and (now - created_at) > ttl_seconds:
                expired_keys.append(key)
        for key in expired_keys:
            self._cache.pop(key, None)

    def _record_indicator(
        self,
        data: MarketDataNormalized,
        attribute: str,
        value: Any,
        source: str,
        indicator: str,
        reason: Optional[str] = None,
        captured_at: Optional[datetime] = None,
    ) -> None:
        if not self.is_valid_data(indicator, value):
            self._flag_invalid_value(data, indicator, value)
            return
        if getattr(data, attribute) is not None:
            return
        setattr(data, attribute, float(value))
        data.source_map[indicator] = source
        data.timestamp_map[indicator] = captured_at or self._snapshot_now()
        if self._canonical_source(source) != "gate":
            logger.info(
                "[DATA_SOURCE] symbol=%s indicator=%s source=%s reason=%s",
                data.symbol,
                indicator,
                f"{source}_fallback" if self._canonical_source(source) == "binance" else source,
                reason or "fallback_applied",
            )

    @staticmethod
    def _snapshot_now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _canonical_source(source: str) -> str:
        if not source:
            return "gate"
        if source.startswith("binance"):
            return "binance"
        if source.startswith("gate"):
            return "gate"
        return source

    @staticmethod
    def _flag_invalid_value(data: MarketDataNormalized, indicator: str, value: Any) -> None:
        parsed = MarketDataService._as_float(value)
        if parsed is None:
            data.flags.append(f"{indicator}_missing")
            return
        if indicator == "taker_ratio":
            data.flags.append("taker_ratio_out_of_range")
        elif indicator in {"volume_24h_ticker_base", "volume_24h_ticker_usdt"} and parsed <= 0:
            data.flags.append(f"{indicator}_non_positive")
        elif indicator == "orderbook_depth_usdt" and parsed <= 0:
            data.flags.append("orderbook_depth_non_positive")

    @staticmethod
    def _collapse_source(source_map: Dict[str, str]) -> str:
        unique_sources = {
            MarketDataService._canonical_source(source)
            for source in source_map.values()
            if source
        }
        if not unique_sources:
            return "gate"
        if len(unique_sources) == 1:
            return unique_sources.pop()
        return "mixed"

    def _confidence_score(self, source: str) -> float:
        scores = self._fallback_config().get("confidence_scores", {})
        return float(scores.get(source, scores.get("gate", 0.7)))

    async def _fetch_gate_ticker(self, symbol: str) -> Optional[Dict[str, Any]]:
        pair = self.to_gate_symbol(symbol)
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                resp = await client.get(GATE_TICKERS_URL, params={"currency_pair": pair})
                resp.raise_for_status()
                payload = resp.json()
                if isinstance(payload, list):
                    return payload[0] if payload else None
                return payload
        except Exception as exc:
            logger.debug("Failed to fetch Gate ticker for %s: %s", symbol, exc)
            return None

    async def _fetch_gate_orderbook(self, symbol: str, depth: int) -> Optional[Dict[str, Any]]:
        pair = self.to_gate_symbol(symbol)
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                resp = await client.get(
                    GATE_ORDERBOOK_URL,
                    params={"currency_pair": pair, "limit": depth, "with_id": "false"},
                )
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            logger.debug("Failed to fetch Gate orderbook for %s: %s", symbol, exc)
            return None

    async def _fetch_binance_ticker(self, symbol: str) -> Optional[Dict[str, Any]]:
        cache_key = f"binance:ticker:{self.to_binance_symbol(symbol)}"
        cached = self._get_cached(
            cache_key,
            float(self._fallback_config().get("ticker_cache_ttl_seconds", 5)),
        )
        if cached is not None:
            return cached
        try:
            payload = await self._binance.get_tickers(symbols=[self.to_binance_symbol(symbol)])
            return self._set_cache(cache_key, payload[0] if payload else None)
        except Exception as exc:
            logger.debug("Failed to fetch Binance ticker for %s: %s", symbol, exc)
            return None

    async def _fetch_binance_orderbook(self, symbol: str, depth: int) -> Optional[Dict[str, Any]]:
        cache_key = f"binance:orderbook:{self.to_binance_symbol(symbol)}:{depth}"
        cached = self._get_cached(
            cache_key,
            float(self._fallback_config().get("orderbook_cache_ttl_seconds", 5)),
        )
        if cached is not None:
            return cached
        try:
            payload = await self._binance.get_orderbook(self.to_binance_symbol(symbol), depth=depth)
            return self._set_cache(cache_key, payload)
        except Exception as exc:
            logger.debug("Failed to fetch Binance orderbook for %s: %s", symbol, exc)
            return None

    async def _fetch_binance_trades(self, symbol: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        limit = int(limit or self._fallback_config().get("binance_trade_limit", 500))
        cache_key = f"binance:trades:{self.to_binance_symbol(symbol)}:{limit}"
        cached = self._get_cached(
            cache_key,
            float(self._fallback_config().get("trades_cache_ttl_seconds", 1)),
        )
        if cached is not None:
            return cached
        try:
            payload = await self._binance.get_recent_trades(self.to_binance_symbol(symbol), limit=limit)
            return self._set_cache(cache_key, payload)
        except Exception as exc:
            logger.debug("Failed to fetch Binance trades for %s: %s", symbol, exc)
            return []

    def _extract_gate_ticker_metrics(self, ticker: Optional[Dict[str, Any]]) -> Dict[str, Optional[float]]:
        if not ticker:
            return {}
        return {
            "price": self._as_float(ticker.get("last")),
            "volume_24h_base": self._as_float(ticker.get("base_volume")),
            "volume_24h_usdt": self._as_float(ticker.get("quote_volume")),
            "spread_pct": self.compute_spread_from_ticker(ticker),
        }

    def _extract_binance_ticker_metrics(self, ticker: Optional[Dict[str, Any]]) -> Dict[str, Optional[float]]:
        if not ticker:
            return {}
        bid = self._as_float(ticker.get("bidPrice"))
        ask = self._as_float(ticker.get("askPrice"))
        spread_pct = None
        if bid and ask and ask > 0:
            spread_pct = round((ask - bid) / ask * 100, 4)
        return {
            "price": self._as_float(ticker.get("lastPrice")),
            "volume_24h_base": self._as_float(ticker.get("volume")),
            "volume_24h_usdt": self._as_float(ticker.get("quoteVolume")),
            "spread_pct": spread_pct,
        }

    def _extract_orderbook_metrics(self, book: Optional[Dict[str, Any]], depth: int) -> Dict[str, Optional[float]]:
        if not book:
            return {}
        bids = book.get("bids") or []
        asks = book.get("asks") or []
        if not bids or not asks:
            return {}
        try:
            best_bid = self._as_float(bids[0][0])
            best_ask = self._as_float(asks[0][0])
            if best_bid is None or best_ask is None or best_ask <= 0:
                return {}
            bid_depth = sum((self._as_float(price) or 0.0) * (self._as_float(qty) or 0.0) for price, qty in bids[:depth])
            ask_depth = sum((self._as_float(price) or 0.0) * (self._as_float(qty) or 0.0) for price, qty in asks[:depth])
            return {
                "spread_pct": round((best_ask - best_bid) / best_ask * 100, 4),
                "orderbook_depth_usdt": round(bid_depth + ask_depth, 8),
            }
        except Exception:
            return {}

    def _extract_taker_metrics(self, trades: List[Dict[str, Any]]) -> Dict[str, Optional[float]]:
        if not trades:
            return {}
        taker_buy_volume = 0.0
        taker_sell_volume = 0.0
        for trade in trades:
            qty = self._as_float(trade.get("qty"))
            if qty is None:
                continue
            if bool(trade.get("isBuyerMaker")):
                taker_sell_volume += qty
            else:
                taker_buy_volume += qty
        total_volume = taker_buy_volume + taker_sell_volume
        denominator_floor = float(self._fallback_config().get("taker_ratio_denominator_floor", 1e-9))
        ratio = taker_buy_volume / max(taker_sell_volume, denominator_floor) if total_volume > 0 else None
        payload: Dict[str, Any] = {
            "taker_buy_volume": taker_buy_volume,
            "taker_sell_volume": taker_sell_volume,
            "volume_delta_trades": taker_buy_volume - taker_sell_volume if total_volume > 0 else 0.0,
        }
        if ratio is not None and self.is_valid_data("taker_ratio", ratio):
            payload["taker_ratio"] = ratio
        else:
            payload["flags"] = ["taker_ratio_out_of_range"] if ratio is not None else []
        return payload

    async def fetch_ohlcv(
        self, symbol: str, timeframe: str = "1h", limit: int = 200,
    ) -> Optional[pd.DataFrame]:
        """Fetch OHLCV candles from Gate.io.

        Returns DataFrame with columns:
        [time, open, high, low, close, volume, quote_volume].
        """
        tf_map = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d"}
        gate_tf = tf_map.get(timeframe, "1h")
        pair = self.to_gate_symbol(symbol)

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

            rows = [parse_gate_spot_candle(candle) for candle in data]

            df = pd.DataFrame(rows)
            df = df.sort_values("time").reset_index(drop=True)
            return df

        except Exception as e:
            logger.error(f"Failed to fetch OHLCV for {symbol}: {e}")
            return None

    async def fetch_all_tickers(self) -> List[Dict[str, Any]]:
        """Fetch all USDT spot tickers from Gate.io (excluding leveraged tokens + stablecoins)."""
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(GATE_TICKERS_URL)
                resp.raise_for_status()
                tickers = resp.json()

            usdt_tickers = [
                t for t in tickers
                if isinstance(t, dict)
                and t.get("currency_pair", "").endswith("_USDT")
                and not is_excluded_asset(t.get("currency_pair", ""))
                and t.get("etf_net_value") is None
            ]
            return usdt_tickers
        except Exception as e:
            logger.error(f"Failed to fetch tickers: {e}")
            return []

    async def fetch_orderbook_metrics(self, symbol: str, depth: int = 10) -> Dict[str, Any]:
        """Fetch orderbook for a symbol and compute spread_pct and orderbook_depth_usdt.

        Returns:
            {
              "spread_pct": float (% difference between best ask and best bid),
              "orderbook_depth_usdt": float (total USDT value in top N levels of bid+ask),
            }
        Returns empty dict on failure.
        """
        depth = depth or int(self._fallback_config().get("orderbook_depth_levels", 10))
        normalized = await self.fetch_normalized_market_data(
            symbol,
            existing_data=None,
            depth=depth,
            include_taker=False,
        )
        payload = {}
        if normalized.spread_pct is not None:
            payload["spread_pct"] = round(float(normalized.spread_pct), 4)
        if normalized.orderbook_depth is not None:
            payload["orderbook_depth_usdt"] = round(float(normalized.orderbook_depth), 8)
        if payload:
            payload["market_data_source"] = self._canonical_source(
                normalized.source_map.get("orderbook_depth_usdt", normalized.source)
            )
            payload["market_data_confidence"] = normalized.confidence_score
            payload["orderbook_depth_source"] = (
                "binance"
                if str(normalized.source_map.get("orderbook_depth_usdt", "")).startswith("binance")
                else "gate"
            )
        return payload

    def compute_spread_from_ticker(self, ticker: Dict[str, Any]) -> Optional[float]:
        """Compute spread_pct from a ticker dict (highest_bid / lowest_ask fields)."""
        try:
            bid = float(ticker.get("highest_bid") or 0)
            ask = float(ticker.get("lowest_ask") or 0)
            if ask > 0 and bid > 0:
                return round((ask - bid) / ask * 100, 4)
        except (TypeError, ValueError):
            pass
        return None

    async def fetch_normalized_market_data(
        self,
        symbol: str,
        existing_data: Optional[Dict[str, Any]] = None,
        depth: int = 10,
        include_taker: bool = True,
    ) -> MarketDataNormalized:
        depth = depth or int(self._fallback_config().get("orderbook_depth_levels", 10))
        normalized_symbol = self.normalize_symbol(symbol)
        normalized = MarketDataNormalized(symbol=normalized_symbol)
        _ = existing_data
        trade_limit = int(self._fallback_config().get("binance_trade_limit", 500))

        async def _fetch_with_timestamp(coro):
            payload = await coro
            return payload, self._snapshot_now()

        coroutines = [
            _fetch_with_timestamp(self._fetch_gate_ticker(normalized_symbol)),
            _fetch_with_timestamp(self._fetch_gate_orderbook(normalized_symbol, depth)),
            _fetch_with_timestamp(self._fetch_binance_orderbook(normalized_symbol, depth)),
        ]
        if include_taker:
            coroutines.append(_fetch_with_timestamp(self._fetch_binance_trades(normalized_symbol, trade_limit)))

        results = await asyncio.gather(*coroutines)
        gate_ticker, gate_ticker_ts = results[0]
        gate_orderbook, gate_orderbook_ts = results[1]
        binance_orderbook, binance_orderbook_ts = results[2]
        trades, trades_ts = results[3] if include_taker else ([], None)

        gate_metrics = self._extract_gate_ticker_metrics(gate_ticker)
        self._record_indicator(normalized, "price", gate_metrics.get("price"), "gate_ticker", "price", captured_at=gate_ticker_ts)
        self._record_indicator(
            normalized,
            "volume_base",
            gate_metrics.get("volume_24h_base"),
            "gate_ticker",
            "volume_24h_ticker_base",
            captured_at=gate_ticker_ts,
        )
        self._record_indicator(
            normalized,
            "volume_quote",
            gate_metrics.get("volume_24h_usdt"),
            "gate_ticker",
            "volume_24h_ticker_usdt",
            captured_at=gate_ticker_ts,
        )
        self._record_indicator(
            normalized,
            "spread_pct",
            gate_metrics.get("spread_pct"),
            "gate_ticker",
            "spread_pct",
            captured_at=gate_ticker_ts,
        )

        gate_book_metrics = self._extract_orderbook_metrics(gate_orderbook, depth)
        self._record_indicator(
            normalized,
            "orderbook_depth",
            gate_book_metrics.get("orderbook_depth_usdt"),
            "gate_orderbook",
            "orderbook_depth_usdt",
            captured_at=gate_orderbook_ts,
        )
        self._record_indicator(
            normalized,
            "spread_pct",
            gate_book_metrics.get("spread_pct"),
            "gate_orderbook",
            "spread_pct",
            captured_at=gate_orderbook_ts,
        )

        if not self.is_valid_data("orderbook_depth_usdt", normalized.orderbook_depth):
            binance_book_metrics = self._extract_orderbook_metrics(binance_orderbook, depth)
            self._record_indicator(
                normalized,
                "orderbook_depth",
                binance_book_metrics.get("orderbook_depth_usdt"),
                "binance_orderbook",
                "orderbook_depth_usdt",
                reason="missing_from_gate",
                captured_at=binance_orderbook_ts,
            )
            self._record_indicator(
                normalized,
                "spread_pct",
                binance_book_metrics.get("spread_pct"),
                "binance_orderbook",
                "spread_pct",
                reason="missing_from_gate",
                captured_at=binance_orderbook_ts,
            )

        if include_taker:
            taker_metrics = self._extract_taker_metrics(trades)
            normalized.flags.extend(taker_metrics.get("flags", []))
            self._record_indicator(
                normalized,
                "taker_buy_volume",
                taker_metrics.get("taker_buy_volume"),
                "binance_trade",
                "taker_buy_volume",
                reason="unavailable_on_gate_spot",
                captured_at=trades_ts,
            )
            self._record_indicator(
                normalized,
                "taker_sell_volume",
                taker_metrics.get("taker_sell_volume"),
                "binance_trade",
                "taker_sell_volume",
                reason="unavailable_on_gate_spot",
                captured_at=trades_ts,
            )
            self._record_indicator(
                normalized,
                "taker_ratio",
                taker_metrics.get("taker_ratio"),
                "binance_trade",
                "taker_ratio",
                reason="unavailable_on_gate_spot",
                captured_at=trades_ts,
            )
            self._record_indicator(
                normalized,
                "volume_delta",
                taker_metrics.get("volume_delta_trades"),
                "binance_trade",
                "volume_delta_trades",
                reason="unavailable_on_gate_spot",
                captured_at=trades_ts,
            )

        self._enforce_snapshot_consistency(normalized)

        normalized.source = self._collapse_source(normalized.source_map)
        normalized.confidence_score = self._confidence_score(normalized.source)
        return normalized

    def _enforce_snapshot_consistency(self, data: MarketDataNormalized) -> None:
        timestamps = [timestamp for timestamp in data.timestamp_map.values() if timestamp is not None]
        if len(timestamps) < 2:
            return
        max_diff_seconds = (
            max(timestamps).timestamp() - min(timestamps).timestamp()
        )
        data.max_timestamp_diff_seconds = max_diff_seconds
        threshold = float(self._fallback_config().get("max_timestamp_diff_seconds", 2))
        if max_diff_seconds >= threshold:
            data.snapshot_consistent = False
            data.flags.append("market_data_snapshot_inconsistent")

    async def fetch_indicator_fallbacks(
        self,
        symbol: str,
        existing_data: Optional[Dict[str, Any]] = None,
        depth: int = 10,
    ) -> Dict[str, Any]:
        return (
            await self.fetch_normalized_market_data(
                symbol,
                existing_data=existing_data,
                depth=depth,
                include_taker=True,
            )
        ).to_indicator_payload()

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
            if not pair.endswith("_USDT"):
                continue
            vol = float(ticker.get("quote_volume", 0) or 0)
            # fetch_all_tickers already filters leveraged + stablecoins
            if vol >= min_volume:
                symbols.append(pair)  # keep BTC_USDT format (with underscore)
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
