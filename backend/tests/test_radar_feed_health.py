import httpx
import pytest

from app.services import radar_service
from app.services.radar_service import RadarFeedUnavailable, validated_radar_assets


def feed(assets=None, **metadata):
    # Envelope flags observed alongside a fresh ACTIVE minute signal in production.
    return {"data": [] if assets is None else assets, "meta": {
        "market_data_enabled": False, "coverage_status": "PARTIAL",
        "has_more": False, "source_provider": "gate.io", "market": "spot", **metadata,
    }}


def test_valid_empty_feed_is_a_selection_not_an_outage():
    assert validated_radar_assets(feed()) == []


def test_minute_selection_ignores_global_collection_and_coverage_flags():
    # Regression for the observed NEAR signal: this endpoint's current list is
    # authoritative even though its shared envelope reports disabled/PARTIAL.
    assets = [{"pair": "NEAR_USDT", "status": "ACTIVE",
               "updated_at": "2026-09-27T16:39:00+00:00"}]
    assert validated_radar_assets(feed(assets)) == assets
    assert validated_radar_assets(feed()) == []


@pytest.mark.parametrize("metadata,reason", [
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


def test_global_metadata_is_not_required_by_minute_selection_contract():
    payload = feed([{"pair": "NEAR_USDT"}])
    del payload["meta"]["market_data_enabled"]
    del payload["meta"]["coverage_status"]
    assert validated_radar_assets(payload) == payload["data"]


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
