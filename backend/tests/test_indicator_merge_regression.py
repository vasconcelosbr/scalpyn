"""Regression tests for indicator merge logic (Task #200).

Verifies that merge_indicator_rows correctly handles:
  - Structural-only (no microstructure rows).
  - Microstructure-only (no structural rows).
  - Both groups present.
  - Envelope unwrapping inside the merge.
"""

import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.utils.indicator_merge import merge_indicator_rows, MergedIndicators


NOW = datetime(2026, 5, 4, 12, 0, 0, tzinfo=timezone.utc)


def test_structural_only_indicators_present():
    """When only structural rows exist (no microstructure), all structural
    indicators must be available in the merged result.
    """
    rows = [
        (
            "structural",
            NOW - timedelta(minutes=5),
            {
                "rsi": {"value": 48.5, "source": "candle_computed", "confidence": 0.8, "status": "VALID"},
                "adx": {"value": 22.4, "source": "candle_computed", "confidence": 0.8, "status": "VALID"},
                "macd_histogram": {"value": 0.003, "source": "candle_computed", "confidence": 0.8, "status": "VALID"},
                "bb_width": {"value": 0.054, "source": "candle_computed", "confidence": 0.8, "status": "VALID"},
            },
        ),
    ]
    result = merge_indicator_rows(rows, now=NOW)

    assert isinstance(result, MergedIndicators)
    assert result.get("rsi") == 48.5
    assert result.get("adx") == 22.4
    assert result.get("macd_histogram") == 0.003
    assert result.get("bb_width") == 0.054
    assert result.get("taker_ratio") is None


def test_microstructure_only_indicators_present():
    """When only microstructure rows exist (no structural), all microstructure
    indicators must be available in the merged result.
    """
    rows = [
        (
            "microstructure",
            NOW - timedelta(minutes=2),
            {
                "taker_ratio": {"value": 0.51, "source": "gate_trades", "confidence": 0.9, "status": "VALID"},
                "volume_delta": {"value": -15.3, "source": "gate_trades", "confidence": 0.9, "status": "VALID"},
                "volume_spike": {"value": 1.42, "source": "candle_computed", "confidence": 0.8, "status": "VALID"},
            },
        ),
    ]
    result = merge_indicator_rows(rows, now=NOW)

    assert result.get("taker_ratio") == 0.51
    assert result.get("volume_delta") == -15.3
    assert result.get("volume_spike") == 1.42
    assert result.get("rsi") is None


def test_both_groups_merge_all_indicators():
    """When both structural and microstructure rows are present, the merged
    result must contain indicators from BOTH groups.
    """
    rows = [
        (
            "structural",
            NOW - timedelta(minutes=10),
            {
                "rsi": {"value": 48.5, "source": "candle_computed", "confidence": 0.8, "status": "VALID"},
                "adx": {"value": 22.4, "source": "candle_computed", "confidence": 0.8, "status": "VALID"},
                "macd_histogram": {"value": 0.003, "source": "candle_computed", "confidence": 0.8, "status": "VALID"},
            },
        ),
        (
            "microstructure",
            NOW - timedelta(minutes=2),
            {
                "taker_ratio": {"value": 0.51, "source": "gate_trades", "confidence": 0.9, "status": "VALID"},
                "volume_delta": {"value": -15.3, "source": "gate_trades", "confidence": 0.9, "status": "VALID"},
                "volume_spike": {"value": 1.42, "source": "candle_computed", "confidence": 0.8, "status": "VALID"},
            },
        ),
    ]
    result = merge_indicator_rows(rows, now=NOW)

    assert result.get("rsi") == 48.5
    assert result.get("adx") == 22.4
    assert result.get("macd_histogram") == 0.003
    assert result.get("taker_ratio") == 0.51
    assert result.get("volume_delta") == -15.3
    assert result.get("volume_spike") == 1.42


def test_envelope_unwrap_inside_merge():
    """merge_indicator_rows must unwrap envelope dicts to bare scalars
    in the MergedIndicators.values output.
    """
    rows = [
        (
            "combined",
            NOW - timedelta(minutes=3),
            {
                "rsi": {"value": 66.0, "source": "candle_computed", "confidence": 0.8, "status": "VALID"},
                "taker_ratio": {"value": 0.75, "source": "gate_trades", "confidence": 0.9, "status": "VALID"},
            },
        ),
    ]
    result = merge_indicator_rows(rows, now=NOW)

    flat = result.as_flat_dict()
    assert flat["rsi"] == 66.0
    assert flat["taker_ratio"] == 0.75
    assert not isinstance(flat["rsi"], dict)
    assert not isinstance(flat["taker_ratio"], dict)


