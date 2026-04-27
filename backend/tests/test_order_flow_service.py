"""Regression tests for order flow taker_ratio normalization.

History
-------
* Pre-#72: collector persisted absurd values (~1e9) into
  ``indicators_json->'taker_ratio'`` for one-sided 60s windows
  (e.g. only buy-side trades for SUI), because the formula was
  ``buy / sell`` with a 1e-9 epsilon floor.
* #72: added a degeneracy/plausibility guard that clamped to (0, 5]
  but kept the buy/sell formula. Did not fix one-sided windows
  (they returned None) and still allowed large-but-finite imbalances
  like 3.28e2 to flow through.
* #82: switched to the canonical "Buy Volume Ratio" formula
  ``buy / (buy + sell)``, bounded [0, 1]. One-sided windows now
  produce a real, bounded value (1.0 for buy-only, 0.0 for
  sell-only) instead of being discarded.
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


def test_safe_taker_ratio_buy_only_window_returns_one():
    # SUI regression: 60s window had 8.98 SUI bought, 0 sold.  Old code
    # divided by 1e-9 and produced 8.98e9.  #72 returned None.  #82
    # returns 1.0 — a real, bounded signal of "all flow was buys".
    assert safe_taker_ratio("SUI_USDT", 60, buy_vol=8.98, sell_vol=0.0) == pytest.approx(1.0)


def test_safe_taker_ratio_sell_only_window_returns_zero():
    assert safe_taker_ratio("SUI_USDT", 60, buy_vol=0.0, sell_vol=12.5) == pytest.approx(0.0)


def test_safe_taker_ratio_returns_none_for_empty_window():
    # Both sides zero → no taker activity at all → caller must persist None.
    assert safe_taker_ratio("SUI_USDT", 60, buy_vol=0.0, sell_vol=0.0) is None


def test_safe_taker_ratio_value_is_bounded_to_zero_one():
    # Sanity check: even an extreme buy:sell imbalance produces a value
    # in [0, 1] under the new formula. 1000:1 → 0.999001.
    ratio = safe_taker_ratio("SUI_USDT", 60, buy_vol=1000.0, sell_vol=1.0)
    assert ratio == pytest.approx(1000.0 / 1001.0, rel=1e-4)
    assert 0.0 <= ratio <= 1.0


def test_safe_taker_ratio_accepts_value_at_upper_bound():
    # Boundary check: exactly TAKER_RATIO_MAX (= 1.0) is the largest
    # valid ratio, achieved when sell_vol == 0.
    assert TAKER_RATIO_MAX == pytest.approx(1.0)
    ratio = safe_taker_ratio("SUI_USDT", 60, buy_vol=5.0, sell_vol=0.0)
    assert ratio == pytest.approx(TAKER_RATIO_MAX)


def test_safe_taker_ratio_returns_real_value_in_normal_range():
    # Normal SUI window: balanced taker flow.
    # buy/(buy+sell) = 510 / 1510 = 0.337748
    ratio = safe_taker_ratio("SUI_USDT", 60, buy_vol=510.0, sell_vol=1000.0)
    assert ratio == pytest.approx(510.0 / 1510.0, rel=1e-4)


def test_safe_taker_ratio_balanced_window_is_half():
    # Equal buy and sell volumes → 0.5, the equilibrium signal.
    ratio = safe_taker_ratio("SUI_USDT", 60, buy_vol=42.0, sell_vol=42.0)
    assert ratio == pytest.approx(0.5)


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
async def test_get_order_flow_data_buy_only_window_returns_one(monkeypatch):
    """End-to-end: SUI-style one-sided window → taker_ratio == 1.0.

    Mirrors what production was doing: a 60s window with several buy-side
    taker trades and zero sell-side ones. Under the canonical
    Buy/(Buy+Sell) formula adopted in #82, that's a real signal of
    "100% buys" and persists as 1.0 (not 8.98e9, not None).
    """

    import time

    now_ms = time.time() * 1000
    trades = [
        _make_trade("buy", "3.5", now_ms - 5_000),
        _make_trade("buy", "5.48", now_ms - 1_000),
    ]
    _patch_gate_trades(monkeypatch, trades)

    payload = await get_order_flow_data("SUI_USDT", window_seconds=60)

    assert payload["taker_ratio"] == pytest.approx(1.0)
    # buy_pressure carries the same value (kept as alias for backward compat).
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

    # Both fields carry the same Buy/(Buy+Sell) value: 5/15 = 0.333.
    assert payload["taker_ratio"] == pytest.approx(5 / 15)
    assert payload["buy_pressure"] == pytest.approx(5 / 15)


@pytest.mark.anyio
async def test_get_order_flow_data_extreme_imbalance_stays_in_unit_interval(monkeypatch):
    """A very lopsided 1000:1 buy/sell window is no longer dropped.

    Under the new formula the result is 1000/1001 ≈ 0.999 — well within
    [0, 1] and a meaningful signal (whales hammered the buy side). The
    legacy [0, 5] guard would have rejected it; the new [0, 1] bound
    accepts it.
    """

    import time

    now_ms = time.time() * 1000
    trades = [
        _make_trade("buy", "1000.0", now_ms - 3_000),
        _make_trade("sell", "1.0", now_ms - 1_000),
    ]
    _patch_gate_trades(monkeypatch, trades)

    payload = await get_order_flow_data("SUI_USDT", window_seconds=60)

    assert payload["taker_ratio"] == pytest.approx(1000 / 1001)
    assert payload["buy_pressure"] == pytest.approx(1000 / 1001)
    assert 0.0 <= payload["taker_ratio"] <= 1.0


@pytest.fixture
def anyio_backend():
    return "asyncio"
