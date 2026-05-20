"""Task #316 — unit tests para o helper exit_metrics.

Cobre:
* drop de valores não-escalares (dict/list) + chamada do contador
* propagação NUNCA (exceção do provider vira ``_capture_error``)
* flatten idempotente (nested → flat, flat → flat)
* imutabilidade da constante ``EXIT_METRICS_INTERNAL_KEYS``
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from app.services import exit_metrics


def test_internal_keys_are_immutable_frozenset():
    assert isinstance(exit_metrics.EXIT_METRICS_INTERNAL_KEYS, frozenset)
    with pytest.raises(AttributeError):
        exit_metrics.EXIT_METRICS_INTERNAL_KEYS.add("foo")  # type: ignore[attr-defined]


def test_flatten_entry_snapshot_nested():
    nested = {
        "rsi": {"value": 55.2, "source_group": "structural", "stale": False},
        "macd": {"value": -0.001, "source_group": "structural"},
    }
    flat = exit_metrics.flatten_entry_snapshot(nested)
    assert flat == {"rsi": 55.2, "macd": -0.001}


def test_flatten_entry_snapshot_idempotent_on_flat():
    flat_in = {"rsi": 55.2, "macd": -0.001}
    assert exit_metrics.flatten_entry_snapshot(flat_in) == flat_in


def test_flatten_entry_snapshot_handles_none_and_empty():
    assert exit_metrics.flatten_entry_snapshot(None) == {}
    assert exit_metrics.flatten_entry_snapshot({}) == {}


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_build_exit_snapshot_drops_non_scalar(monkeypatch):
    async def fake_provider(_db, _symbol, include_stale=True):
        return {
            "rsi": 50.0,
            "macd": -0.01,
            "ema_full_alignment": True,
            "nested_garbage": {"a": 1},  # must be dropped
            "list_garbage": [1, 2, 3],   # must be dropped
            "missing": None,             # scalar None — kept
            "symbol_label": "BTC_USDT",  # str scalar — kept
        }

    monkeypatch.setattr(
        exit_metrics.indicators_provider,
        "build_full_flat_snapshot",
        fake_provider,
    )
    result = _run(exit_metrics.build_exit_snapshot(None, "BTC_USDT"))
    assert result == {
        "rsi": 50.0,
        "macd": -0.01,
        "ema_full_alignment": True,
        "missing": None,
        "symbol_label": "BTC_USDT",
    }
    assert "nested_garbage" not in result
    assert "list_garbage" not in result


def test_build_exit_snapshot_never_raises(monkeypatch):
    async def boom(_db, _symbol, include_stale=True):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(
        exit_metrics.indicators_provider,
        "build_full_flat_snapshot",
        boom,
    )
    result = _run(exit_metrics.build_exit_snapshot(None, "BTC_USDT"))
    assert "_capture_error" in result
    assert "RuntimeError" in result["_capture_error"]
    assert "provider exploded" in result["_capture_error"]


def test_build_exit_snapshot_empty_provider_returns_empty(monkeypatch):
    async def empty(_db, _symbol, include_stale=True):
        return {}

    monkeypatch.setattr(
        exit_metrics.indicators_provider,
        "build_full_flat_snapshot",
        empty,
    )
    result = _run(exit_metrics.build_exit_snapshot(None, "BTC_USDT"))
    assert result == {}
