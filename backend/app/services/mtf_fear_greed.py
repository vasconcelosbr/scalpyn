"""MTF_CMC_DISPLAY_ONLY: CoinMarketCap sentiment, never an evaluation input."""
import asyncio
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import time

import httpx

from .redis_client import get_async_redis

CMC_FEAR_GREED_URL = 'https://pro-api.coinmarketcap.com/v3/fear-and-greed/latest'
CLASSIFICATIONS = {label.casefold(): label for label in ('Extreme Fear', 'Fear', 'Neutral', 'Greed', 'Extreme Greed')}


def reading(data, now):
    """Retain source classification and source time, not response/cache time."""
    if not isinstance(data, dict):
        raise ValueError('INVALID_DATA')
    value = data.get('value')
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 100:
        raise ValueError('INVALID_VALUE')
    # CMC_CLASSIFICATION_CASE: live API uses "Extreme fear", docs use "Extreme Fear".
    raw_classification = data.get('value_classification')
    classification = CLASSIFICATIONS.get(raw_classification.casefold()) if isinstance(raw_classification, str) else None
    if classification is None:
        raise ValueError('INVALID_CLASSIFICATION')
    at = datetime.fromisoformat(str(data.get('update_time')).replace('Z', '+00:00'))
    if at.tzinfo is None or at.timestamp() > now:
        raise ValueError('INVALID_SOURCE_TIME')
    return {'value': value, 'classification': classification,
            'update_time': at.astimezone(timezone.utc).isoformat()}


def snapshot(entry, cfg, *, now, reason=None):
    result = {'source': 'CoinMarketCap', 'status': 'UNAVAILABLE', 'reason': reason,
              'value': None, 'classification': None, 'update_time': None, 'fetched_at': None}
    if not entry or not entry.get('data'):
        return {**result, 'reason': reason or (entry or {}).get('error') or 'NO_DATA'}
    try:
        data = reading(entry['data'], now)
    except (ValueError, TypeError, OverflowError):
        return {**result, 'reason': 'INVALID_DATA'}
    age = now - datetime.fromisoformat(data['update_time']).timestamp()
    error = reason or entry.get('error')
    return {**result, **data, 'fetched_at': entry.get('fetched_at'),
            'status': 'STALE' if error or age > cfg.max_age_seconds else 'READY',
            'reason': error or ('SOURCE_STALE' if age > cfg.max_age_seconds else None)}


async def fetch_reading(key):
    async with httpx.AsyncClient(timeout=8.0) as client:
        response = await client.get(CMC_FEAR_GREED_URL, headers={'X-CMC_PRO_API_KEY': key, 'Accept': 'application/json'})
    if response.status_code == 429:
        return None, 'RATE_LIMITED'
    if response.status_code in (401, 403):
        return None, 'KEY_OR_PLAN_REJECTED'
    response.raise_for_status()
    payload = response.json()
    if str(payload.get('status', {}).get('error_code', 0)) != '0':
        return None, 'PROVIDER_ERROR'
    data = reading(payload.get('data'), time.time())
    # Store the same shape validated by reading(), without provider credentials.
    return {'value': data['value'], 'value_classification': data['classification'], 'update_time': data['update_time']}, None


async def get_snapshot(uid, key, cfg):
    if cfg is None or not cfg.enabled:
        return {**snapshot(None, cfg, now=time.time()), 'status': 'DISABLED', 'reason': None}
    if not key:
        return {**snapshot(None, cfg, now=time.time()), 'status': 'NOT_CONFIGURED', 'reason': 'KEY_NOT_CONFIGURED'}
    # Per-user cache and credential fingerprint prevent cross-account reuse and
    # make replacing/removing a key effective immediately. Never log the key.
    # New parser version must not reuse failures cached by case-sensitive parsing.
    cache_key = f'mtf:cmc:fear-greed:v2:{uid}:{sha256(key.encode()).hexdigest()}'
    entry = None
    try:
        async with asyncio.timeout(20):
            redis = await get_async_redis()
            if redis is None:
                return snapshot(None, cfg, now=time.time(), reason='CACHE_UNAVAILABLE')
            async def cached():
                raw = await redis.get(cache_key)
                return json.loads(raw) if raw else None
            entry = await cached()
            if entry and 0 <= time.time() - entry['checked_at'] < cfg.refresh_seconds:
                return snapshot(entry, cfg, now=time.time())
            # Distributed lock, not one lock per browser or per API process.
            lock = redis.lock(cache_key + ':lock', timeout=30, blocking_timeout=10)
            if not await lock.acquire():
                return snapshot(entry, cfg, now=time.time(), reason='REFRESH_IN_PROGRESS')
            try:
                entry = await cached()
                if entry and 0 <= time.time() - entry['checked_at'] < cfg.refresh_seconds:
                    return snapshot(entry, cfg, now=time.time())
                try:
                    data, error = await fetch_reading(key)
                except (httpx.HTTPError, ValueError, TypeError, OverflowError, AttributeError):
                    data, error = None, 'PROVIDER_UNAVAILABLE'
                checked = time.time()
                entry = {'data': data or (entry or {}).get('data'), 'error': error, 'checked_at': checked,
                         'fetched_at': datetime.fromtimestamp(checked, timezone.utc).isoformat() if data else (entry or {}).get('fetched_at')}
                await redis.set(cache_key, json.dumps(entry, allow_nan=False), ex=2 * max(cfg.max_age_seconds, cfg.refresh_seconds))
                return snapshot(entry, cfg, now=checked)
            finally:
                await lock.release()
    except Exception:
        # Fail closed when caching is unavailable; never fan out requests to CMC.
        return snapshot(entry, cfg, now=time.time(), reason='CACHE_UNAVAILABLE')
