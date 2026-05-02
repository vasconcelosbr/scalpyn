"""Robust compute pipeline.

Re-uses the existing ``FeatureEngine``, ``OrderFlowService`` and
``MarketDataService`` outputs without re-implementing any TA math, then wraps
each value into an :class:`IndicatorEnvelope` carrying source + timestamp.

The function exposed here is the *adapter*: it takes a flat indicator dict
already produced by the legacy pipeline and converts it into the envelope
shape. The full ``compute_indicators_robust(symbol, timeframe)`` async helper
is also provided for use in tests; it pulls indicators on demand via
``MarketDataService`` + ``FeatureEngine`` + ``OrderFlowService``.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional

from .envelope import DataSource, IndicatorEnvelope, wrap_indicator
from .metrics import observe_compute_duration

logger = logging.getLogger(__name__)


# Mapping from indicator key -> default DataSource when caller didn't tell us.
# Flow indicators come from order_flow (gate trades) or merged feed; candle TAs
# come from gate candles; orderbook quantities from gate orderbook.
_DEFAULT_SOURCE_BY_INDICATOR: Dict[str, DataSource] = {
    # candle-derived TAs
    "rsi": DataSource.GATE_CANDLES,
    "adx": DataSource.GATE_CANDLES,
    "adx_acceleration": DataSource.GATE_CANDLES,
    "di_plus": DataSource.GATE_CANDLES,
    "di_minus": DataSource.GATE_CANDLES,
    "ema5": DataSource.GATE_CANDLES,
    "ema9": DataSource.GATE_CANDLES,
    "ema21": DataSource.GATE_CANDLES,
    "ema50": DataSource.GATE_CANDLES,
    "ema200": DataSource.GATE_CANDLES,
    "ema9_distance_pct": DataSource.GATE_CANDLES,
    "atr": DataSource.GATE_CANDLES,
    "atr_pct": DataSource.GATE_CANDLES,
    "atr_percent": DataSource.GATE_CANDLES,
    "macd": DataSource.GATE_CANDLES,
    "macd_signal_line": DataSource.GATE_CANDLES,
    "macd_histogram": DataSource.GATE_CANDLES,
    "macd_histogram_pct": DataSource.GATE_CANDLES,
    "macd_histogram_prev": DataSource.GATE_CANDLES,
    "macd_histogram_slope": DataSource.GATE_CANDLES,
    "macd_histogram_mean_10": DataSource.GATE_CANDLES,
    "macd_histogram_std_10": DataSource.GATE_CANDLES,
    "macd_signal": DataSource.GATE_CANDLES,
    "vwap": DataSource.GATE_CANDLES,
    "vwap_distance_pct": DataSource.GATE_CANDLES,
    "stoch_k": DataSource.GATE_CANDLES,
    "stoch_d": DataSource.GATE_CANDLES,
    "obv": DataSource.GATE_CANDLES,
    "bb_upper": DataSource.GATE_CANDLES,
    "bb_middle": DataSource.GATE_CANDLES,
    "bb_lower": DataSource.GATE_CANDLES,
    "bb_width": DataSource.GATE_CANDLES,
    "psar": DataSource.GATE_CANDLES,
    "psar_trend": DataSource.GATE_CANDLES,
    "zscore": DataSource.GATE_CANDLES,
    "volume_spike": DataSource.GATE_CANDLES,
    "close": DataSource.GATE_CANDLES,
    "close_5m": DataSource.GATE_CANDLES,
    "price": DataSource.GATE_CANDLES,
    # flow / live data
    "taker_ratio": DataSource.GATE_TRADES,
    "buy_pressure": DataSource.GATE_TRADES,
    "taker_buy_volume": DataSource.GATE_TRADES,
    "taker_sell_volume": DataSource.GATE_TRADES,
    "volume_delta": DataSource.GATE_TRADES,
    # orderbook / ticker
    "spread_pct": DataSource.GATE_ORDERBOOK,
    "orderbook_depth_usdt": DataSource.GATE_ORDERBOOK,
    "volume_24h_base": DataSource.GATE_TICKER,
    "volume_24h_usdt": DataSource.GATE_TICKER,
    # derived booleans
    "ema9_gt_ema21": DataSource.DERIVED,
    "ema9_gt_ema50": DataSource.DERIVED,
    "ema50_gt_ema200": DataSource.DERIVED,
    "ema_full_alignment": DataSource.DERIVED,
}


def _coerce_source_string(raw: Optional[str]) -> DataSource:
    if not raw:
        return DataSource.UNKNOWN
    raw_l = raw.strip().lower()
    mapping = {
        "gate": DataSource.GATE_TRADES,
        "gate_io": DataSource.GATE_TRADES,
        "gate_io_trades": DataSource.GATE_TRADES,
        "binance": DataSource.BINANCE_TRADES,
        "binance_trades": DataSource.BINANCE_TRADES,
        "mixed": DataSource.MERGED,
        "merged": DataSource.MERGED,
        "candle": DataSource.GATE_CANDLES,
        "candles": DataSource.GATE_CANDLES,
    }
    if raw_l in mapping:
        return mapping[raw_l]
    try:
        return DataSource(raw_l)
    except ValueError:
        return DataSource.UNKNOWN


def envelope_indicators(
    symbol: str,
    indicators: Mapping[str, Any],
    *,
    timestamp: Optional[datetime] = None,
    source_timestamps: Optional[Mapping[str, datetime]] = None,
    flow_source_hint: Optional[str] = None,
) -> Dict[str, IndicatorEnvelope]:
    """Wrap a flat indicator dict into a name -> envelope mapping."""
    if timestamp is None:
        timestamp = datetime.now(timezone.utc)
    flow_src = _coerce_source_string(flow_source_hint) if flow_source_hint else None
    src_ts_map = dict(source_timestamps or {})

    out: Dict[str, IndicatorEnvelope] = {}
    for name, value in indicators.items():
        if not isinstance(name, str) or not name:
            continue
        if name.startswith("_"):
            continue
        if name in ("market_data_source", "market_data_symbol",
                    "market_data_confidence", "taker_source", "taker_window",
                    "taker_window_end"):
            continue
        if name.endswith("_timestamp"):
            continue

        if flow_src and name in {"taker_ratio", "buy_pressure",
                                  "taker_buy_volume", "taker_sell_volume",
                                  "volume_delta"}:
            source = flow_src
        else:
            source = _DEFAULT_SOURCE_BY_INDICATOR.get(name, DataSource.UNKNOWN)

        ts = src_ts_map.get(name, timestamp)
        env = wrap_indicator(
            name=name, value=value, source=source,
            timestamp=ts, now=timestamp,
        )
        out[name] = env
    return out


async def compute_indicators_robust(
    symbol: str,
    timeframe: str = "1h",
    *,
    db_session=None,
    user_id=None,
) -> Dict[str, IndicatorEnvelope]:
    """Pull live data and produce envelopes (used by tests / on-demand callers).

    Candle-derived approximations of taker_ratio / volume_delta are NOT
    supported: when the order-flow primary source is missing we drop those
    keys so the envelope marks them ``NO_DATA`` rather than producing a
    fake signal.
    """
    from ..feature_engine import FeatureEngine
    from ..market_data_service import MarketDataService
    from ..order_flow_service import get_order_flow_data
    from ..config_service import config_service
    from ..seed_service import DEFAULT_INDICATORS

    indicators_config = DEFAULT_INDICATORS
    if db_session is not None and user_id is not None:
        try:
            cfg = await config_service.get_config(db_session, "indicators", user_id)
            if cfg:
                indicators_config = cfg
        except Exception as exc:
            logger.warning(
                "[robust_indicators] config read failed for %s: %s — using defaults",
                symbol, exc,
            )

    fe = FeatureEngine(indicators_config)
    md_service = MarketDataService()

    started = time.perf_counter()
    try:
        df = await md_service.get_ohlcv_dataframe(symbol, timeframe=timeframe)
    except Exception as exc:
        logger.warning("[robust_indicators] OHLCV fetch failed for %s: %s", symbol, exc)
        df = None

    market_data: Dict[str, Any] = {}
    flow_source: Optional[str] = None
    flow = None
    try:
        flow = await get_order_flow_data(symbol)
        if flow:
            market_data.update(flow)
            flow_source = flow.get("taker_source")
    except Exception as exc:
        logger.debug("[robust_indicators] order flow fetch failed for %s: %s", symbol, exc)

    if df is None or df.empty:
        observe_compute_duration(symbol, "all", "missing_ohlcv", time.perf_counter() - started)
        return {}

    raw = fe.calculate(df, market_data=market_data or None)
    duration = time.perf_counter() - started
    observe_compute_duration(symbol, "all", "live", duration)

    if not flow:
        # Without primary order-flow data we MUST NOT emit candle
        # approximations of taker_ratio / volume_delta — drop the keys
        # so the envelope marks them NO_DATA.
        raw.pop("taker_ratio", None)
        raw.pop("volume_delta", None)

    return envelope_indicators(
        symbol,
        raw,
        flow_source_hint=flow_source,
    )


__all__ = [
    "compute_indicators_robust",
    "envelope_indicators",
]
