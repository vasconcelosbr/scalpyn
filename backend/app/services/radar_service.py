"""Market Catalyst Radar (mdatahub) — buy-pressure feed client."""
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class RadarFeedUnavailable(ValueError):
    """A response cannot safely establish presence or absence of a signal."""


def validated_radar_assets(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise RadarFeedUnavailable("invalid_payload")
    meta = payload.get("meta")
    if not isinstance(meta, dict):
        raise RadarFeedUnavailable("missing_metadata")
    if meta.get("market_data_enabled") is not True:
        raise RadarFeedUnavailable("market_data_disabled")
    # The provider currently exposes PARTIAL when collection is degraded.
    # Do not assume an undocumented positive enum (e.g. FULL vs COMPLETE).
    coverage = meta.get("coverage_status")
    if not isinstance(coverage, str) or not coverage.strip() or coverage.upper() in {
        "PARTIAL", "UNAVAILABLE", "DEGRADED", "STALE", "ERROR", "DISABLED",
    }:
        raise RadarFeedUnavailable("incomplete_coverage")
    if meta.get("has_more") is not False:
        raise RadarFeedUnavailable("incomplete_page")
    if meta.get("source_provider") != "gate.io" or meta.get("market") != "spot":
        raise RadarFeedUnavailable("unexpected_market")
    assets = payload["data"]
    if any(not isinstance(a, dict) or not isinstance(a.get("pair"), str) for a in assets):
        raise RadarFeedUnavailable("invalid_asset")
    return assets

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

    Returns a validated complete ``data`` array — each entry already carries
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
    return validated_radar_assets(payload)
