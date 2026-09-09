"""User-scoped universe and explicitly reconstructed market-time observations."""

from copy import deepcopy
from datetime import timedelta
import hashlib
import inspect
import math

import pandas as pd
from sqlalchemy import select

from ..models.pool import Pool, PoolCoin
from ..models.pipeline_watchlist import PipelineWatchlist, PipelineWatchlistAsset
from .feature_engine import FeatureEngine

UNIVERSE_SOURCE = "user_pool_pipeline_v1"
TIMEFRAMES = {"1h": (3600, "L1"), "15m": (900, "L2"), "5m": (300, "L3")}
NON_OHLCV = frozenset({
    "volume_delta", "taker_ratio", "buy_pressure", "taker_buy_volume",
    "taker_sell_volume", "orderbook_pressure", "bid_ask_imbalance",
    "orderbook_depth_usdt", "spread_pct", "cvd", "cvd_slope",
})
ENGINE_VERSION = hashlib.sha256(inspect.getsource(FeatureEngine).replace("\r\n", "\n").encode()).hexdigest()


def normalize_symbol(symbol):
    return str(symbol or "").strip().upper().replace("/", "_").replace("-", "_")


async def load_user_universe(db, user_id):
    pools = (await db.execute(
        select(PoolCoin.symbol, Pool.id)
        .join(Pool, Pool.id == PoolCoin.pool_id)
        .where(Pool.user_id == user_id, Pool.is_active.is_(True),
               Pool.market_type == "spot", PoolCoin.is_active.is_(True),
               PoolCoin.market_type == "spot")
    )).all()
    pipeline = (await db.execute(
        select(PipelineWatchlistAsset.symbol, PipelineWatchlist.id, PipelineWatchlist.level)
        .join(PipelineWatchlist, PipelineWatchlist.id == PipelineWatchlistAsset.watchlist_id)
        .where(PipelineWatchlist.user_id == user_id, PipelineWatchlist.market_mode == "spot",
               PipelineWatchlist.level.in_(["POOL", "L1", "L2", "L3"]))
    )).all()
    members = {}
    for symbol, scope_id, level in [*( (s, p, "POOL") for s, p in pools), *pipeline]:
        symbol = normalize_symbol(symbol)
        if not symbol:
            continue
        member = members.setdefault(symbol, {"symbol": symbol, "memberships": []})
        member["memberships"].append({"level": level, "scope_id": str(scope_id)})
    return [members[symbol] for symbol in sorted(members)]


def closed_history(rows, at, timeframe):
    """A reconstruction uses market close, never the candle spanning the anchor."""
    eligible = sorted((r for r in rows if r.is_closed and r.close_time <= at), key=lambda r: r.open_time)
    # A gap invalidates earlier warm-up history; do not calculate through it.
    step = timedelta(seconds=TIMEFRAMES[timeframe][0])
    if not eligible or at - eligible[-1].close_time >= step:
        return []
    for index in range(len(eligible) - 1, 0, -1):
        if eligible[index].open_time - eligible[index - 1].open_time != step:
            return eligible[index:]
    return eligible


def reconstruct_indicators(rows, at, timeframe, indicators_config, context_candles=300):
    history = closed_history(rows, at, timeframe)[-context_candles:]
    if len(history) < 2 or not indicators_config:
        return {}, history
    config = deepcopy(indicators_config)
    # Never allow order-flow branches in an OHLCV-only reconstruction.
    for key in ("volume_delta", "taker_ratio"):
        config[key] = {**config.get(key, {}), "enabled": False}
    frame = pd.DataFrame([{
        "time": r.open_time, "open": float(r.open), "high": float(r.high),
        "low": float(r.low), "close": float(r.close),
        "volume": float(r.volume_base), "quote_volume": float(r.volume_quote or 0),
    } for r in history])
    values = FeatureEngine(config).calculate(frame, timeframe=timeframe, as_of=at)
    return {
        key: value for key, value in values.items()
        if key not in NON_OHLCV and isinstance(value, (int, float, bool))
        and math.isfinite(float(value))
    }, history
