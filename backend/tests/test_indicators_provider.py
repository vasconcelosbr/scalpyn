"""Regression tests for the unified indicator provider (Task #215).

Pins the four invariants that the dual-scheduler merge bug depended on:

  1. When the latest row is microstructure-only (frequent in production),
     the provider still returns RSI / MACD / ADX from the structural row,
     so decision engines do not silently skip the candidate.

  2. ``is_complete`` shares its rule across every consumer — pipeline_scan,
     evaluate_signals, execute_buy — so any future call site gets the
     same pass/skip outcome on the same indicator payload.

  3. The drift cap default is high enough that a 1100 s skew between
     micro and structural still keeps both groups alive (was 900 s and
     would drop structural; now ≥ 1200 s).

  4. ``build_indicators_snapshot`` produces the exact ``{key: {value,
     source_group, ts, stale}}`` shape consumed by ``decisions_log``.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.utils.indicator_merge import (
    INDICATOR_MAX_DRIFT_SECONDS,
    STRUCTURAL_INTERVAL_SECONDS,
    merge_indicator_rows,
)
from app.services.indicators_provider import (
    REQUIRED_CORE_INDICATORS,
    build_indicators_snapshot,
    filter_incomplete_assets,
    is_complete,
)


NOW = datetime(2026, 5, 4, 12, 0, 0, tzinfo=timezone.utc)


def _env(value, source="candle_computed", confidence=0.85):
    return {"value": value, "source": source, "confidence": confidence, "status": "VALID"}


# ── 1. Latest-row-is-microstructure regression ─────────────────────────────


def test_latest_row_micro_only_still_yields_core_via_struct_row():
    """The bug's signature: micro is fresher (5 min ago) than structural (10
    min ago); structural row carries the only RSI / MACD / ADX values.

    Naive ``DISTINCT ON (symbol) ... ORDER BY time DESC`` would return the
    micro row only and execution would skip the candidate. The merge
    keeps both groups alive, so the provider's flat dict carries every
    required core indicator.
    """
    rows = [
        (
            "structural",
            NOW - timedelta(minutes=10),
            {
                "rsi": _env(48.5),
                "adx": _env(22.4),
                "macd_histogram": _env(0.003),
            },
        ),
        (
            "microstructure",
            NOW - timedelta(minutes=2),
            {
                "taker_ratio": _env(0.51, source="gate_trades", confidence=0.9),
                "volume_delta": _env(-15.3, source="gate_trades", confidence=0.9),
            },
        ),
    ]

    merged = merge_indicator_rows(rows, now=NOW)
    flat = merged.as_flat_dict()

    ok, missing = is_complete(flat)
    assert ok, f"expected complete, got missing={missing}"
    assert flat["rsi"] == 48.5
    assert flat["adx"] == 22.4
    assert flat["macd_histogram"] == 0.003
    assert flat["taker_ratio"] == 0.51


# ── 2. Shared completeness rule ────────────────────────────────────────────


def test_is_complete_treats_envelope_value_none_as_missing():
    """Envelope with ``value=None`` must be treated as missing — the trace UI
    bug ("SEM DADOS / aguardando coleta") came from consumers that read the
    raw envelope dict and saw a non-None object.
    """
    indicators = {
        "rsi": {"value": None, "status": "NO_DATA"},
        "adx": _env(20.0),
        "macd_histogram": _env(0.001),
    }
    ok, missing = is_complete(indicators)
    assert not ok
    assert missing == ["rsi"]


def test_is_complete_accepts_flat_scalars():
    indicators = {"rsi": 50.0, "adx": 25.0, "macd_histogram": 0.002}
    ok, missing = is_complete(indicators)
    assert ok and missing == []


def test_filter_incomplete_assets_partitions_correctly():
    assets = [
        {"symbol": "OK_USDT", "indicators": {"rsi": 50.0, "adx": 25.0, "macd_histogram": 0.001}},
        {"symbol": "MISS_USDT", "indicators": {"rsi": 50.0, "adx": None, "macd_histogram": 0.001}},
    ]
    complete, incomplete = filter_incomplete_assets(assets)
    assert [a["symbol"] for a in complete] == ["OK_USDT"]
    assert [a["symbol"] for a in incomplete] == ["MISS_USDT"]


def test_required_core_indicators_use_canonical_macd_histogram_key():
    # Operator review explicitly named ``macd_histogram`` as the canonical
    # key (it is what robust_indicators/asset_score.py and signal/block
    # engines actually consume); the raw ``macd`` line value is informational.
    assert "macd_histogram" in REQUIRED_CORE_INDICATORS
    assert "macd" not in REQUIRED_CORE_INDICATORS


# ── 3. Drift cap respects the new default ──────────────────────────────────


def test_drift_cap_default_is_at_least_structural_interval_plus_300():
    assert INDICATOR_MAX_DRIFT_SECONDS >= STRUCTURAL_INTERVAL_SECONDS + 300.0


def test_1100_second_drift_no_longer_drops_structural_group():
    """Old default (900 s) would treat 1100 s drift as too large and drop
    the older structural group — silently erasing RSI / MACD / ADX. New
    default (≥ 1200 s) keeps both groups alive.
    """
    rows = [
        (
            "structural",
            NOW - timedelta(seconds=1150),
            {"rsi": _env(48.5), "adx": _env(22.4), "macd_histogram": _env(0.003)},
        ),
        (
            "microstructure",
            NOW - timedelta(seconds=50),
            {"taker_ratio": _env(0.51, source="gate_trades", confidence=0.9)},
        ),
    ]
    merged = merge_indicator_rows(rows, now=NOW)
    flat = merged.as_flat_dict()
    assert flat.get("rsi") == 48.5
    assert flat.get("adx") == 22.4
    assert flat.get("macd_histogram") == 0.003
    assert flat.get("taker_ratio") == 0.51


# ── 4. Snapshot persistence shape ──────────────────────────────────────────


def test_build_indicators_snapshot_default_only_required_core():
    """Default snapshot must stay small — required-core only — so JSONB
    growth per decision is bounded."""
    rows = [
        (
            "structural",
            NOW - timedelta(minutes=10),
            {"rsi": _env(48.5), "adx": _env(22.4), "macd_histogram": _env(0.003)},
        ),
        (
            "microstructure",
            NOW - timedelta(minutes=2),
            {
                "taker_ratio": _env(0.51, source="gate_trades", confidence=0.9),
                "volume_delta": _env(-15.3, source="gate_trades", confidence=0.9),
                "volume_spike": _env(1.42),
            },
        ),
    ]
    merged = merge_indicator_rows(rows, now=NOW)
    snap = build_indicators_snapshot(merged)

    assert set(snap.keys()) == set(REQUIRED_CORE_INDICATORS), (
        "Default snapshot must include only the required-core keys; "
        f"got extras: {set(snap.keys()) - set(REQUIRED_CORE_INDICATORS)}"
    )

    rsi_entry = snap["rsi"]
    assert rsi_entry["value"] == 48.5
    assert rsi_entry["source_group"] == "structural"
    assert rsi_entry["ts"] is not None
    assert rsi_entry["stale"] is False


def test_build_indicators_snapshot_explicit_keys_scopes_correctly():
    """Callers that consumed specific keys (e.g. score components) can
    pass them explicitly; required-core is always added back so the
    snapshot is self-evident about completeness state."""
    rows = [
        (
            "structural",
            NOW - timedelta(minutes=10),
            {"rsi": _env(48.5), "adx": _env(22.4), "macd_histogram": _env(0.003)},
        ),
        (
            "microstructure",
            NOW - timedelta(minutes=2),
            {"taker_ratio": _env(0.51, source="gate_trades", confidence=0.9)},
        ),
    ]
    merged = merge_indicator_rows(rows, now=NOW)
    snap = build_indicators_snapshot(merged, keys={"taker_ratio"})

    assert "taker_ratio" in snap
    assert snap["taker_ratio"]["value"] == 0.51
    assert snap["taker_ratio"]["source_group"] == "microstructure"
    # Required-core is always merged in
    for key in REQUIRED_CORE_INDICATORS:
        assert key in snap


# ── 5. Execution-path quarantine regression (Task #215 scenario) ───────────


def _build_asset_from_merge(symbol, mi):
    """Mirror the pipeline_scan / execute_buy candidate-build path:
    use the same flat dict the consumer hands to its quarantine guard."""
    return {"symbol": symbol, "indicators": mi.as_flat_dict()}


def test_execution_path_micro_only_latest_does_not_quarantine():
    """End-to-end regression for the bug's original signature.

    Production saw this exact shape:
      * Latest indicators row in the DB is microstructure (5 min ago)
      * Structural row from 10 min ago carries the only RSI/MACD/ADX
      * Old code: ``DISTINCT ON ... ORDER BY time DESC`` → micro row only
        → consumer sees ``rsi=None``, ``macd_histogram=None``, ``adx=None``
        → quarantine fires → SKIP / SEM-DADOS

    With the merge + provider in the path, the consumer's flat dict
    carries the structural keys and ``filter_incomplete_assets``
    classifies the asset as complete (i.e. it would advance to scoring
    in pipeline_scan and to the entry-trigger loop in evaluate_signals /
    execute_buy).
    """
    rows = [
        (
            "structural",
            NOW - timedelta(minutes=10),
            {"rsi": _env(48.5), "adx": _env(22.4), "macd_histogram": _env(0.003)},
        ),
        (
            "microstructure",
            NOW - timedelta(minutes=2),
            {
                "taker_ratio": _env(0.51, source="gate_trades", confidence=0.9),
                "volume_delta": _env(-15.3, source="gate_trades", confidence=0.9),
            },
        ),
    ]
    mi = merge_indicator_rows(rows, now=NOW)

    # Consumer's view via the same flat dict shape it hands to engines.
    asset = _build_asset_from_merge("BTC_USDT", mi)
    complete, incomplete = filter_incomplete_assets([asset])
    assert [a["symbol"] for a in complete] == ["BTC_USDT"], (
        "Micro-only-latest scenario must NOT quarantine: structural keys "
        "are still merged into the consumer's flat dict."
    )
    assert incomplete == []

    # And the engines see the values, not None
    flat = asset["indicators"]
    assert flat["rsi"] == 48.5
    assert flat["adx"] == 22.4
    assert flat["macd_histogram"] == 0.003
    # Microstructure keys also reach the consumer
    assert flat["taker_ratio"] == 0.51


def test_execution_path_genuine_warmup_still_quarantines():
    """Negative complement: when structural truly never produced a row
    (e.g. a freshly added symbol mid-warmup), the consumer MUST still
    quarantine — completeness rule is symmetric."""
    rows = [
        (
            "microstructure",
            NOW - timedelta(minutes=2),
            {"taker_ratio": _env(0.51, source="gate_trades", confidence=0.9)},
        ),
    ]
    mi = merge_indicator_rows(rows, now=NOW)
    asset = _build_asset_from_merge("NEW_USDT", mi)
    complete, incomplete = filter_incomplete_assets([asset])
    assert complete == []
    assert [a["symbol"] for a in incomplete] == ["NEW_USDT"]


def test_build_indicators_snapshot_records_missing_core_with_null_value():
    """When a required core key is absent (e.g. genuine warmup), the
    snapshot must still list the key with a ``None`` value so an
    investigator can distinguish "we never tried to look it up" from
    "we looked it up and the DB had nothing".
    """
    rows = [
        (
            "microstructure",
            NOW - timedelta(minutes=2),
            {"taker_ratio": _env(0.51, source="gate_trades", confidence=0.9)},
        ),
    ]
    merged = merge_indicator_rows(rows, now=NOW)
    snap = build_indicators_snapshot(merged)

    for key in REQUIRED_CORE_INDICATORS:
        assert key in snap, f"snapshot must always carry {key} even when missing"
        assert snap[key]["value"] is None
