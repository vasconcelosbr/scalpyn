"""Market Catalyst Radar (mdatahub) — buy-pressure feed client."""
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# quant-feed-pro.lovable.app 302-redirects to mdatahub.scalpyn.com; the
# client must follow it or every call silently returns an empty body.
# 2026-09-23: endpoint moved from .../buy-pressure/top-assets to
# .../buy-pressure/minute-signals (same API key, same request/response shape).
RADAR_TOP_ASSETS_URL = "https://quant-feed-pro.lovable.app/api/public/radar/v1/buy-pressure/minute-signals"


async def fetch_top_assets(api_key: str) -> list[dict[str, Any]]:
    """Fetch the current top buy-pressure assets from the Radar feed.

    Returns the raw ``data`` array as-is — each entry already carries
    ``pair`` in Gate.io ``BASE_USDT`` format (``meta.source_provider`` is
    ``"gate.io"``, ``meta.market`` is ``"spot"``), so no symbol construction
    is needed here.
    """
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        resp = await client.get(
            RADAR_TOP_ASSETS_URL,
            headers={"X-Radar-API-Key": api_key, "Accept": "application/json"},
        )
    resp.raise_for_status()
    payload = resp.json()
    return payload.get("data") or []
