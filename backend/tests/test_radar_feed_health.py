import httpx
import pytest

from app.services import radar_service
from app.services.radar_service import RadarFeedUnavailable, validated_radar_assets


def feed(assets=None, **metadata):
    # A complete-provider label is deliberately not an assumed protocol enum.
    return {"data": [] if assets is None else assets, "meta": {
        "market_data_enabled": True, "coverage_status": "complete-provider-label",
        "has_more": False, "source_provider": "gate.io", "market": "spot", **metadata,
    }}


def test_valid_empty_feed_is_a_selection_not_an_outage():
    assert validated_radar_assets(feed()) == []


def test_provider_disabled_empty_is_not_a_valid_absence():
    with pytest.raises(RadarFeedUnavailable, match="market_data_disabled"):
        validated_radar_assets(feed(market_data_enabled=False, coverage_status="PARTIAL"))


@pytest.mark.parametrize("metadata,reason", [
    ({"coverage_status": "PARTIAL"}, "incomplete_coverage"),
    ({"coverage_status": None}, "incomplete_coverage"),
    ({"market_data_enabled": "true"}, "market_data_disabled"),
    ({"has_more": True}, "incomplete_page"),
    ({"has_more": None}, "incomplete_page"),
    ({"market": "futures"}, "unexpected_market"),
    ({"source_provider": "other"}, "unexpected_market"),
])
def test_incomplete_feed_cannot_remove_members(metadata, reason):
    with pytest.raises(RadarFeedUnavailable, match=reason):
        validated_radar_assets(feed(**metadata))


@pytest.mark.parametrize("payload", [None, [], {}, {"data": None}, {"data": []}])
def test_invalid_payload_is_unavailable(payload):
    with pytest.raises(RadarFeedUnavailable):
        validated_radar_assets(payload)


def test_valid_signals_preserve_raw_pair():
    assets = [{"pair": "ADA_USDT", "value": 2}]
    assert validated_radar_assets(feed(assets)) == assets
    with pytest.raises(RadarFeedUnavailable, match="invalid_asset"):
        validated_radar_assets(feed([{"symbol": "ADA"}]))


@pytest.mark.asyncio
async def test_http_failure_does_not_become_empty_selection(monkeypatch):
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url, **kwargs):
            return httpx.Response(503, request=httpx.Request("GET", url))
    monkeypatch.setattr(radar_service.httpx, "AsyncClient", Client)
    with pytest.raises(httpx.HTTPStatusError):
        await radar_service.fetch_top_assets("test-key")
