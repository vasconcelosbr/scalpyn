"""Unit tests for the robust indicator pipeline.

Tests are pure-Python and do not require a database connection — they cover
the envelope wrapper, validation rules and the confidence-weighted score
engine.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.robust_indicators.envelope import (  # noqa: E402
    CONFIDENCE_MAP,
    DataSource,
    IndicatorStatus,
    wrap_indicator,
)
from app.services.robust_indicators.score import calculate_score_with_confidence  # noqa: E402
from app.services.robust_indicators.validation import (  # noqa: E402
    validate_indicator_integrity,
)


NOW = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)


def _env(name, value, *, source=DataSource.GATE_CANDLES, age_seconds=0):
    return wrap_indicator(
        name=name,
        value=value,
        source=source,
        timestamp=NOW - timedelta(seconds=age_seconds),
        now=NOW,
    )


# ── envelope wrapper ──────────────────────────────────────────────────────────


def test_wrap_indicator_valid_fresh():
    env = _env("rsi", 25.0, source=DataSource.GATE_CANDLES, age_seconds=10)
    assert env.status is IndicatorStatus.VALID
    assert env.confidence == pytest.approx(CONFIDENCE_MAP[DataSource.GATE_CANDLES])
    assert env.is_usable


def test_wrap_indicator_no_data_when_value_none():
    env = _env("rsi", None)
    assert env.status is IndicatorStatus.NO_DATA
    assert env.confidence == 0.0
    assert not env.is_usable


def test_wrap_indicator_degraded_when_stale():
    env = _env("rsi", 30.0, source=DataSource.GATE_CANDLES, age_seconds=200)
    # 180 <= 200 < 300 → multiplier 0.5
    assert env.status is IndicatorStatus.DEGRADED
    assert env.confidence == pytest.approx(CONFIDENCE_MAP[DataSource.GATE_CANDLES] * 0.5)


def test_wrap_indicator_serialises():
    env = _env("rsi", 30.0)
    payload = env.to_dict()
    for key in (
        "name", "value", "status", "source", "timestamp",
        "confidence", "base_confidence", "staleness_seconds",
    ):
        assert key in payload
    assert payload["status"] == "VALID"


# ── validation rules ──────────────────────────────────────────────────────────


def _full_envelope_set():
    return {
        "rsi": _env("rsi", 25.0),
        "adx": _env("adx", 30.0),
        "macd": _env("macd", 0.5),
        "macd_signal_line": _env("macd_signal_line", 0.4),
        "macd_histogram": _env("macd_histogram", 0.1),
        "ema9": _env("ema9", 100.0),
        "ema50": _env("ema50", 99.0),
        "ema200": _env("ema200", 95.0),
        "taker_ratio": _env("taker_ratio", 0.55, source=DataSource.GATE_TRADES),
        "buy_pressure": _env("buy_pressure", 0.55, source=DataSource.GATE_TRADES),
        "volume_delta": _env("volume_delta", 12.3, source=DataSource.GATE_TRADES),
    }


def test_validation_passes_when_all_critical_present():
    result = validate_indicator_integrity(_full_envelope_set())
    assert result.passed is True
    assert not result.errors


def test_validation_fails_on_missing_critical():
    envs = _full_envelope_set()
    envs["rsi"] = _env("rsi", None)  # NO_DATA
    result = validate_indicator_integrity(envs)
    assert result.passed is False
    assert any("critical" in r.name for r in result.rules if not r.passed)


def test_validation_fails_on_volume_delta_from_candles():
    envs = _full_envelope_set()
    envs["volume_delta"] = _env("volume_delta", 10.0, source=DataSource.CANDLE_FALLBACK)
    result = validate_indicator_integrity(envs)
    assert result.passed is False
    assert any("volume_delta_bucket_exclusivity" in r.name for r in result.rules if not r.passed)


def test_validation_fails_on_missing_warmup():
    envs = _full_envelope_set()
    envs["ema200"] = _env("ema200", None)
    result = validate_indicator_integrity(envs)
    # Spec: sufficient_candles is CRITICAL — long warm-up NO_DATA fails.
    assert result.passed is False
    failing = [
        r for r in result.rules if r.severity == "CRITICAL" and not r.passed
    ]
    assert any(r.name == "sufficient_candles" for r in failing)
    assert any("ema200" in e for e in result.errors)


def test_validation_fails_on_missing_derived_input():
    """derived_dependencies is CRITICAL: macd_histogram with missing macd
    must fail validation (not merely warn)."""
    envs = _full_envelope_set()
    envs["macd"] = _env("macd", None)
    result = validate_indicator_integrity(envs)
    assert result.passed is False
    failing = [
        r for r in result.rules if r.severity == "CRITICAL" and not r.passed
    ]
    assert any(r.name == "derived_dependencies" for r in failing)


def test_validation_critical_includes_ema50():
    """ema50 was added to the critical set per spec — NO_DATA must reject."""
    envs = _full_envelope_set()
    envs["ema50"] = _env("ema50", None)
    result = validate_indicator_integrity(envs)
    assert result.passed is False
    assert any(
        r.name == "critical_no_data" and not r.passed
        for r in result.rules
    )


def test_validation_fails_on_bucket_overlap():
    """Bucket exclusivity: taker_buy + taker_sell exceeding total volume
    by more than 5% must trigger volume_delta_bucket_exclusivity."""
    envs = _full_envelope_set()
    envs["taker_buy_volume"] = _env(
        "taker_buy_volume", 60.0, source=DataSource.GATE_TRADES
    )
    envs["taker_sell_volume"] = _env(
        "taker_sell_volume", 60.0, source=DataSource.GATE_TRADES
    )
    envs["volume_24h_base"] = _env(
        "volume_24h_base", 100.0, source=DataSource.GATE_TICKER
    )
    result = validate_indicator_integrity(envs)
    assert result.passed is False
    assert any(
        r.name == "volume_delta_bucket_exclusivity" and not r.passed
        for r in result.rules
    )


def test_validation_fails_on_volume_delta_bucket_mismatch():
    """volume_delta must equal taker_buy - taker_sell within 5% tolerance."""
    envs = _full_envelope_set()
    envs["taker_buy_volume"] = _env(
        "taker_buy_volume", 50.0, source=DataSource.GATE_TRADES
    )
    envs["taker_sell_volume"] = _env(
        "taker_sell_volume", 30.0, source=DataSource.GATE_TRADES
    )
    # buy - sell = 20, but volume_delta says 100 → bucket mismatch.
    envs["volume_delta"] = _env(
        "volume_delta", 100.0, source=DataSource.GATE_TRADES
    )
    result = validate_indicator_integrity(envs)
    assert result.passed is False
    assert any(
        r.name == "volume_delta_bucket_exclusivity" and not r.passed
        for r in result.rules
    )


# ── score engine ──────────────────────────────────────────────────────────────


_SAMPLE_RULES = [
    {"id": "rsi_strong", "indicator": "rsi", "operator": "<=", "value": 30,
     "points": 30, "category": "momentum"},
    {"id": "adx_trend", "indicator": "adx", "operator": ">=", "value": 25,
     "points": 30, "category": "momentum"},
    {"id": "macd_pos", "indicator": "macd", "operator": ">", "value": 0,
     "points": 20, "category": "signal"},
]


def test_score_rejects_when_critical_missing():
    envs = _full_envelope_set()
    envs["adx"] = _env("adx", None)  # critical NO_DATA
    out = calculate_score_with_confidence(envs, _SAMPLE_RULES)
    assert out.rejected is True
    assert out.rejection_reason and out.rejection_reason.startswith("critical_gate")
    assert out.can_trade is False


def test_score_rejects_when_global_confidence_too_low():
    envs = _full_envelope_set()
    # Force every envelope to a deeply stale (>300s) state — confidence 0.10×base
    envs = {
        name: _env(env.name, env.value, source=env.source, age_seconds=400)
        for name, env in envs.items()
    }
    out = calculate_score_with_confidence(envs, _SAMPLE_RULES)
    assert out.rejected is True
    assert out.rejection_reason and out.rejection_reason.startswith("confidence_gate")


def test_score_uses_confidence_weighting():
    envs = _full_envelope_set()
    out_fresh = calculate_score_with_confidence(envs, _SAMPLE_RULES)
    assert out_fresh.rejected is False
    assert out_fresh.score > 0
    assert 0.0 <= out_fresh.score_confidence <= 1.0
    # Stale-but-usable envelopes should produce a strictly lower score.
    envs_stale = {
        name: _env(env.name, env.value, source=env.source, age_seconds=120)
        for name, env in envs.items()
    }
    out_stale = calculate_score_with_confidence(envs_stale, _SAMPLE_RULES)
    if not out_stale.rejected:
        assert out_stale.score <= out_fresh.score


def test_score_uses_direct_confidence_weighted_formula():
    """Spec: score = Σ(points × confidence) / Σ(points) × 100.

    With three matched rules totalling 80 points and a uniform
    confidence (= base GATE_CANDLES = 0.85 for fresh values), the
    expected score is exactly 0.85 × 100 = 85.
    """
    envs = _full_envelope_set()
    out = calculate_score_with_confidence(envs, _SAMPLE_RULES)
    assert out.rejected is False
    expected = CONFIDENCE_MAP[DataSource.GATE_CANDLES] * 100.0  # 85.0
    assert out.score == pytest.approx(expected, rel=0.02)
    # No category weights in direct mode — the result is identical
    # whether the caller passes weights or not.
    out_weighted = calculate_score_with_confidence(
        envs, _SAMPLE_RULES,
        weights={"liquidity": 100, "momentum": 0, "signal": 0,
                 "market_structure": 0},
    )
    assert out.score == out_weighted.score


def test_score_can_trade_threshold():
    envs = _full_envelope_set()
    # All three rules should match (RSI<=30, ADX>=25, MACD>0)
    out = calculate_score_with_confidence(
        envs, _SAMPLE_RULES, can_trade_threshold=10.0
    )
    assert out.can_trade is True
    out2 = calculate_score_with_confidence(
        envs, _SAMPLE_RULES, can_trade_threshold=99.0
    )
    assert out2.can_trade is False


# ── package surface ───────────────────────────────────────────────────────────


def test_phase4_removed_symbols_not_exported():
    """Phase 4 cleanup contract: the rollout/shadow/divergence symbols
    are gone from the public surface and must not be re-introduced.
    """
    from app.services import robust_indicators as pkg
    for removed in (
        "is_shadow_enabled",
        "run_shadow_scan",
        "is_legacy_rollback_active",
        "select_authoritative_score",
    ):
        assert not hasattr(pkg, removed), (
            f"robust_indicators.{removed} must remain removed after Phase 4"
        )

    from app.services.robust_indicators import metrics as metrics_mod
    for removed in ("divergence_bucket", "increment_divergence"):
        assert not hasattr(metrics_mod, removed), (
            f"robust_indicators.metrics.{removed} must remain removed after Phase 4"
        )
