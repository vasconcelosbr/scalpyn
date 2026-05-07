"""Tests for the order-flow merge fix in ``tasks/compute_indicators`` (Task #171).

The pre-#171 code did ``results.update({k: v for ...})`` which
overwrote a previously-computed valid ``taker_ratio`` with ``None``
whenever the new order-flow lookup happened to come back empty.  The
new ``_merge_order_flow_into_results`` preserves valid values when the
incoming ``of_data[k]`` is ``None``, while still letting metadata
(``taker_source`` / ``taker_window``) update unconditionally.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.tasks.compute_indicators import _merge_order_flow_into_results


def test_merge_preserves_valid_value_when_new_is_none():
    results = {
        "taker_ratio": 0.62,
        "buy_pressure": 0.62,
        "volume_delta": 12.5,
    }
    of_data = {
        "taker_ratio": None,
        "buy_pressure": None,
        "volume_delta": None,
        "taker_buy_volume": None,
        "taker_sell_volume": None,
        "taker_source": "gate_io_trades",
        "taker_window": "60s",
    }

    _merge_order_flow_into_results(results, of_data)

    # Valid values were NOT overwritten by None.
    assert results["taker_ratio"]  == pytest.approx(0.62)
    assert results["buy_pressure"] == pytest.approx(0.62)
    assert results["volume_delta"] == pytest.approx(12.5)
    # Metadata was updated unconditionally so the envelope reflects
    # the actual fetch that just happened.
    assert results["taker_source"] == "gate_io_trades"
    assert results["taker_window"] == "60s"


def test_merge_overwrites_when_new_value_is_provided():
    results = {
        "taker_ratio": 0.50,
        "buy_pressure": 0.50,
    }
    of_data = {
        "taker_ratio": 0.71,
        "buy_pressure": 0.71,
        "volume_delta": 5.0,
        "taker_source": "gate_trades_ws",
        "taker_window": "300s",
    }

    _merge_order_flow_into_results(results, of_data)

    # New value wins.
    assert results["taker_ratio"]  == pytest.approx(0.71)
    assert results["buy_pressure"] == pytest.approx(0.71)
    assert results["volume_delta"] == pytest.approx(5.0)
    assert results["taker_source"] == "gate_trades_ws"
    assert results["taker_window"] == "300s"


def test_merge_seeds_value_when_results_has_no_prior_entry():
    """A first-time merge populates the key (regression for the ``None`` branch)."""
    results: dict = {}
    of_data = {
        "taker_ratio": None,        # still None, key gets set to None
        "buy_pressure": None,
        "volume_delta": 3.14,       # value provided
        "taker_buy_volume": None,
        "taker_sell_volume": None,
        "taker_source": "gate_trades_ws",
        "taker_window": "300s",
    }
    _merge_order_flow_into_results(results, of_data)
    assert results["taker_ratio"] is None
    assert results["volume_delta"] == pytest.approx(3.14)
    assert results["taker_source"] == "gate_trades_ws"


def test_merge_propagates_non_orderflow_keys_normally():
    """Keys outside the order-flow set are merged with normal ``update`` semantics."""
    results = {"foo": "old"}
    of_data = {"foo": "new", "bar": 7, "taker_source": "gate_io_trades", "taker_window": "60s"}
    _merge_order_flow_into_results(results, of_data)
    assert results["foo"] == "new"
    assert results["bar"] == 7
