"""
Market Data Hub — async HTTP client.

Fetches macro/intermarket context from the MDH API and returns a validated
feature dict ready to be merged into the XGBoost inference feature vector.

Resilience contract (spec):
  - Timeout: 5 s per request
  - Retry: 1 additional attempt on timeout / 5xx
  - On any failure: returns MACRO_FEATURES_EMPTY with macro_context_available=False
  - NEVER blocks the main inference pipeline
"""
import asyncio
import logging
import os
import time
from typing import Any, Dict, Optional

import httpx

from .macro_features import (
    MACRO_FEATURES_EMPTY,
    extract_macro_features,
)

logger = logging.getLogger(__name__)

# ── Configuration (override via env) ─────────────────────────────────────────
MDH_BASE_URL = os.getenv(
    "MDH_BASE_URL",
    "https://project--ef76c935-3c72-4146-86a4-a67c746a5b41.lovable.app",
).rstrip("/")
MDH_API_KEY = os.getenv("MDH_API_KEY", "mdh_master_change_me")
MDH_TIMEOUT = float(os.getenv("MDH_TIMEOUT_S", "5.0"))
MDH_USER_AGENT = "Scalpyn-XGBoost/1.0"

# Shared headers for every request
_HEADERS = {
    "x-api-key": MDH_API_KEY,
    "User-Agent": MDH_USER_AGENT,
    "Accept": "application/json",
}

# Which endpoints to call and what key to store their response under
_ENDPOINTS: Dict[str, str] = {
    "indices":    "/api/v1/market/indices",
    "volatility": "/api/v1/market/volatility",
    "forex":      "/api/v1/market/forex",
    "bonds":      "/api/v1/market/bonds",
    "crypto_global": "/api/v1/crypto/global",
}


async def _get(client: httpx.AsyncClient, path: str, retries: int = 1) -> Optional[Any]:
    """GET with 1 retry on timeout / 5xx. Returns parsed JSON or None."""
    url = f"{MDH_BASE_URL}{path}"
    for attempt in range(retries + 1):
        try:
            resp = await client.get(url, timeout=MDH_TIMEOUT)
            if resp.status_code == 200:
                return resp.json()
            logger.warning("[MDH] %s → HTTP %d", path, resp.status_code)
        except httpx.TimeoutException:
            logger.warning("[MDH] %s → timeout (attempt %d/%d)", path, attempt + 1, retries + 1)
        except Exception as exc:
            logger.warning("[MDH] %s → %s", path, exc)
            break  # non-transient error — don't retry
    return None


async def fetch_macro_context() -> Dict[str, Any]:
    """
    Fetch all required macro endpoints concurrently and return a validated
    feature dict.

    Returns:
        Dict with keys matching MACRO_FEATURE_COLUMNS + macro_context_available.
        On any failure: MACRO_FEATURES_EMPTY (macro_context_available=False).
    """
    t0 = time.monotonic()
    raw: Dict[str, Any] = {}

    try:
        async with httpx.AsyncClient(headers=_HEADERS, follow_redirects=True) as client:
            # Fire all requests concurrently
            results = await asyncio.gather(
                *[_get(client, path) for path in _ENDPOINTS.values()],
                return_exceptions=True,
            )

        for key, result in zip(_ENDPOINTS.keys(), results):
            if isinstance(result, Exception):
                logger.warning("[MDH] %s → exception: %s", key, result)
                raw[key] = None
            else:
                raw[key] = result

        elapsed_ms = round((time.monotonic() - t0) * 1000)
        any_ok = any(v is not None for v in raw.values())

        if not any_ok:
            logger.warning("[MDH] All endpoints failed — macro_context_available=False (%dms)", elapsed_ms)
            return dict(MACRO_FEATURES_EMPTY)

        features = extract_macro_features(raw)
        available = features.get("macro_context_available", False)
        logger.info(
            "[MDH] macro_context_available=%s elapsed=%dms",
            available, elapsed_ms,
        )
        return features

    except Exception as exc:
        elapsed_ms = round((time.monotonic() - t0) * 1000)
        logger.warning("[MDH] Unexpected error after %dms: %s", elapsed_ms, exc)
        return dict(MACRO_FEATURES_EMPTY)
