from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.services.shadow_entry_quote import estimate_entry, capture_entry_quote, EntryQuoteUnavailable, require_current_quote

NOW = datetime(2026, 10, 9, 5, 35, 16, tzinfo=timezone.utc)


def book():
    return {"update": int((NOW - timedelta(seconds=1)).timestamp() * 1000),
            "current": int(NOW.timestamp() * 1000), "_observed_at": NOW.isoformat(),
            "_requested_at": NOW.isoformat(),
            "bids": [["7.439", "100"]],
            "asks": [["7.441", "1"], ["7.442", "10"]]}


def estimate(payload=None, **kwargs):
    return estimate_entry(book() if payload is None else payload,
                          symbol="UNI_USDT", amount_usdt="14.883",
                          max_age_seconds=5, now=NOW, **kwargs)


def test_entire_amount_uses_ask_depth_not_last_candle_or_best_ask():
    result = estimate()
    assert result["value"] == 7.4415
    assert result["estimated_quantity"] == "2"
    assert result["consumed_asks"] == [["7.441", "1"], ["7.442", "1"]]
    assert result["entry_at"] == NOW.isoformat()
    assert result["realized"] is False


@pytest.mark.parametrize("field,value,reason", [
    ("update", int((NOW-timedelta(minutes=30)).timestamp()*1000), "STALE"),
    ("update", int((NOW+timedelta(seconds=1)).timestamp()*1000), "FUTURE"),
    ("_observed_at", (NOW+timedelta(seconds=1)).isoformat(), "FUTURE"),
    ("_requested_at", (NOW-timedelta(minutes=30)).isoformat(), "STALE"),
    ("_observed_at", None, "TIMESTAMP_MISSING"),
    ("update", None, "TIMESTAMP_MISSING"),
    ("asks", [], "EMPTY_BOOK"),
    ("asks", [["7.441", "0.1"]], "INSUFFICIENT_DEPTH"),
    ("asks", [["NaN", "10"]], "INVALID_BOOK"),
    ("bids", [["7.5", "10"]], "CROSSED_BOOK"),
])
def test_invalid_book_never_becomes_a_simulated_fill(field, value, reason):
    payload = book(); payload[field] = value
    with pytest.raises(EntryQuoteUnavailable, match=reason):
        estimate(payload)


@pytest.mark.parametrize("age", [None, 0, -1, float("nan"), float("inf")])
def test_missing_or_invalid_governed_age_refuses_capture(age):
    with pytest.raises(EntryQuoteUnavailable, match="AGE_UNCONFIGURED"):
        estimate_entry(book(), symbol="UNI_USDT", amount_usdt=1, max_age_seconds=age, now=NOW)


@pytest.mark.asyncio
async def test_cached_quote_does_not_get_a_new_timestamp():
    payload = book(); original = deepcopy(payload)
    with patch("app.services.market_data_service.MarketDataService.fetch_raw_orderbook",
               new=AsyncMock(return_value=payload)):
        with pytest.raises(EntryQuoteUnavailable, match="STALE"):
            await capture_entry_quote(symbol="UNI_USDT", amount_usdt=1, max_age_seconds=5)
    assert payload == original


def test_quote_that_expires_during_database_work_cannot_be_inserted():
    result = estimate()
    with pytest.raises(EntryQuoteUnavailable, match="EXPIRED_BEFORE_INSERT"):
        require_current_quote(result, now=NOW+timedelta(seconds=5), max_age_seconds=5)


def test_exchange_clock_offset_does_not_refresh_or_reject_a_valid_quote():
    payload = book()
    payload["current"] += 8000
    payload["update"] += 8000
    result = estimate(payload)
    assert result["age_seconds"] == 1
    assert result["exchange_clock_offset_seconds"] == 8
    assert result["source_at"] == (NOW-timedelta(seconds=1)).isoformat()
    assert result["exchange_source_at"] == (NOW+timedelta(seconds=7)).isoformat()


def test_slow_request_does_not_rejuvenate_old_book():
    payload = book()
    payload["_requested_at"] = (NOW-timedelta(seconds=5)).isoformat()
    with pytest.raises(EntryQuoteUnavailable, match="STALE"):
        estimate(payload)


def test_authorization_that_expires_after_capture_cannot_be_inserted():
    result = estimate()
    contract = {"evaluated_at": NOW.isoformat(), "feature_evaluations": [
        {"max_age_seconds": 1, "resolved_feature": {"age_seconds": 0,
         "source_timestamp": NOW.isoformat()}}]}
    with pytest.raises(EntryQuoteUnavailable, match="AUTHORIZATION_EXPIRED"):
        require_current_quote(result, now=NOW+timedelta(seconds=1), max_age_seconds=5,
                              authorization=contract)
