"""Request-bound, closed Gate candles for profile H4 observation inputs."""
from datetime import datetime, timedelta, timezone
import json
import math
import logging

from .feature_engine import FeatureEngine
from .indicator_calculation_identity import calculation_identities
from .indicator_classifier import timeframe_semantics

logger = logging.getLogger(__name__)


def closed_4h_candidates(df, config, identity, symbol, *, now):
    from ..tasks.compute_indicators import _derive_min_candles

    duration = timedelta(hours=4)
    if df is None or df.empty or df.attrs.get("exchange") != "gate.io" or "is_closed" not in df:
        return []
    series = df.loc[(df["is_closed"] == True) & (df["time"] + duration <= now)].sort_values("time")
    required = max(_derive_min_candles(config, "4h"), max(config.get("ema", {}).get("periods") or [0]))
    if len(series) < required:
        return []
    series = series.tail(required)
    if any(b - a != duration for a, b in zip(series["time"], series["time"].iloc[1:])):
        return []
    closed_at = series.iloc[-1]["time"].to_pydatetime() + duration
    if not 0 <= (now - closed_at).total_seconds() < duration.total_seconds():
        return []
    values = FeatureEngine(config).calculate(series, timeframe="4h", as_of=now)
    calculations = calculation_identities(config, values)
    candidates = []
    for name, value in values.items():
        if timeframe_semantics(name) != "CANDLE_TIMEFRAME":
            continue
        if name.startswith(("recent_high_", "recent_low_", "price_change_")) or name == "breakout_distance_pct":
            continue
        if value is None or isinstance(value, (float, int)) and not math.isfinite(value):
            continue
        candidates.append({
            "indicator": name, "actual": value, "source": "ohlcv", "timeframe": "4h",
            "source_provider": "gate.io", "provider_policy_id": "spot_gate_closed_ohlcv_v1",
            "candle_policy": "CLOSED_ONLY", "candle_closed": True, "request_bound": True,
            "market_scope": {"exchange": "gate_io", "market_type": "spot", "normalized_symbol": symbol},
            "source_timestamp": closed_at.isoformat(), "computed_at": now.isoformat(), "available_at": now.isoformat(),
            "age_seconds": (now - closed_at).total_seconds(), "stale": False,
            "producer_version": "profile_closed_gate_4h_v1", "capture_contract_version": "gate_spot_candlesticks_v1",
            "sample_count": len(series), **identity, **calculations.get(name, {}),
        })
    return candidates


async def get_closed_profile_4h(db, symbols, *, user_id):
    from sqlalchemy import text
    from ..tasks.compute_indicators import _derive_min_candles
    from .market_data_service import market_data_service
    from .profile_runtime_config import canonical_hash
    from .redis_client import get_async_redis

    if not symbols or user_id is None:
        return {}
    rows = (await db.execute(text("""
        SELECT id, config_json FROM config_profiles
         WHERE user_id=:user_id AND pool_id IS NULL
           AND config_type='indicators' AND is_active=true
    """), {"user_id": user_id})).mappings().all()
    if len(rows) != 1 or not rows[0]["config_json"]:
        raise ValueError("PROFILE_INDICATOR_CONFIG_UNAVAILABLE")
    config = rows[0]["config_json"]
    identity = {"config_profile_id": str(rows[0]["id"]), "config_user_id": str(user_id), "config_hash": canonical_hash(config)}
    now = datetime.now(timezone.utc)
    seconds = int(timedelta(hours=4).total_seconds())
    bucket = int(now.timestamp()) // seconds
    limit = max(_derive_min_candles(config, "4h"), max(config.get("ema", {}).get("periods") or [0])) + 1
    redis = await get_async_redis()
    result = {}
    for symbol in sorted(set(symbols)):
        key = f"profile:closed:4h:{user_id}:{symbol}:{identity['config_hash']}:{bucket}"
        try:
            cached = await redis.get(key) if redis else None
        except Exception:
            logger.warning("H4 candle cache unavailable for %s", symbol)
            cached = None
        if cached:
            candidates = json.loads(cached)
            for candidate in candidates:
                candidate["age_seconds"] = (now - datetime.fromisoformat(candidate["source_timestamp"])).total_seconds()
        else:
            df = await market_data_service.fetch_ohlcv(symbol, timeframe="4h", limit=limit)
            candidates = closed_4h_candidates(df, config, identity, symbol, now=now)
            if candidates and redis:
                try:
                    await redis.set(key, json.dumps(candidates), ex=seconds - int(now.timestamp()) % seconds)
                except Exception:
                    logger.warning("H4 candle cache write unavailable for %s", symbol)
        result[symbol] = candidates
    return result
