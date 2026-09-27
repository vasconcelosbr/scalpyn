"""Latest scanner flow observation; display-only, never execution authority."""
import asyncio
import json
import logging

from .redis_client import get_async_redis

logger = logging.getLogger(__name__)


def _key(user_id, watchlist_id, symbol):
    return f"l3:flow-check:{user_id}:{watchlist_id}:{symbol}"


async def record_flow_check(*, user_id, watchlist_id, symbol, observation):
    if not user_id or not watchlist_id or not observation:
        return
    logger.info("[L3_FLOW_CHECK] user=%s watchlist=%s symbol=%s observation=%s",
                user_id, watchlist_id, symbol, json.dumps(observation))
    try:
        async with asyncio.timeout(1):
            client = await get_async_redis()
            if client is not None:
                await client.setex(
                    _key(user_id, watchlist_id, symbol),
                    observation["retention_seconds"], json.dumps(observation),
                )
    except Exception as exc:
        logger.warning("[L3_FLOW_CHECK] diagnostic write unavailable: %s", type(exc).__name__)


async def load_flow_checks(*, user_id, watchlist_id, symbols):
    if not symbols:
        return {}
    try:
        async with asyncio.timeout(1):
            client = await get_async_redis()
            if client is None:
                return {}
            values = await client.mget([_key(user_id, watchlist_id, s) for s in symbols])
        result = {}
        for symbol, raw in zip(symbols, values):
            if raw:
                try:
                    value = json.loads(raw)
                    if isinstance(value, dict):
                        result[symbol] = value
                except (ValueError, TypeError):
                    continue
        return result
    except Exception:
        return {}
