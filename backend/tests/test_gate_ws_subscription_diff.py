"""Unit test for ``GateWSClient.apply_subscription_diff`` (Task #194 round-2 blocker 5)."""
import asyncio
import json
from app.websocket.gate_ws_client import GateWSClient


class _FakeWS:
    def __init__(self):
        self.sent = []

    async def send(self, raw):
        self.sent.append(json.loads(raw))


def test_apply_subscription_diff_sends_only_deltas():
    client = GateWSClient(
        api_key="", api_secret="",
        contracts=["BTC_USDT", "ETH_USDT"],
        spot_pairs=["BTC_USDT", "SOL_USDT"],
    )
    spot_ws = _FakeWS()
    futures_ws = _FakeWS()
    client._spot_ws = spot_ws
    client._futures_ws = futures_ws

    out = asyncio.run(
        client.apply_subscription_diff(
            spot_pairs=["BTC_USDT", "DOGE_USDT"],   # add DOGE, remove SOL
            futures_contracts=["BTC_USDT", "XRP_USDT"],  # add XRP, remove ETH
        )
    )

    assert out == {
        "spot": {"added": 1, "removed": 1},
        "futures": {"added": 1, "removed": 1},
    }
    spot_events = [(m["event"], m["payload"]) for m in spot_ws.sent]
    fut_events = [(m["event"], m["payload"]) for m in futures_ws.sent]
    assert ("subscribe", ["DOGE_USDT"]) in spot_events
    assert ("unsubscribe", ["SOL_USDT"]) in spot_events
    assert ("subscribe", ["XRP_USDT"]) in fut_events
    assert ("unsubscribe", ["ETH_USDT"]) in fut_events
    # State updated to new desired set.
    assert sorted(client._spot_pairs) == ["BTC_USDT", "DOGE_USDT"]
    assert sorted(client._contracts) == ["BTC_USDT", "XRP_USDT"]


def test_apply_subscription_diff_no_changes_is_noop():
    client = GateWSClient(
        api_key="", api_secret="",
        contracts=["BTC_USDT"], spot_pairs=["ETH_USDT"],
    )
    # No live socket needed when nothing to send.
    out = asyncio.run(
        client.apply_subscription_diff(
            spot_pairs=["ETH_USDT"], futures_contracts=["BTC_USDT"]
        )
    )
    assert out == {
        "spot": {"added": 0, "removed": 0},
        "futures": {"added": 0, "removed": 0},
    }


def test_apply_subscription_diff_raises_when_socket_missing():
    """Caller must fall back to drop+reconnect when no live ws."""
    client = GateWSClient(
        api_key="", api_secret="",
        contracts=[], spot_pairs=["ETH_USDT"],
    )
    # spot_ws is None → diff with changes must raise.
    import pytest
    with pytest.raises(RuntimeError, match="spot WS not connected"):
        asyncio.run(
            client.apply_subscription_diff(
                spot_pairs=["BTC_USDT"], futures_contracts=[]
            )
        )


def test_apply_subscription_diff_uses_per_channel_payload_form():
    """Round-4 fix: futures.candlesticks expects ``["1m,<contract>"]``,
    NOT raw symbols. ``-1`` universe channels must be skipped entirely."""
    client = GateWSClient(
        api_key="", api_secret="",
        contracts=["BTC_USDT"],
        spot_pairs=["BTC_USDT"],
    )
    spot_ws = _FakeWS()
    futures_ws = _FakeWS()
    client._spot_ws = spot_ws
    client._futures_ws = futures_ws

    asyncio.run(
        client.apply_subscription_diff(
            spot_pairs=["BTC_USDT", "DOGE_USDT"],            # add DOGE
            futures_contracts=["BTC_USDT", "ETH_USDT"],      # add ETH
        )
    )

    # By channel, what payload was sent for the add?
    fut_by_channel = {m["channel"]: m["payload"] for m in futures_ws.sent if m["event"] == "subscribe"}

    assert fut_by_channel.get("futures.candlesticks") == ["1m,ETH_USDT"]
    assert fut_by_channel.get("futures.tickers") == ["ETH_USDT"]
    assert fut_by_channel.get("futures.trades") == ["ETH_USDT"]
    # Universe-wide channels (orders/autoorders) must NOT appear in diff.
    assert "futures.orders" not in fut_by_channel
    assert "futures.autoorders" not in fut_by_channel

    spot_by_channel = {m["channel"]: m["payload"] for m in spot_ws.sent if m["event"] == "subscribe"}
    assert spot_by_channel.get("spot.tickers") == ["DOGE_USDT"]
    assert spot_by_channel.get("spot.trades") == ["DOGE_USDT"]
    assert spot_by_channel.get("spot.orders") == ["DOGE_USDT"]