def test_envelope_value_none_excluded_from_merge():
    """Envelope entries with value=None (NO_DATA) must NOT appear in
    MergedIndicators.values — they are not scoreable.
    """
    rows = [
        (
            "structural",
            NOW - timedelta(minutes=5),
            {
                "rsi": {"value": None, "source": "unknown", "confidence": 0.0, "status": "NO_DATA"},
                "adx": {"value": 22.4, "source": "candle_computed", "confidence": 0.8, "status": "VALID"},
            },
        ),
    ]
    result = merge_indicator_rows(rows, now=NOW)

    assert "rsi" not in result
    assert result.get("adx") == 22.4


def test_flat_scalars_still_work_in_merge():
    """Legacy flat-scalar rows (pre-envelope) must still merge correctly."""
    rows = [
        (
            "combined",
            NOW - timedelta(minutes=5),
            {"rsi": 48.5, "adx": 22.4, "bb_width": 0.054},
        ),
    ]
    result = merge_indicator_rows(rows, now=NOW)

    assert result.get("rsi") == 48.5
    assert result.get("adx") == 22.4
    assert result.get("bb_width") == 0.054


def test_stale_structural_excluded():
    """A structural row older than STRUCTURAL_STALE_SECONDS (default 1800s)
    must be excluded from the merge values.
    """
    rows = [
        (
            "structural",
            NOW - timedelta(minutes=35),
            {"rsi": {"value": 48.5, "source": "candle_computed", "confidence": 0.8, "status": "VALID"}},
        ),
        (
            "microstructure",
            NOW - timedelta(minutes=2),
            {"taker_ratio": {"value": 0.51, "source": "gate_trades", "confidence": 0.9, "status": "VALID"}},
        ),
    ]
    result = merge_indicator_rows(rows, now=NOW)

    assert "rsi" not in result
    assert result.get("taker_ratio") == 0.51


def test_as_flat_dict_returns_plain_dict():
    """as_flat_dict must return a plain dict (not MergedIndicators)."""
    rows = [
        (
            "combined",
            NOW - timedelta(minutes=3),
            {"rsi": 50.0, "adx": 20.0},
        ),
    ]
    result = merge_indicator_rows(rows, now=NOW)

    flat = result.as_flat_dict()
    assert type(flat) is dict
    assert flat == {"rsi": 50.0, "adx": 20.0}


def test_fetch_indicators_map_adapter_contract():
    """The adapter pattern used by _fetch_indicators_map must produce
    {symbol: flat_dict} where every value in the flat_dict is a scalar
    (not a dict/envelope). This tests the contract at the MergedIndicators
    boundary without needing a DB session.
    """
    rows_by_symbol = {
        "HYPE_USDT": [
            (
                "structural",
                NOW - timedelta(minutes=5),
                {
                    "rsi": {"value": 48.5, "source": "candle_computed", "confidence": 0.8, "status": "VALID"},
                    "adx": {"value": 22.4, "source": "candle_computed", "confidence": 0.8, "status": "VALID"},
                },
            ),
            (
                "microstructure",
                NOW - timedelta(minutes=2),
                {
                    "taker_ratio": {"value": 0.51, "source": "gate_trades", "confidence": 0.9, "status": "VALID"},
                    "volume_spike": {"value": 1.42, "source": "candle_computed", "confidence": 0.8, "status": "VALID"},
                },
            ),
        ],
        "BTC_USDT": [
            (
                "combined",
                NOW - timedelta(minutes=3),
                {"rsi": 55.0, "bb_width": 0.04},
            ),
        ],
    }

    adapter_result = {}
    for sym, rows in rows_by_symbol.items():
        mi = merge_indicator_rows(rows, now=NOW)
        adapter_result[sym] = mi.as_flat_dict()

    assert isinstance(adapter_result, dict)
    assert set(adapter_result.keys()) == {"HYPE_USDT", "BTC_USDT"}

    hype = adapter_result["HYPE_USDT"]
    assert isinstance(hype, dict)
    assert hype["rsi"] == 48.5
    assert hype["adx"] == 22.4
    assert hype["taker_ratio"] == 0.51
    assert hype["volume_spike"] == 1.42
    for v in hype.values():
        assert not isinstance(v, dict), f"Adapter must return scalars, got dict: {v}"

    btc = adapter_result["BTC_USDT"]
    assert btc["rsi"] == 55.0
    assert btc["bb_width"] == 0.04
    for v in btc.values():
        assert not isinstance(v, dict), f"Adapter must return scalars, got dict: {v}"
