"""Regression tests for order flow taker_ratio normalization.

The collector previously persisted absurd values (~1e9) into
``indicators_json->'taker_ratio'`` whenever a 60s window happened to be
one-sided (e.g. only buy-side trades for SUI). The fix adds a
plausibility/degeneracy guard around ``buy_vol / sell_vol`` so the
collector now writes ``None`` instead of a number that cannot represent
a real ratio.
"""

import os
import sys
from typing import Any, Dict, List

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services import order_flow_service
from app.services.order_flow_service import (
    TAKER_RATIO_MAX,
    safe_taker_ratio,
    get_order_flow_data,
)


def test_safe_taker_ratio_returns_none_when_sell_volume_is_zero():
    # SUI regression: 60s window had 8.98 SUI bought, 0 sold.  Old code
    # divided by 1e-9 and produced 8.98e9.  New code returns None so the
    # value is never persisted as a "ratio".
    assert safe_taker_ratio("SUI_USDT", 60, buy_vol=8.98, sell_vol=0.0) is None


def test_safe_taker_ratio_returns_none_when_buy_volume_is_zero():
    assert safe_taker_ratio("SUI_USDT", 60, buy_vol=0.0, sell_vol=12.5) is None


def test_safe_taker_ratio_returns_none_for_empty_window():
    # Both sides zero → no warning, no value (caller is expected to have
    # already returned the empty payload, but the helper must still be
    # defensive).
    assert safe_taker_ratio("SUI_USDT", 60, buy_vol=0.0, sell_vol=0.0) is None


def test_safe_taker_ratio_returns_none_when_above_plausibility_bound():
    # A large but finite buy/sell imbalance still has to fit in (0, 5];
    # anything above is treated as a corrupted feed regardless of the
    # raw inputs.
    assert safe_taker_ratio("SUI_USDT", 60, buy_vol=1000.0, sell_vol=1.0) is None


def test_safe_taker_ratio_accepts_value_at_upper_bound():
    # Boundary check: exactly TAKER_RATIO_MAX is the largest valid ratio.
    ratio = safe_taker_ratio("SUI_USDT", 60, buy_vol=5.0, sell_vol=1.0)
    assert ratio == pytest.approx(TAKER_RATIO_MAX)


def test_safe_taker_ratio_returns_real_value_in_normal_range():
    # Normal SUI window: balanced taker flow.
    ratio = safe_taker_ratio("SUI_USDT", 60, buy_vol=510.0, sell_vol=1000.0)
    assert ratio == pytest.approx(0.51)


def _make_trade(side: str, amount: str, ts_ms: float) -> Dict[str, Any]:
    return {"side": side, "amount": amount, "create_time_ms": ts_ms}


def _patch_gate_trades(monkeypatch, trades: List[Dict[str, Any]]) -> None:
    """Replace the public Gate.io HTTP call with an in-memory fixture."""

    class _FakeAdapter:
        SPOT_BASE = "https://example.test"

        @staticmethod
        def _normalize_symbol(symbol: str) -> str:
            return symbol

        @staticmethod
        async def _public_get(url: str, params: Dict[str, Any]):
            return trades

    monkeypatch.setattr(
        "app.exchange_adapters.gate_adapter.GateAdapter",
        _FakeAdapter,
        raising=False,
    )


@pytest.mark.anyio
async def test_get_order_flow_data_returns_none_taker_ratio_for_sui_one_sided_window(
    monkeypatch,
):
    """End-to-end regression: SUI-style one-sided window → taker_ratio=None.

    Mirrors what production was doing: a 60s window with several buy-side
    taker trades and zero sell-side ones.  The persisted payload must
    have ``taker_ratio == None`` so downstream rule evaluators mark the
    rule SKIPPED instead of receiving 8.98e9.
    """

    import time

    now_ms = time.time() * 1000
    trades = [
        _make_trade("buy", "3.5", now_ms - 5_000),
        _make_trade("buy", "5.48", now_ms - 1_000),
    ]
    _patch_gate_trades(monkeypatch, trades)

    payload = await get_order_flow_data("SUI_USDT", window_seconds=60)

    # The bug: taker_ratio was ~8.98e9.  The fix: None.
    assert payload["taker_ratio"] is None
    # buy_pressure should still carry the directional signal.
    assert payload["buy_pressure"] == pytest.approx(1.0)
    # And the underlying volumes are preserved for diagnostics.
    assert payload["taker_buy_volume"] == pytest.approx(8.98)
    assert payload["taker_sell_volume"] == pytest.approx(0.0)


@pytest.mark.anyio
async def test_get_order_flow_data_returns_real_ratio_for_balanced_window(monkeypatch):
    import time

    now_ms = time.time() * 1000
    trades = [
        _make_trade("buy", "5.0", now_ms - 4_000),
        _make_trade("sell", "10.0", now_ms - 2_000),
    ]
    _patch_gate_trades(monkeypatch, trades)

    payload = await get_order_flow_data("SUI_USDT", window_seconds=60)

    assert payload["taker_ratio"] == pytest.approx(0.5)
    assert payload["buy_pressure"] == pytest.approx(5 / 15)


@pytest.mark.anyio
async def test_get_order_flow_data_drops_implausible_ratio(monkeypatch):
    """A ratio above the plausibility bound (e.g. 1000:1) is dropped, not
    persisted.  Defends against feeds that emit dust-only sell volume
    against large buys."""

    import time

    now_ms = time.time() * 1000
    trades = [
        _make_trade("buy", "1000.0", now_ms - 3_000),
        _make_trade("sell", "1.0", now_ms - 1_000),
    ]
    _patch_gate_trades(monkeypatch, trades)

    payload = await get_order_flow_data("SUI_USDT", window_seconds=60)

    assert payload["taker_ratio"] is None
    # buy_pressure is still well-defined and meaningful.
    assert payload["buy_pressure"] == pytest.approx(1000 / 1001)


@pytest.fixture
def anyio_backend():
    return "asyncio"
