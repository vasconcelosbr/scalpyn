"""Market Catalyst Radar (mdatahub) — buy-pressure feed client."""
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# 2026-09-24: endpoint history for the PUMP pool radar feed --
# minute-signals (quant-feed-pro redirect) -> top-assets (quant-feed-pro
# redirect) -> top-assets (direct mdatahub host) -> minute-signals (direct
# mdatahub host, current). Direct host skips the quant-feed-pro.lovable.app
# redirect hop. Same API key, same request/response shape throughout.
# follow_redirects stays on as a defensive default in case the direct host
# ever redirects again.
RADAR_TOP_ASSETS_URL = "https://mdatahub.scalpyn.com/api/public/radar/v1/buy-pressure/minute-signals"


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
